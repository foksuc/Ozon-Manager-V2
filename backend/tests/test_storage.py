import sqlite3
import pytest
from app.storage import Store, confined, migrate, plan_hash


def sample():
    plan = dict(id="synthetic-plan", kind="stocks", created_at="2026-10-08T00:00:00Z", complete=True, valid=True,
                source=dict(mode="MOCK", scenario="ready", fetched_at="2026-10-08T00:00:00Z", action_id=None, items=[]), rows=[], errors=[])
    plan["hash"] = plan_hash(plan)
    return plan


def test_backup_verified_and_readable(tmp_path):
    store = Store(tmp_path / "data/mock.sqlite3", root=tmp_path)
    plan = sample()
    store.put_plan(plan)
    draft = store.save_draft(plan["id"], plan["hash"])
    target = tmp_path / "data/backup.sqlite3"
    store.backup(target)
    assert Store(target, root=tmp_path).history() == [draft]
    with pytest.raises(ValueError):
        store.backup(target)


def test_migration_rollback(tmp_path):
    path = tmp_path / "migration.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE original(value TEXT)")
    connection.execute("INSERT INTO original VALUES('synthetic')")
    connection.commit()
    with pytest.raises(RuntimeError):
        migrate(connection, fail=True)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert connection.execute("SELECT value FROM original").fetchone()[0] == "synthetic"
    assert connection.execute("SELECT name FROM sqlite_master WHERE name IN ('plans','drafts')").fetchall() == []
    migrate(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    connection.close()


def test_confinement_before_database_write(tmp_path):
    outside = tmp_path.parent / "forbidden-mock.sqlite3"
    with pytest.raises(ValueError):
        Store(outside, root=tmp_path).connect()
    assert not outside.exists()
    with pytest.raises(ValueError):
        confined(tmp_path, tmp_path)


def test_tampered_saved_plan_rejected(tmp_path):
    store = Store(tmp_path / "mock.sqlite3", root=tmp_path)
    plan = sample()
    store.put_plan(plan)
    connection = store.connect()
    with connection:
        connection.execute("UPDATE plans SET body=replace(body, 'stocks', 'prices')")
    connection.close()
    with pytest.raises(ValueError):
        store.save_draft(plan["id"], plan["hash"])


def test_legacy_plan_without_source_cannot_be_saved(tmp_path):
    store = Store(tmp_path / "mock.sqlite3", root=tmp_path)
    plan = sample()
    del plan["source"]
    plan["hash"] = plan_hash(plan)
    store.put_plan(plan)
    with pytest.raises(ValueError, match="снимка источника"):
        store.save_draft(plan["id"], plan["hash"])
