"""Confined, transactional SQLite storage; no configurable production database."""
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4
from .catalog import timestamp

ROOT = Path(__file__).absolute().parents[2]
DATABASE = ROOT / "data" / "mock.sqlite3"


def confined(path: Path, root: Path = ROOT) -> Path:
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    if path == root or not path.is_relative_to(root):
        raise ValueError("Путь вне разрешённой папки V2")
    # Inspect every existing ancestor, including root's ancestors: fail closed on junctions.
    for part in [path, *path.parents]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Ссылки и junction запрещены")
    return path


def canonical(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in plan.items() if k != "hash"}).encode()).hexdigest()


def migrate(connection: sqlite3.Connection, *, fail: bool = False) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > 1:
        raise ValueError("Неподдерживаемая версия БД")
    if version == 0:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE plans(id TEXT PRIMARY KEY, hash TEXT NOT NULL, body TEXT NOT NULL)")
            connection.execute("CREATE TABLE drafts(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE REFERENCES plans(id), created_at TEXT NOT NULL)")
            if fail:
                raise RuntimeError("Synthetic migration failure")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


class Store:
    def __init__(self, path: Path = DATABASE, *, root: Path = ROOT):
        self.path, self.root = Path(path), Path(root)

    def connect(self) -> sqlite3.Connection:
        path = confined(self.path, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        confined(path, self.root)
        connection = sqlite3.connect(path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            migrate(connection)
        except Exception:
            connection.close()
            raise
        return connection

    def put_plan(self, plan: dict) -> None:
        connection = self.connect()
        try:
            with connection:
                connection.execute("INSERT INTO plans VALUES(?,?,?)", (plan["id"], plan["hash"], canonical(plan)))
        finally:
            connection.close()

    @staticmethod
    def draft(connection: sqlite3.Connection, row: tuple) -> dict:
        body = connection.execute("SELECT body FROM plans WHERE id=?", (row[1],)).fetchone()[0]
        plan = json.loads(body)
        if plan_hash(plan) != plan["hash"]:
            raise ValueError("Нарушена целостность плана")
        return dict(id=row[0], status="DRAFT", mode="MOCK", created_at=row[2], plan=plan)

    def save_draft(self, plan_id: str, expected_hash: str) -> dict:
        connection = self.connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT body FROM plans WHERE id=?", (plan_id,)).fetchone()
                if not row:
                    raise LookupError("План не найден")
                plan = json.loads(row[0])
                if expected_hash != plan["hash"] or plan_hash(plan) != expected_hash:
                    raise ValueError("Hash плана не совпадает")
                if "source" not in plan:
                    raise ValueError("В старом плане нет снимка источника; создайте новый preview")
                if not plan["valid"] or not plan["complete"]:
                    raise ValueError("Невалидный или неполный план нельзя сохранить")
                existing = connection.execute("SELECT id,plan_id,created_at FROM drafts WHERE plan_id=?", (plan_id,)).fetchone()
                if existing is None:
                    existing = (str(uuid4()), plan_id, timestamp())
                    connection.execute("INSERT INTO drafts VALUES(?,?,?)", existing)
                return self.draft(connection, existing)
        finally:
            connection.close()

    def history(self) -> list[dict]:
        connection = self.connect()
        try:
            return [self.draft(connection, row) for row in connection.execute("SELECT id,plan_id,created_at FROM drafts ORDER BY created_at DESC,id DESC").fetchall()]
        finally:
            connection.close()

    def backup(self, target: Path) -> None:
        target = confined(target, self.root)
        if target.exists() or target == self.path:
            raise ValueError("Резервная копия уже существует")
        target.parent.mkdir(parents=True, exist_ok=True)
        confined(target, self.root)
        source = self.connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        check = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Проверка резервной копии не пройдена")
            if check.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Версия резервной копии неверна")
            for body, in check.execute("SELECT body FROM plans"):
                plan = json.loads(body)
                if plan_hash(plan) != plan["hash"]:
                    raise ValueError("Hash резервного плана неверен")
        finally:
            check.close()
