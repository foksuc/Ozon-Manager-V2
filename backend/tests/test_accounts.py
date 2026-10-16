import json
from pathlib import Path
import pytest
from app.accounts import AccountStore, AccountError, dpapi


def cipher(value, *, decrypt=False):
    return value[4:][::-1] if decrypt else b"TEST" + value[::-1]


def test_mock_never_inspects_credentials_store(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Secret access in MOCK")
    store = AccountStore("MOCK", path=tmp_path / "missing" / "secret", cipher=forbidden)
    store.add("Второй", "SYNTHETIC", "not-used")
    assert store.credentials() is None
    assert not (tmp_path / "missing").exists()


def test_encrypted_roundtrip_public_has_no_credentials(tmp_path):
    path = tmp_path / "accounts.bin"
    store = AccountStore("LIVE_READ", root=tmp_path, path=path, cipher=cipher)
    public = store.add("Тестовый", "123", "synthetic-api-key")
    account_id = public["active_id"]
    assert b"synthetic-api-key" not in path.read_bytes()
    assert "synthetic-api-key" not in json.dumps(public)
    assert "client_id" not in json.dumps(public)
    restored = AccountStore("LIVE_READ", root=tmp_path, path=path, cipher=cipher)
    assert restored.active_id == account_id
    assert restored.credentials().api_key == "synthetic-api-key"
    assert "synthetic-api-key" not in repr(restored.credentials())


def test_edit_blank_key_keeps_secret_delete_switch_revision(tmp_path):
    store = AccountStore("LIVE_READ", root=tmp_path, path=tmp_path / "a", cipher=cipher)
    store.add("Первый", "1", "synthetic-key")
    first = store.active_id
    store.update(first, "Новое имя")
    assert store.credentials().api_key == "synthetic-key"
    store.add("Второй", "2", "synthetic-next")
    second = next(key for key in store._accounts if key != first)
    revision = store.revision
    store.select(second)
    assert store.revision == revision + 1
    store.delete(second)
    assert store.active_id == first


@pytest.mark.parametrize("client,key", [("0", "k"), ("-1", "k"), ("1\nOZON_API_KEY=bad", "k"), ("1", "k\nextra"), ("1", "k k"), ("1", "")])
def test_credentials_validation_no_secret_errors(tmp_path, client, key):
    store = AccountStore("LIVE_READ", root=tmp_path, path=tmp_path / "a", cipher=cipher)
    with pytest.raises(AccountError) as error:
        store.add("Test", client, key)
    assert "bad" not in str(error.value) and "extra" not in str(error.value)
    assert store.list()["accounts"] == []


def test_store_confinement_and_corruption(tmp_path):
    with pytest.raises(AccountError):
        AccountStore("LIVE_READ", root=tmp_path, path=tmp_path.parent / "outside", cipher=cipher)
    path = tmp_path / "corrupt"
    path.write_bytes(b"TESTbroken")
    with pytest.raises(AccountError, match="защищённое"):
        AccountStore("LIVE_READ", root=tmp_path, path=path, cipher=cipher)


def test_failed_persist_rolls_back_and_busy_rejects_account_change(tmp_path):
    def fail(*args, **kwargs):
        raise AccountError("Synthetic encryption failure")
    store = AccountStore("LIVE_READ", root=tmp_path, path=tmp_path / "a", cipher=fail)
    with pytest.raises(AccountError):
        store.add("Test", "1", "synthetic")
    assert store.active_id is None and store.revision == 0
    mock = AccountStore()
    mock.busy = True
    with pytest.raises(AccountError):
        mock.delete(mock.active_id)


def test_windows_dpapi_roundtrip_and_ciphertext():
    import os
    if os.name != "nt":
        pytest.skip("Windows user DPAPI")
    value = b"synthetic-credential-test-only"
    encrypted = dpapi(value)
    assert value not in encrypted
    assert dpapi(encrypted, decrypt=True) == value
