"""User-entered accounts; Windows user DPAPI, no public credential readback."""
import ctypes
from ctypes import wintypes
import json
import os
import threading
from pathlib import Path
from uuid import UUID, uuid4
from .connection import Credentials, parse_credentials
from .storage import ROOT, confined


class AccountError(ValueError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise AccountError("Защищённое хранение кабинетов доступно только в Windows")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, output = _Blob(len(value), buffer), _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN; default user scope, never machine scope.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise AccountError("Не удалось обработать защищённые данные кабинетов")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class AccountStore:
    def __init__(self, mode="MOCK", *, path=None, root=ROOT, cipher=dpapi):
        if mode not in ("MOCK", "LIVE_READ"):
            raise AccountError("Неизвестный режим кабинетов")
        self.mode, self.root, self.cipher = mode, root, cipher
        self.path = Path(path) if path else root / ".cache/accounts.bin"
        self._lock = threading.RLock()
        self.busy = False
        self._accounts, self.active_id, self.revision = {}, None, 0
        if mode == "MOCK":
            self.active_id = "00000000-0000-4000-8000-000000000001"
            self._accounts[self.active_id] = {"name": "Учебный кабинет", "credentials": None}
            return  # No file inspection and no encryption in MOCK.
        try:
            path = confined(self.path, root)
            if path.exists():
                if path.stat().st_size > 1024 * 1024:
                    raise AccountError("Хранилище кабинетов повреждено")
                document = json.loads(cipher(path.read_bytes(), decrypt=True))
                if set(document) != {"version", "active_id", "accounts"} or document["version"] != 1:
                    raise AccountError("Неизвестный формат кабинетов")
                for account in document["accounts"]:
                    account_id = str(UUID(account["id"]))
                    if account_id in self._accounts:
                        raise AccountError("Повторяющийся кабинет")
                    self._accounts[account_id] = {"name": self._name(account["name"]),
                        "credentials": self._credentials(account["client_id"], account["api_key"])}
                self.active_id = document["active_id"]
                if self.active_id is not None and self.active_id not in self._accounts:
                    raise AccountError("Неизвестный активный кабинет")
        except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
            raise AccountError("Не удалось открыть защищённое хранилище кабинетов") from None

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(c) < 32 for c in name):
            raise AccountError("Укажите название кабинета до 100 символов")
        return name.strip()

    @staticmethod
    def _credentials(client_id, api_key):
        if not isinstance(client_id, str) or not isinstance(api_key, str) or any(c in client_id + api_key for c in "\r\n"):
            raise AccountError("Некорректные данные подключения")
        try:
            return parse_credentials(f"OZON_CLIENT_ID={client_id}\nOZON_API_KEY={api_key}")
        except ValueError:
            raise AccountError("Некорректные данные подключения") from None

    def _persist(self):
        if self.mode == "MOCK":
            return
        document = {"version": 1, "active_id": self.active_id, "accounts": [
            {"id": key, "name": value["name"], "client_id": value["credentials"].client_id,
             "api_key": value["credentials"].api_key} for key, value in self._accounts.items()]}
        encrypted = self.cipher(json.dumps(document, ensure_ascii=False).encode("utf-8"))
        path = confined(self.path, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = confined(path.with_name(path.name + ".tmp-" + uuid4().hex), self.root)
        try:
            with temporary.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            confined(path, self.root)
            os.replace(temporary, path)
        except OSError:
            raise AccountError("Не удалось сохранить защищённый кабинет") from None

    def list(self):
        with self._lock:
            return {"mode": self.mode, "active_id": self.active_id, "revision": self.revision,
                    "accounts": [{"id": key, "name": value["name"], "configured": self.mode == "LIVE_READ"}
                                 for key, value in self._accounts.items()]}

    def credentials(self):
        with self._lock:
            account = self._accounts.get(self.active_id)
            return account["credentials"] if account else None

    def _change(self, operation):
        with self._lock:
            if self.busy:
                raise AccountError("Сначала дождитесь завершения текущей операции")
            previous, active = self._accounts.copy(), self.active_id
            try:
                operation()
                self._persist()
            except Exception:
                self._accounts, self.active_id = previous, active
                raise
            self.revision += 1
            return self.list()

    def add(self, name, client_id="", api_key=""):
        name = self._name(name)
        credentials = None if self.mode == "MOCK" else self._credentials(client_id, api_key)
        account_id = str(uuid4())
        def change():
            if len(self._accounts) >= 20:
                raise AccountError("Допустимо не больше 20 кабинетов")
            self._accounts[account_id] = {"name": name, "credentials": credentials}
            if self.active_id is None:
                self.active_id = account_id
        return self._change(change)

    def update(self, account_id, name, client_id="", api_key=""):
        name = self._name(name)
        def change():
            account = self._accounts.get(account_id)
            if account is None:
                raise AccountError("Кабинет не найден")
            credentials = account["credentials"]
            if self.mode != "MOCK" and api_key:
                credentials = self._credentials(client_id or credentials.client_id, api_key)
            elif self.mode != "MOCK" and client_id and client_id != credentials.client_id:
                raise AccountError("При смене Client-ID введите новый API-ключ")
            self._accounts[account_id] = {"name": name, "credentials": credentials}
        return self._change(change)

    def delete(self, account_id):
        def change():
            if account_id not in self._accounts:
                raise AccountError("Кабинет не найден")
            del self._accounts[account_id]
            if self.active_id == account_id:
                self.active_id = next(iter(self._accounts), None)
        return self._change(change)

    def select(self, account_id):
        def change():
            if account_id not in self._accounts:
                raise AccountError("Кабинет не найден")
            self.active_id = account_id
        return self._change(change)
