"""Immutable plans, encrypted recovery snapshots, and UUID-only attempt markers."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4
from .storage import ROOT, canonical, confined
from .vendor_errors import normalize_vendor_error
from .operation_history import account_history
from .ozon_write import WriteOperation as Op, WriteError, money, blocked_reason, validate_request, contract_profile, contract_path, LEGACY_PROFILE, LEGACY_VALID_UNTIL, FUTURE_PROFILE


class MutationService:
    """fresh(op, scope, ids) -> {complete:bool, items:list[dict]}.

    Each item has id, product_id, current (money string, stock int or None),
    currency, member bool for actions; stocks also warehouse_id/reserved/present.
    All relevant policy/bound fields remain on the item and enter the snapshot.
    Writer factory receives current Credentials and returns send(op,body),close().
    """
    def __init__(self, accounts, writer_factory, fresh, *, path=None, root=ROOT, clock=time.monotonic, journal=None, restore_context=None, readback_sleep=asyncio.sleep):
        self.accounts, self.writer_factory, self.fresh = accounts, writer_factory, fresh
        self.readback_sleep = readback_sleep
        self.root, self.path, self.clock = root, Path(path) if path else root / ".cache/mutation-attempts", clock
        self._plans, self._history, self._rate = {}, {}, {}
        self._lock = asyncio.Lock()
        self._recovered = []
        self._reviewed = []
        self.journal = journal if journal is not None else account_history(accounts,'shop',root=root)
        try:
            for event in self.journal.entries():
                event = deepcopy(event)
                bundle = event.pop('_reconciliation', None)
                if bundle is not None:
                    document, digest = bundle['document'], bundle['hash']
                    serialized = canonical(document)
                    if document['id'] != event['id'] or document['account_id'] != event['account_id'] or not hmac.compare_digest(hashlib.sha256(serialized.encode()).hexdigest(), digest):
                        raise ValueError('Invalid reconciliation snapshot')
                    if not isinstance(document['rows'], list) or not document['rows'] or document['operation'] not in Op.__members__:
                        raise ValueError('Invalid reconciliation targets')
                    if document['api_path'] != contract_path(Op[document['operation']], document['api_profile']):
                        raise ValueError('Invalid reconciliation contract')
                    self._plans[event['id']] = {'document': serialized, 'hash': digest, 'created': self.clock(),
                        'attempted': True, 'recovered_plan': True, 'attempted_at': event.get('attempted_at'),
                        'stable_account_digest': bundle.get('stable_account_digest'),
                        'transport_error': deepcopy(event.get('transport_error'))}
                    if bundle.get('verified_source') is not None:
                        self._plans[event['id']]['verified_source'] = deepcopy(bundle['verified_source'])
                    if bundle.get('rollback_attempt_id'):
                        self._plans[event['id']]['rollback_attempt_id'] = bundle['rollback_attempt_id']
                    if restore_context is not None:
                        restore_context(deepcopy(document))
                self._history[event['id']] = event | {'recovered': True}
            # The inverse's encrypted pre-send document is authoritative even
            # if a crash occurred before the original's in-memory link updated.
            for attempt_id, stored in self._plans.items():
                original_id=json.loads(stored['document']).get('rollback_of')
                if original_id in self._plans:
                    self._plans[original_id]['rollback_attempt_id']=attempt_id
        except Exception:
            raise WriteError('HISTORY_STORAGE', 'Не удалось прочитать зашифрованную историю; отправка запрещена', 409) from None
        if accounts.mode != "MOCK":
            directory = confined(self.path, root)
            if directory.exists():
                for marker in directory.iterdir():
                    confined(marker, root)
                    if marker.suffix != ".json":
                        continue
                    try:
                        plan_id = str(UUID(marker.stem))
                        if marker.stat().st_size > 512:
                            raise ValueError
                        value = json.loads(marker.read_text(encoding="utf-8"))
                        if set(value) != {"id", "status"} or value["id"] != plan_id or value["status"] not in {"VERIFIED_SUCCESS", "REJECTED", "PARTIAL", "DISCREPANCY", "UNKNOWN_RESULT", "REVIEWED_DISCREPANCY"}:
                            raise ValueError
                        if plan_id in self._history:
                            continue
                        if value['status']=='REVIEWED_DISCREPANCY':
                            self._reviewed.append({'id':plan_id,'status':'REVIEWED_DISCREPANCY','recovered':True,
                                'message':'Пользователь уточнил состав потерянного плана; текущие значения сверены. Полный исход и откат исходного плана не подтверждены'})
                        elif value["status"] not in {"VERIFIED_SUCCESS", "REJECTED"}:
                            self._recovered.append({"id": plan_id, "status": "UNKNOWN_RESULT", "recovered": True,
                                "message": "После перезапуска детали плана не сохранены. Сверьте результат в Seller; автоматический повтор запрещён"})
                    except (ValueError, OSError, TypeError):
                        raise WriteError("ATTEMPT_STORAGE", "Журнал попыток повреждён; отправка запрещена", 409) from None

    def _binding(self):
        return (self.accounts.active_id, self.accounts.revision)

    def _account_digest(self):
        credentials = self.accounts.credentials()
        client_id = getattr(credentials, 'client_id', None)
        return hashlib.sha256(str(client_id).encode()).hexdigest() if client_id else None

    def _save_history(self, event):
        try:
            encrypted_event = deepcopy(event)
            stored = self._plans.get(event['id'])
            if stored is not None:
                stored.setdefault('stable_account_digest', self._account_digest())
                encrypted_event['_reconciliation'] = {'document': json.loads(stored['document']), 'hash': stored['hash'],
                    'stable_account_digest': stored['stable_account_digest'],
                    'verified_source': deepcopy(stored.get('verified_source')),
                    'rollback_attempt_id': stored.get('rollback_attempt_id')}
            self.journal.upsert(encrypted_event)
        except Exception:
            raise WriteError('HISTORY_STORAGE', 'Не удалось сохранить зашифрованную историю; проверьте исход операции перед повтором', 409) from None
        self._history[event['id']] = deepcopy(event)

    def _ensure_active(self):
        if self.accounts.active_id is None or (self.accounts.mode != "MOCK" and self.accounts.credentials() is None):
            raise WriteError("NOT_CONFIGURED", "Выберите настроенный кабинет", 409)

    def _marker(self, plan_id, status):
        if self.accounts.mode == "MOCK":
            return
        directory = confined(self.path, self.root)
        directory.mkdir(parents=True, exist_ok=True)
        destination = confined(directory / (str(UUID(plan_id)) + ".json"), self.root)
        temporary = confined(directory / (uuid4().hex + ".tmp"), self.root)
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump({"id": plan_id, "status": status}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            confined(destination, self.root)
            os.replace(temporary, destination)
        except OSError:
            raise WriteError("ATTEMPT_STORAGE", "Не удалось сохранить попытку; отправка запрещена", 409) from None

    def history(self):
        entries = deepcopy([*self._recovered, *self._reviewed, *reversed(list(self._history.values()))])
        for item in entries:
            reason = self._rollback_reason(item['id'])
            item.update(can_rollback=reason is None, rollback_blocked_reason=reason)
            item['can_reconcile'] = bool(self._plans.get(item['id'], {}).get('attempted'))
        return entries

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _public_row(row):
        source = row['source']
        return {key: source.get(key) for key in ('name', 'sku', 'offer_id', 'warehouse_id', 'currency')} | {
            'id': row['id'], 'name': source.get('name', ''), 'offer_id': source.get('offer_id', ''),
            'before': row['before'], 'after': row['after']}

    def _outcome(self, document, status, rows, message):
        stored = self._plans[document['id']]
        return {'id': document['id'], 'operation': Op[document['operation']].value[0],
            'account_id': document['account_id'], 'mode': self.accounts.mode,
            'created_at': document['created_at'], 'attempted_at': stored.get('attempted_at'),
            'rollback_of': document.get('rollback_of'), 'status': status, 'rows': rows,
            'effects': deepcopy(document.get('effects', [])),
            'plan_rows': [self._public_row(row) for row in document['rows']], 'message': message}

    def _inverse(self, document):
        op = Op[document['operation']]
        if op in (Op.PRICE, Op.STOCK):
            inverse = op
        elif op in (Op.ACTION_REMOVE, Op.AUTO_REMOVE):
            inverse = Op.ACTION_UPDATE if op is Op.ACTION_REMOVE else Op.AUTO_UPDATE
        else:
            members = {row['source'].get('member') for row in document['rows']}
            if members == {True}:
                inverse = op
            elif members == {False}:
                inverse = Op.ACTION_REMOVE if op is Op.ACTION_UPDATE else Op.AUTO_REMOVE
            else:
                raise WriteError('ROLLBACK_MIXED', 'Полный откат смешанного добавления и изменения цен требует разных методов; единый план недоступен', 409)
        changes = [{'id': row['id'], 'after': None if inverse in (Op.ACTION_REMOVE, Op.AUTO_REMOVE) else row['before']}
                   for row in document['rows']]
        return inverse, changes

    def _rollback_reason(self, operation_id):
        original = self._history.get(operation_id)
        stored = self._plans.get(operation_id)
        if original is None or stored is None:
            return 'Исходный снимок отсутствует в памяти; после перезапуска полный откат недоступен'
        document = json.loads(stored['document'])
        recovered_return=stored.get('recovered_plan') and document['operation']==Op.AUTO_REMOVE.name and document['api_profile']==FUTURE_PROFILE
        if stored.get('recovered_plan') and not recovered_return:
            return 'История восстановлена для сверки; автоматический откат после перезапуска пока недоступен'
        if original['status'] != 'VERIFIED_SUCCESS' or any(row['status'] != 'VERIFIED_SUCCESS' for row in original['rows']):
            return 'Полный откат доступен только после успешной сверки каждой строки; сначала выясните частичный или неизвестный исход'
        if any(effect.get('card_price') for effect in document.get('effects', [])):
            return 'Операция изменила цену карточки и участие в акции; полный атомарный откат обоих значений пока недоступен'
        same_binding=(document['account_id'], document['revision']) == self._binding()
        if recovered_return:
            same_binding=document['account_id']==self.accounts.active_id and stored.get('stable_account_digest')==self._account_digest()
        if not same_binding:
            return 'Исходный магазин или версия подключения изменились; откат заблокирован'
        if stored.get('rollback_attempt_id'):
            return 'Откат этой операции уже отправлялся; повтор запрещён. Откройте его лог'
        if self._unresolved():
            return 'Есть неизвестный исход отправки; сначала нужна сверка'
        verified = stored.get('verified_source')
        if verified is None or any(row['id'] not in verified for row in document['rows']):
            return 'Нет полного снимка состояния после операции; восстановление всех строк не подтверждено'
        try:
            inverse, changes = self._inverse(document)
            if self.accounts.mode != 'MOCK' and blocked_reason(inverse):
                return blocked_reason(inverse)
            for change in changes:
                self._validate(inverse, verified[change['id']], change['after'], live=self.accounts.mode != 'MOCK')
        except WriteError as exc:
            return exc.message
        return None

    async def rollback_preview(self, operation_id):
        # No client targets or values: all rows and scope belong to the original
        # immutable server snapshot. Preparing this plan never sends a mutation.
        async with self._lock:
            reason = self._rollback_reason(operation_id)
            if reason:
                raise WriteError('ROLLBACK_BLOCKED', reason, 409)
            stored = self._plans[operation_id]
            document = json.loads(stored['document'])
            inverse, changes = self._inverse(document)
            expected = {'complete': True, 'items': list(deepcopy(stored['verified_source']).values())}
            return await self.preview(inverse, document['scope'], changes,
                expected_source=expected, rollback_of=operation_id)

    def invalidate(self):
        # Only unattempted plans are discarded. Attempted plans remain for read reconciliation.
        self._plans = {key: value for key, value in self._plans.items() if value.get("attempted")}

    def has_unresolved(self):
        return self._unresolved()

    def _unresolved(self):
        return bool(self._recovered) or any(any(row["status"] == "UNKNOWN_RESULT" for row in item.get("rows", []))
                                         for item in self._history.values())

    @staticmethod
    def _source(source, ids):
        if not isinstance(source, dict) or source.get("complete") is not True or not isinstance(source.get("items"), list):
            raise WriteError("INCOMPLETE", "Свежие данные неполны; отправка запрещена", 409)
        items = {}
        for row in source["items"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in items:
                raise WriteError("SCHEMA", "Небезопасная структура свежего ответа", 409)
            if row["id"] in ids:
                items[row["id"]] = deepcopy(row)
        return items

    @staticmethod
    def _body(operation, scope, rows, profile=None):
        profile = profile or contract_profile(operation, mock=True)
        body = {}
        if operation not in (Op.PRICE, Op.STOCK):
            body["action_id"] = int(scope["action_id"])
        if operation in (Op.AUTO_UPDATE, Op.AUTO_REMOVE):
            body["auto_add_date"] = scope["auto_add_date"]
        if operation is Op.PRICE:
            body["prices"] = [{"product_id": int(row["source"]["product_id"]), "price": row["after"], "currency_code": row["source"]["currency"]} for row in rows]
        elif operation is Op.STOCK:
            body["stocks"] = [{**({"offer_id": row["source"]["offer_id"]} if row["source"].get("offer_id") else {"product_id": int(row["source"]["product_id"])}),
                "warehouse_id": int(row["source"]["warehouse_id"]), "stock": row["after"]} for row in rows]
        elif operation in (Op.ACTION_REMOVE, Op.AUTO_REMOVE):
            selector = int if profile == LEGACY_PROFILE and operation is Op.ACTION_REMOVE else str
            body["product_ids"] = [selector(row["source"]["product_id"]) for row in rows]
        elif profile == LEGACY_PROFILE:
            key = "to_update" if operation is Op.AUTO_UPDATE else "products"
            body[key] = [{"product_id": int(row["source"]["product_id"]), "action_price": row["after"],
                **({"currency": row["source"]["currency"]} if operation is Op.AUTO_UPDATE else {})} for row in rows]
        else:
            field = "id" if operation is Op.AUTO_UPDATE else "product_id"
            body["products"] = [{field: int(row["source"]["product_id"]), "action_price": {"amount": row["after"], "currency": row["source"]["currency"]}} for row in rows]
        validate_request(operation, body, profile=profile)
        return body

    @staticmethod
    def _validate(operation, row, after, *, live=False):
        from decimal import Decimal
        if operation in (Op.ACTION_UPDATE, Op.ACTION_REMOVE, Op.AUTO_UPDATE, Op.AUTO_REMOVE) and row.get('freeze_date'):
            if operation in (Op.ACTION_REMOVE, Op.AUTO_REMOVE) or row.get('member') is not True:
                raise WriteError('ACTION_FROZEN', 'Ozon запретил изменение списка товаров этой акции; доступно только снижение цены участника', 409)
            if row.get('current') is None or Decimal(money(after)) > Decimal(money(row['current'])):
                raise WriteError('ACTION_FROZEN', 'Ozon запретил повышение цены в этой акции; доступно снижение цены участника', 409)
        if operation is Op.STOCK:
            if type(after) is not int or not 0 <= after <= 2**63 - 1 or any(type(row.get(field)) is not int or not 0 <= row[field] <= 2**63 - 1 for field in ("current", "reserved", "present")):
                raise WriteError("VALIDATION", "Остаток/резерв не подтверждён или новое количество некорректно", 422)
            return after
        if operation in (Op.ACTION_REMOVE, Op.AUTO_REMOVE):
            if after is not None or row.get("member") is not True:
                raise WriteError("VALIDATION", "Удаление допускается только для свежего участника акции", 422)
            return None
        if row.get("currency") != "RUB":
            raise WriteError("CURRENCY_UNKNOWN", "Валюта денежного поля не подтверждена; запись запрещена", 409)
        legacy = contract_profile(operation) == LEGACY_PROFILE
        if live and not legacy and operation is Op.AUTO_UPDATE:
            currencies = row.get('money_currencies', {})
            if (currencies.get('action_price') != 'RUB' or currencies.get('price') != 'RUB'
                or {'price', 'action_price'}.intersection(row.get('money_assumed_fields', []))):
                raise WriteError('CURRENCY_UNKNOWN', 'Возврат требует подтверждённую RUB цены товара и цены автодобавления Ozon', 409)
            if (row.get('member') is not False or row.get('role') not in (None, 'candidate')
                or row.get('action_price') is None):
                raise WriteError('AUTO_RETURN_ONLY', 'Можно только вернуть кандидата по свежей цене автодобавления Ozon', 409)
            try:
                proposed = Decimal(money(row['action_price']))
            except WriteError as exc:
                raise WriteError('AUTO_RETURN_ONLY', 'Свежая цена автодобавления Ozon должна быть положительной денежной суммой', 409) from exc
            if proposed <= 0 or Decimal(money(after)) != proposed:
                raise WriteError('AUTO_RETURN_ONLY', 'При возврате используется точная цена автодобавления Ozon без пересчёта скидки', 409)
        if row.get("currency_assumed"):
            fields = row.get("money_assumed_fields", [])
            used = {"price"} if operation is Op.PRICE or (legacy and operation in (Op.ACTION_UPDATE, Op.AUTO_UPDATE)) else {"price", "action_price", "price_min_elastic", "price_max_elastic", "max_action_price"}
            if not fields or used.intersection(fields):
                raise WriteError("CURRENCY_ASSUMED", "Валюта записи задана только допущением; требуется явная валюта свежего ответа", 409)
        after = money(after)
        if row.get("current") is not None:
            money(row["current"])
        elif operation is Op.PRICE or row.get("member") is True:
            raise WriteError("VALIDATION", "Текущая цена отсутствует", 409)
        if operation in (Op.ACTION_UPDATE, Op.AUTO_UPDATE):
            if type(row.get("member")) is not bool:
                raise WriteError("VALIDATION", "Участие товара в акции не подтверждено", 409)
            # Only explicit bounds. Auto-add thresholds are independent policies.
            # Names denote boost levels: min-boost price is the upper money bound;
            # max-boost price is the lower money bound (confirmed domain mapping).
            for key, predicate in (("price_min_elastic", lambda a,b: a > b), ("price_max_elastic", lambda a,b: a < b), ("max_action_price", lambda a,b: a > b), ("max_price", lambda a,b: a > b)):
                if row.get(key) is not None and predicate(Decimal(after), Decimal(money(row[key]))):
                    raise WriteError("VALIDATION", "Цена не соответствует подтверждённым ограничениям акции", 422)
            if row.get("elastic") is True and (row.get("price_min_elastic") is None or row.get("price_max_elastic") is None):
                raise WriteError("VALIDATION", "Границы эластичной акции не подтверждены", 409)
        return after

    async def preview(self, operation, scope, changes, *, expected_source=None, rollback_of=None):
        op = operation if type(operation) is Op else Op.parse(operation)
        self._ensure_active()
        if self._unresolved():
            raise WriteError("UNRESOLVED", "Есть неизвестный исход прежней отправки. Сначала сверка; повтор запрещён", 409)
        if not isinstance(scope, dict) or set(scope) - {"action_id", "auto_add_date"}:
            raise WriteError("VALIDATION", "Некорректная область операции", 422)
        if op in (Op.PRICE, Op.STOCK) and scope:
            raise WriteError("VALIDATION", "Для цен каталога и остатков область акции не допускается", 422)
        if not isinstance(changes, list) or not 1 <= len(changes) <= op.value[2] or any(not isinstance(row, dict) or set(row) != {"id", "after"} or not isinstance(row["id"], str) for row in changes):
            raise WriteError("VALIDATION", f"Один план допускает до {op.value[2]} строк выбранного метода", 422)
        ids = [row["id"] for row in changes]
        if len(set(ids)) != len(ids):
            raise WriteError("VALIDATION", "Повторяющиеся строки запрещены", 422)
        # Domain calculation may precede this fresh read. Bind its entire original
        # selected source to the new snapshot, so an old delta cannot turn an
        # intended stock increase into a decrease after a concurrent change.
        expected = self._source(deepcopy(expected_source), ids) if expected_source is not None else None
        binding = self._binding()
        source = self._source(await self.fresh(op, deepcopy(scope), ids), ids)
        if binding != self._binding():
            raise WriteError("ACCOUNT_CHANGED", "Кабинет изменился во время чтения", 409)
        if expected is not None and source != expected:
            raise WriteError("FRESH_CHANGED", "Исходные данные изменились после расчёта; выполните новый расчёт и подтверждение", 409)
        rows = []
        profile = contract_profile(op, mock=self.accounts.mode == "MOCK")
        for change in changes:
            if change["id"] not in source:
                raise WriteError("MISSING_TARGET", "Выбранная строка отсутствует в свежих данных", 409)
            row = source[change["id"]]
            if self.accounts.mode != "MOCK" and profile != LEGACY_PROFILE and op in (Op.ACTION_UPDATE, Op.ACTION_REMOVE):
                voucher = row.get('is_voucher_action')
                if type(voucher) is not bool:
                    raise WriteError('ACTION_TYPE_UNKNOWN', 'Тип акции не подтверждён свежим ответом Ozon', 409)
                if op is Op.ACTION_UPDATE and voucher is False:
                    if row.get('elastic') is not True or row.get('min_price') is None or row.get('max_price') is None:
                        raise WriteError('UNKNOWN_BOUNDS', 'Подтверждённый диапазон бустинга отсутствует; изменение цены может удалить участие', 409)
                    if row.get('card_currency') != 'RUB' or row.get('card_price') is None:
                        raise WriteError('CARD_PRICE_UNKNOWN', 'Свежая цена карточки и её валюта не подтверждены', 409)
                    money(row['card_price'])
            rows.append({"id": change["id"], "source": row, "before": row.get("current"),
                         "after": self._validate(op, row, change["after"], live=self.accounts.mode != 'MOCK')})
        try:
            profile = contract_profile(op, mock=self.accounts.mode == "MOCK")
            body = self._body(op, scope, rows, profile)
        except (KeyError, TypeError, ValueError):
            raise WriteError("VALIDATION", "Идентификаторы или область операции не подтверждены", 422) from None
        plan_id = str(uuid4())
        effects = []
        for row in rows:
            item = row['source']
            card = None
            if profile != LEGACY_PROFILE and op is Op.ACTION_UPDATE and item.get('is_voucher_action') is False:
                card = {'before': item['card_price'], 'after': row['after'], 'currency': item['card_currency'],
                        'application': 'possible', 'accepted_values': list(dict.fromkeys([item['card_price'], row['after']]))}
            effects.append({'id': row['id'], 'card_price': card,
                            'currency_binding_fields': deepcopy(item.get('currency_binding_fields', []))})
            if op is Op.ACTION_REMOVE and item.get('is_voucher_action') is False and item.get('card_price') is not None and item.get('card_currency'):
                money(item['card_price'])
                effects[-1]['card_unchanged'] = {'before': item['card_price'], 'currency': item['card_currency']}
            if op is Op.AUTO_UPDATE and profile==FUTURE_PROFILE:
                effects[-1]['scheduled_card_price']={'after':row['after'],'currency':item['currency'],
                    'at':scope['auto_add_date'],'verification':'schedule_only'}
        document = {"id": plan_id, "operation": op.name, "scope": deepcopy(scope), "account_id": binding[0],
                    "effects": effects,
                    "revision": binding[1], "rows": rows, "body": body, "api_profile": profile,
                    "api_path": contract_path(op, profile), 'created_at': self._timestamp(),
                    'rollback_of': rollback_of}
        serialized = canonical(document)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        blocked = blocked_reason(op) if self.accounts.mode != "MOCK" else None
        self._plans[plan_id] = {"document": serialized, "hash": digest, "created": self.clock(), "attempted": False}
        # Bound memory lifetime and capacity; real products never enter durable storage.
        for key in list(self._plans):
            if not self._plans[key]["attempted"] and (self.clock() - self._plans[key]["created"] > 120 or (len(self._plans) > 100 and key != plan_id)):
                del self._plans[key]
        return {"id": plan_id, "hash": digest, "operation": op.value[0], "mode": self.accounts.mode,
                "api_profile": profile, "api_path": contract_path(op, profile),
                "contract_valid_until": LEGACY_VALID_UNTIL.isoformat() if profile == LEGACY_PROFILE else None,
                "account_id": binding[0], "valid": blocked is None, "blocked_reason": blocked, "expires_in": 120,
                'rollback_of': rollback_of, "effects": deepcopy(effects), "rows": [self._public_row(row) for row in rows]}

    def _plan(self, plan_id, plan_hash):
        stored = self._plans.get(plan_id)
        if stored is None or not isinstance(plan_hash, str) or not hmac.compare_digest(stored["hash"], plan_hash):
            raise WriteError("PLAN_MISMATCH", "План отсутствует или подтверждение не совпадает", 409)
        return stored, json.loads(stored["document"])

    @staticmethod
    def _response_id(value):
        from decimal import Decimal
        from .ozon_read import identifier
        if isinstance(value, Decimal):
            if not value.is_finite() or value != value.to_integral_value() or not 0 < value <= 2**53-1:
                return None
            value = int(value)
        return str(value) if identifier(value) else None

    @staticmethod
    def _accepted(op, response, row):
        product = str(row["source"]["product_id"])
        if op in (Op.PRICE, Op.STOCK):
            results = response.get("result") if isinstance(response, dict) else None
            if not isinstance(results, list):
                return None
            matching = [item for item in results if isinstance(item, dict) and str(item.get("product_id")) == product and (op is not Op.STOCK or str(item.get("warehouse_id")) == str(row["source"]["warehouse_id"]))]
            if len(matching) != 1:
                return None
            item = matching[0]
            if item.get("updated") is False or (isinstance(item.get("errors"), list) and item["errors"]):
                return False
            return True if item.get("updated") is True and item.get("errors") == [] else None
        payload = response.get("result", response) if isinstance(response, dict) else {}
        if not isinstance(payload, dict):
            return None
        rejected = payload.get("rejected", [])
        if op in (Op.ACTION_UPDATE, Op.AUTO_UPDATE):
            for field in ('deactivated_product_ids', 'deactivated_ids'):
                values = payload.get(field, [])
                if isinstance(values, list) and any(MutationService._response_id(item) == product for item in values):
                    return False
        if isinstance(rejected, list) and any(isinstance(item, dict) and MutationService._response_id(item.get("product_id", item.get("id"))) == product for item in rejected):
            return False
        if op in (Op.ACTION_REMOVE, Op.AUTO_REMOVE):
            successes = payload.get("deactivated_product_ids", payload.get("deactivated_ids", payload.get("product_ids")))
        else:
            successes = payload.get("updated_ids", payload.get("active_product_ids", payload.get("product_ids")))
        if isinstance(successes, list) and [MutationService._response_id(item) for item in successes].count(product) == 1:
            return True
        return None

    def _response_errors(self, op, response, row):
        # Only errors belonging to this target; arbitrary response/header/body
        # fields never enter the public log. Strip runtime credentials as well.
        payload = response.get('result', response) if isinstance(response, dict) else None
        candidates = payload if isinstance(payload, list) else payload.get('rejected', []) if isinstance(payload, dict) else []
        if isinstance(payload, dict) and isinstance(candidates, list):
            candidates = list(candidates)
            warnings = payload.get('warnings', [])
            if isinstance(warnings, list):
                candidates.extend(warnings)
            for field in ('below_min_price','extremely_low_price','failed_price'):
                values = payload.get(field, [])
                if isinstance(values, list):
                    # These diagnostics contain a product/price pair, not a
                    # vendor reason. Retain that pair instead of inventing one.
                    candidates.extend({'product_id': item['key'], 'code': field.upper(),
                        'reason': json.dumps({key: item[key] for key in ('key', 'value') if key in item},
                            ensure_ascii=False, default=str), 'price_diagnostic': True}
                        for item in values if isinstance(item, dict) and 'key' in item)
        errors = []
        credentials = self.accounts.credentials()
        secrets = [value for value in (getattr(credentials, 'api_key', None), getattr(credentials, 'client_id', None)) if value]
        for candidate in candidates:
            if not isinstance(candidate, dict) or self._response_id(candidate.get('product_id', candidate.get('id'))) != str(row['source']['product_id']):
                continue
            if op is Op.STOCK and str(candidate.get('warehouse_id')) != str(row['source']['warehouse_id']):
                continue
            entries = candidate.get('errors', [candidate] if 'code' in candidate or 'message' in candidate or 'reason' in candidate else [])
            if not isinstance(entries, list):
                entries = []
            for error in entries[:100]:
                if isinstance(error, (dict, str)):
                    normalized = normalize_vendor_error(error, secrets=secrets)
                    if candidate.get('price_diagnostic'):
                        normalized['severity'] = 'warning'
                    errors.append(normalized)
            if candidate.get('reason') is not None and 'errors' in candidate:
                errors.append(normalize_vendor_error(candidate, secrets=secrets))
        return errors

    async def _reconcile(self, document, response):
        op, ids = Op[document["operation"]], [row["id"] for row in document["rows"]]
        try:
            current = self._source(await self.fresh(op, document["scope"], ids), ids)
        except Exception:
            current = None
        results = []
        stored = self._plans[document['id']]
        previous = {row['id']: row for row in self._history.get(document['id'], {}).get('rows', [])}
        for row in document["rows"]:
            accepted = self._accepted(op, response, row) if response else previous.get(row['id'], {}).get('accepted')
            old_errors = previous.get(row['id'], {}).get('errors', [])
            legacy_price_diagnostics=False
            if response is None and accepted is False and old_errors and all(
                error.get('code') in ('BELOW_MIN_PRICE', 'EXTREMELY_LOW_PRICE', 'FAILED_PRICE')
                and error.get('reason') == 'Ozon сообщил проблему проверки цены; проверьте построчную сверку'
                for error in old_errors if isinstance(error, dict)) and all(isinstance(error, dict) for error in old_errors):
                # Older releases fabricated rejection from price diagnostics.
                # Recover no vendor acceptance claim: only a fresh, exact
                # snapshot can prove the requested state, never a resend.
                accepted = None
                legacy_price_diagnostics=True
            status = "UNKNOWN_RESULT"
            effect = next((item for item in document.get('effects', []) if item['id'] == row['id']), {})
            card_effect = effect.get('card_price') or effect.get('card_unchanged')
            if current is not None:
                actual = current.get(row["id"])
                if op in (Op.ACTION_REMOVE, Op.AUTO_REMOVE):
                    matches = actual is None or actual.get("member") is False
                    effect = next((item for item in document.get('effects', []) if item['id'] == row['id']), {})
                    card = effect.get('card_unchanged')
                    if card:
                        matches = matches and actual is not None and actual.get('card_price') == card['before'] and actual.get('card_currency') == card['currency']
                else:
                    matches = actual is not None and actual.get("current") == row["after"] and (op in (Op.PRICE, Op.STOCK) or actual.get("member") is True)
                    if op is not Op.STOCK:
                        matches = matches and actual.get("currency") == row["source"].get("currency") if actual else False
                    effect = next((item for item in document.get('effects', []) if item['id'] == row['id']), {})
                    card = effect.get('card_price')
                    if card:
                        expected_cards = card.get('accepted_values', [card['after']])
                        matches = matches and actual is not None and actual.get('card_price') in expected_cards and actual.get('card_currency') == card['currency']
                if matches:
                    status = "DISCREPANCY" if accepted is False else "VERIFIED_SUCCESS"
                elif accepted is False:
                    status = "REJECTED"
                elif accepted is True:
                    status = "DISCREPANCY"
            actual = current.get(row['id']) if current is not None else None
            errors = self._response_errors(op, response, row) if response is not None else previous.get(row['id'], {}).get('errors', [])
            if legacy_price_diagnostics:
                errors=[normalize_vendor_error(error) | {'severity':'warning','original_unavailable':True} for error in errors]
            message = {'VERIFIED_SUCCESS': 'Новое состояние подтверждено повторным чтением',
                'REJECTED': 'Ozon отклонил строку; новое состояние не подтверждено',
                'DISCREPANCY': 'Ответ отправки и фактическое состояние расходятся',
                'UNKNOWN_RESULT': 'Исход не установлен; повторная отправка запрещена'}[status]
            results.append(self._public_row(row) | {'status': status, 'message': message,
                'actual': actual.get('current') if actual else None,
                'actual_member': actual.get('member') if actual else None,
                'actual_currency': actual.get('currency') if actual else None,
                'card_actual': actual.get('card_price') if actual and card_effect else None,
                'card_changed': (actual.get('card_price') != card_effect['before']) if actual and card_effect and actual.get('card_price') is not None and actual.get('card_currency') == card_effect['currency'] else None,
                'readback_complete': current is not None, 'accepted': accepted,
                'errors': deepcopy(errors)})
        statuses = {row["status"] for row in results}
        status = next(iter(statuses)) if len(statuses) == 1 else "PARTIAL"
        if status == 'VERIFIED_SUCCESS':
            # Freeze the first verified post-operation snapshot. A later manual
            # reconciliation must not adopt somebody else's reserve/bound change
            # as a new baseline and make an unsafe inverse eligible.
            stored.setdefault('verified_source', deepcopy(current))
        outcome = self._outcome(document, status, results,
            'Отправка выполнена один раз; каждый результат проверен чтением. Неизвестные исходы не повторяются')
        outcome['transport_error'] = deepcopy(stored.get('transport_error'))
        self._save_history(outcome)
        self._marker(document["id"], status)
        return deepcopy(outcome)

    async def execute(self, plan_id, plan_hash, *, confirmed=False):
        if confirmed is not True:
            raise WriteError("CONFIRMATION_REQUIRED", "Подтвердите конкретный план перед отправкой", 422)
        async with self._lock:
            stored, document = self._plan(plan_id, plan_hash)
            if stored["attempted"]:
                raise WriteError("ALREADY_ATTEMPTED", "Этот план уже отправлялся; повтор запрещён", 409)
            if self._unresolved():
                raise WriteError("UNRESOLVED", "Сначала сверка неизвестного исхода", 409)
            if (document["account_id"], document["revision"]) != self._binding():
                raise WriteError("ACCOUNT_CHANGED", "Кабинет изменился; подготовьте новый план", 409)
            if self.clock() - stored["created"] > 120:
                raise WriteError("STALE_PLAN", "План устарел; подготовьте и подтвердите новый", 409)
            if document.get('rollback_of') and self._rollback_reason(document['rollback_of']):
                raise WriteError('ROLLBACK_BLOCKED', self._rollback_reason(document['rollback_of']), 409)
            op = Op[document["operation"]]
            if self.accounts.mode != "MOCK" and blocked_reason(op, document['api_profile']):
                raise WriteError("CONTRACT_BLOCKED", blocked_reason(op, document['api_profile']), 409)
            if document["api_profile"] != contract_profile(op, mock=self.accounts.mode == "MOCK") or document["api_path"] != contract_path(op, document["api_profile"]):
                raise WriteError("CONTRACT_BLOCKED", "Профиль API изменился; подготовьте новый план и подтверждение", 409)
            if self.accounts.busy:
                raise WriteError("BUSY", "Дождитесь текущей операции магазина", 409)
            self.accounts.busy = True
            writer = None
            try:
                ids = [row["id"] for row in document["rows"]]
                fresh = self._source(await self.fresh(op, document["scope"], ids), ids)
                if self.accounts.mode != 'MOCK' and blocked_reason(op,document['api_profile']):
                    raise WriteError('CONTRACT_BLOCKED',blocked_reason(op,document['api_profile']),409)
                if any(fresh.get(row["id"]) != row["source"] for row in document["rows"]):
                    raise WriteError("FRESH_CHANGED", "Свежие данные изменились; нужен новый план и подтверждение", 409)
                for row in document['rows']:
                    self._validate(op, fresh[row['id']], row['after'], live=self.accounts.mode != 'MOCK')
                if (document["account_id"], document["revision"]) != self._binding():
                    raise WriteError("ACCOUNT_CHANGED", "Кабинет изменился во время проверки", 409)
                if self.accounts.mode != "MOCK" and blocked_reason(op, document['api_profile']):
                    raise WriteError("CONTRACT_BLOCKED", blocked_reason(op, document['api_profile']), 409)
                if self.clock() - stored["created"] > 120:
                    raise WriteError("STALE_PLAN", "План устарел во время свежей проверки; подтвердите новый план", 409)
                now = self.clock()
                for row in document["rows"]:
                    key = (document["account_id"], op.name, row["source"]["product_id"], row["source"].get("warehouse_id"))
                    attempts = [value for value in self._rate.get(key, []) if now - value < 3600]
                    if op is Op.PRICE and len(attempts) >= 10 or op is Op.STOCK and attempts and now - attempts[-1] < 30:
                        raise WriteError("LOCAL_RATE_LIMIT", "Достигнут лимит обновления товара; отправка не выполнена", 429)
                stock_rate_key = (document['account_id'], 'STOCK_REQUEST')
                stock_attempts = [value for value in self._rate.get(stock_rate_key, []) if now - value < 60]
                if op is Op.STOCK and len(stock_attempts) >= 80:
                    raise WriteError('LOCAL_RATE_LIMIT', 'Достигнут лимит 80 запросов остатков в минуту; отправка не выполнена', 429)
                writer = self.writer_factory(self.accounts.credentials())
                stored['attempted_at'] = self._timestamp()
                pending = self._outcome(document, 'UNKNOWN_RESULT',
                    [self._public_row(row) | {'status': 'UNKNOWN_RESULT', 'errors': [], 'message': 'Ожидается ответ и сверка'} for row in document['rows']],
                    'Попытка сохранена перед отправкой; ожидается сверка')
                self._save_history(pending)  # Encrypted fsync must succeed BEFORE network.
                self._marker(plan_id, "UNKNOWN_RESULT")  # fsync before network; errors forbid send.
                stored["attempted"] = True
                if op is Op.STOCK:
                    self._rate[stock_rate_key] = stock_attempts + [now]
                if document.get('rollback_of'):
                    self._plans[document['rollback_of']]['rollback_attempt_id'] = plan_id
                for row in document["rows"]:
                    self._rate.setdefault((document["account_id"], op.name, row["source"]["product_id"], row["source"].get("warehouse_id")), []).append(now)
                try:
                    response = await writer.send(op, deepcopy(document["body"]))
                except WriteError as exc:
                    if exc.code == "CONTRACT_BLOCKED":
                        # The closed transport raises this only BEFORE network.
                        self._save_history(self._outcome(document, 'REJECTED',
                            [self._public_row(row) | {'status': 'REJECTED', 'errors': [{'code': 'CONTRACT_BLOCKED', 'message': exc.message}], 'message': 'Запрос не отправлен'} for row in document['rows']],
                            'Отправка не выполнялась: разрешённый период контракта завершился'))
                        self._marker(plan_id, "REJECTED")
                        raise
                    stored['transport_error'] = {'code': exc.code, 'message': 'Запрос завершился ошибкой транспорта; итог установлен только последующим чтением'}
                    if getattr(exc, 'vendor_error', None):
                        stored['transport_error'].update(deepcopy(exc.vendor_error))
                    response = None
                except Exception:
                    stored['transport_error'] = {'code': 'TRANSPORT_ERROR', 'message': 'Ответ транспорта не получен; итог установлен только последующим чтением'}
                    response = None
                # Persist selected diagnostics as soon as the send returns. A
                # restart during readback must retain the reason and acceptance,
                # while the operation remains UNKNOWN until fresh state proves it.
                received = self._outcome(document, 'UNKNOWN_RESULT',
                    [self._public_row(row) | {'status': 'UNKNOWN_RESULT',
                        'accepted': self._accepted(op, response, row) if response is not None else None,
                        'errors': self._response_errors(op, response, row) if response is not None else [],
                        'message': 'Ответ получен; ожидается сверка состояния'} for row in document['rows']],
                    'Отправка выполнена один раз; ожидается сверка состояния')
                received['transport_error'] = deepcopy(stored.get('transport_error'))
                self._save_history(received)
                outcome = await self._reconcile(document, response)
                # Promotion membership and price reads can lag the write. Retry
                # only reads, under the same lock/account binding, with a finite
                # budget. An accepted response alone never establishes success.
                if op in (Op.ACTION_UPDATE, Op.ACTION_REMOVE, Op.AUTO_UPDATE, Op.AUTO_REMOVE):
                    for delay in (0.5, 1.0, 2.0):
                        pending_rows = [row for row in outcome['rows']
                            if row['status'] in ('UNKNOWN_RESULT', 'DISCREPANCY') and row.get('accepted') is not False]
                        if not pending_rows:
                            break
                        await self.readback_sleep(delay)
                        if (document['account_id'], document['revision']) != self._binding():
                            break
                        outcome = await self._reconcile(document, response)
                return outcome
            finally:
                self.accounts.busy = False
                if writer is not None:
                    await writer.close()

    async def reconcile(self, plan_id, plan_hash):
        async with self._lock:
            stored, document = self._plan(plan_id, plan_hash)
            self._ensure_active()
            same_binding = (document['account_id'], document['revision']) == self._binding()
            if stored.get('recovered_plan'):
                same_binding = document['account_id'] == self.accounts.active_id and stored.get('stable_account_digest') == self._account_digest()
            if not stored["attempted"] or not same_binding:
                raise WriteError("RECONCILIATION_BLOCKED", "Сверка требует исходный кабинет и отправленную попытку", 409)
            if self.accounts.busy:
                raise WriteError("BUSY", "Дождитесь текущей операции магазина", 409)
            self.accounts.busy = True
            try:
                return await self._reconcile(document, None)
            finally:
                self.accounts.busy = False

    async def reconcile_history(self, operation_id):
        stored = self._plans.get(operation_id)
        if stored is None or not stored.get('attempted'):
            raise WriteError('RECONCILIATION_BLOCKED', 'Исходный снимок операции отсутствует; требуется ручная сверка', 409)
        return await self.reconcile(operation_id, stored['hash'])
