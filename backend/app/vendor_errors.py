"""Bounded vendor diagnostics: original reason and honest Russian explanation."""
import json
import re


def translate_vendor_reason(reason):
    text = reason if isinstance(reason, str) else ''
    match = re.search(r'DiscountPercent must be greater or equal than ([\d.,]+), but actual is ([\d.,]+)', text, re.I)
    if match:
        return f'Скидка должна быть не меньше {match[1]}%; фактическая скидка — {match[2]}%.'
    patterns = (
        (r'product (?:not found|does not exist)', 'Товар не найден.'),
        (r'action (?:not found|does not exist)', 'Акция не найдена.'),
        (r'(?:product|item).*(?:not in|not participating)', 'Товар не участвует в акции.'),
        (r'(?:access denied|permission denied|forbidden)', 'Доступ к операции запрещён.'),
        (r'(?:invalid api.key|unauthorized)', 'Не удалось подтвердить авторизацию Ozon.'),
        (r'(?:rate limit|too many requests)', 'Превышена частота запросов Ozon.'),
    )
    for pattern, translation in patterns:
        if re.search(pattern, text, re.I):
            return translation
    if re.search('[А-Яа-яЁё]', text.replace('[скрыто]', '')) and not re.search('[A-Za-z]', text):
        return text
    return 'Ozon сообщил причину, для которой нет точного перевода; смотрите оригинал.' if text else 'Ozon не указал причину отказа.'


def normalize_vendor_error(error, *, secrets=()):
    """Allowlisted fields only; never copy arbitrary vendor response objects."""
    def safe(value, depth=0):
        if depth > 4:
            return None
        if isinstance(value, str):
            for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
                value = value.replace(secret, '[скрыто]')
            return value[:2000]
        if value is None or type(value) in (int, bool):
            return value
        if isinstance(value, list):
            return [safe(item, depth + 1) for item in value[:10]]
        if isinstance(value, dict):
            return {key: safe(item, depth + 1) for key, item in value.items()
                    if key in ('code', 'message', 'reason', 'error', 'errors', 'details', 'description')}
        return None
    def text(value):
        value = safe(value)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)[:2000] if value is not None else ''
    fields = error if isinstance(error, dict) else {'reason': error}
    reason = text(fields.get('reason', fields.get('message', fields.get('error', fields.get('description', '')))))
    price_explanations = {
        'BELOW_MIN_PRICE': 'Ozon сообщил о цене ниже минимальной; фактический результат проверяется отдельно.',
        'EXTREMELY_LOW_PRICE': 'Ozon сообщил о скидке больше 70%; фактический результат проверяется отдельно.',
        'FAILED_PRICE': 'Ozon сообщил о проблеме проверки цены; фактический результат проверяется отдельно.',
    }
    translated = price_explanations.get(text(fields.get('code')), translate_vendor_reason(reason))
    return {'code': text(fields.get('code', 'API_ERROR')) or 'API_ERROR',
            'message': translated, 'reason': reason, 'reason_ru': translated}
