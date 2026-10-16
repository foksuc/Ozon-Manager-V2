"""Local backend credentials only. No environment fallback or frontend exposure."""
from dataclasses import dataclass, field
import os
import re
from pathlib import Path
from .storage import ROOT, confined


@dataclass(frozen=True)
class Credentials:
    client_id: str = field(repr=False)
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class Connection:
    mode: str
    credentials: Credentials | None = field(default=None, repr=False)
    message: str = ""

    def public(self) -> dict:
        return {"mode": self.mode, "configured": self.credentials is not None,
                "read_only": True, "message": self.message}


def parse_credentials(text: str) -> Credentials:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("Некорректный формат backend/.env")
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in {"OZON_CLIENT_ID", "OZON_API_KEY"} or name in values:
            raise ValueError("В backend/.env разрешены только два уникальных поля подключения")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Некорректные кавычки в backend/.env")
            value = value[1:-1]
        values[name] = value
    client_id, api_key = values.get("OZON_CLIENT_ID", ""), values.get("OZON_API_KEY", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", client_id) or int(client_id) > 2**64 - 1:
        raise ValueError("Заполните корректный OZON_CLIENT_ID в backend/.env")
    if not re.fullmatch(r"[!-~]{1,4096}", api_key):
        raise ValueError("Заполните OZON_API_KEY без пробелов и переносов в backend/.env")
    return Credentials(client_id, api_key)


def load_connection(mode: str | None = None) -> Connection:
    selected = mode if mode is not None else os.environ.get("OZON_V2_MODE", "MOCK")
    if selected == "MOCK":
        # MOCK must not even inspect/read a credentials file.
        return Connection("MOCK", message="Синтетические данные; запросов Ozon нет")
    if selected != "LIVE_READ":
        raise ValueError("Разрешены только режимы MOCK и LIVE_READ")
    try:
        path = confined(ROOT / "backend/.env")
        if not path.is_file():
            return Connection(selected, message="Создайте backend/.env по .env.example, заполните локально и перезапустите start-read. Ключ в чат не отправляйте.")
        if path.stat().st_size > 16384:
            raise ValueError("backend/.env превышает допустимый размер")
        with path.open("r", encoding="utf-8-sig") as stream:
            text = stream.read(16385)
        if len(text) > 16384:
            raise ValueError("backend/.env превышает допустимый размер")
        credentials = parse_credentials(text)
        return Connection(selected, credentials, "Ключ настроен локально. Доступ к Ozon ещё нужно проверить.")
    except (ValueError, OSError, UnicodeError):
        # Never surface parser lines, OS paths, or credential values in errors.
        return Connection(selected, message="Не удалось безопасно прочитать backend/.env. Проверьте формат двух полей и отсутствие ссылок/junction, затем перезапустите start-read.")
