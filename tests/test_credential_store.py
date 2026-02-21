"""Tests for credential_store: blob keyring storage, config.json fallback, and migration."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src import config, credential_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SERVICE = credential_store.SERVICE_NAME
ACCOUNT = credential_store.CREDENTIAL_ACCOUNT


def _set_blob(storage, data):
    storage[f"{SERVICE}:{ACCOUNT}"] = json.dumps(data)


def _get_blob(storage):
    raw = storage.get(f"{SERVICE}:{ACCOUNT}")
    return json.loads(raw) if raw else {}


def _write_config(tmp_path, data):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(data))
    return str(cfg_path)


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Point CONFIG_FILE at a temp directory."""
    cfg_path = str(tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_path)
    return tmp_path


@pytest.fixture
def mock_keyring(monkeypatch):
    """Provide a mock keyring module with in-memory storage and mark keyring as available."""
    storage: dict[str, str] = {}

    kr = MagicMock()
    kr.get_password = MagicMock(side_effect=lambda svc, key: storage.get(f"{svc}:{key}"))
    kr.set_password = MagicMock(
        side_effect=lambda svc, key, val: storage.__setitem__(f"{svc}:{key}", val)
    )

    class _PasswordDeleteError(Exception):
        pass

    kr.errors = MagicMock()
    kr.errors.PasswordDeleteError = _PasswordDeleteError

    def _delete(svc, key):
        full_key = f"{svc}:{key}"
        if full_key not in storage:
            raise _PasswordDeleteError("not found")
        del storage[full_key]

    kr.delete_password = MagicMock(side_effect=_delete)

    monkeypatch.setattr(credential_store, "_keyring_available", True)
    monkeypatch.setitem(__import__("sys").modules, "keyring", kr)

    return kr, storage


# ---------------------------------------------------------------------------
# Keyring available (blob-based storage)
# ---------------------------------------------------------------------------

class TestKeyringAvailable:
    def test_get_returns_value(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "my-id"})
        assert credential_store.get("client_id") == "my-id"

    def test_get_returns_none_when_absent(self, mock_keyring):
        assert credential_store.get("client_id") is None

    def test_set_stores_value_in_blob(self, mock_keyring):
        kr, storage = mock_keyring
        credential_store.set("client_id", "abc")
        assert _get_blob(storage)["client_id"] == "abc"

    def test_set_many(self, mock_keyring):
        kr, storage = mock_keyring
        credential_store.set_many({"client_id": "id1", "client_secret": "sec1"})
        blob = _get_blob(storage)
        assert blob["client_id"] == "id1"
        assert blob["client_secret"] == "sec1"

    def test_set_preserves_existing_blob_keys(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "id1", "client_secret": "sec1"})
        credential_store.set("refresh_token", "rt1")
        blob = _get_blob(storage)
        assert blob["client_id"] == "id1"
        assert blob["client_secret"] == "sec1"
        assert blob["refresh_token"] == "rt1"

    def test_delete_removes_value(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "x", "client_secret": "y"})
        credential_store.delete("client_id")
        blob = _get_blob(storage)
        assert "client_id" not in blob
        assert blob["client_secret"] == "y"

    def test_delete_ignores_missing(self, mock_keyring):
        """Deleting a key that doesn't exist should not raise."""
        credential_store.delete("client_id")  # no error

    def test_get_all(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "id1", "refresh_token": "rt1"})
        result = credential_store.get_all()
        assert result["client_id"] == "id1"
        assert result["refresh_token"] == "rt1"
        assert result["client_secret"] is None
        assert result["access_token"] is None  # session-only, no active session

    def test_invalid_key_raises(self, mock_keyring):
        with pytest.raises(ValueError, match="Unknown credential key"):
            credential_store.get("bogus")

    def test_set_invalid_key_raises(self, mock_keyring):
        with pytest.raises(ValueError, match="Unknown credential key"):
            credential_store.set("bogus", "val")

    def test_delete_invalid_key_raises(self, mock_keyring):
        with pytest.raises(ValueError, match="Unknown credential key"):
            credential_store.delete("bogus")

    def test_access_token_is_session_only(self, mock_keyring):
        """access_token must never be written to keyring."""
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "id1"})
        credential_store.load_session()
        credential_store.set("access_token", "at123")

        assert credential_store.get("access_token") == "at123"  # from session cache
        assert "access_token" not in _get_blob(storage)  # not persisted

    def test_access_token_returns_none_outside_session(self, mock_keyring):
        """access_token is inaccessible outside an active session."""
        assert credential_store.get("access_token") is None

    def test_single_blob_entry(self, mock_keyring):
        """All persistent credentials share one keychain entry."""
        kr, storage = mock_keyring
        credential_store.set_many({"client_id": "id1", "client_secret": "sec1", "refresh_token": "rt1"})
        blob_keys = [k for k in storage if k.startswith(f"{SERVICE}:")]
        assert blob_keys == [f"{SERVICE}:{ACCOUNT}"]


# ---------------------------------------------------------------------------
# Keyring unavailable (fallback to config.json)
# ---------------------------------------------------------------------------

class TestKeyringUnavailable:
    def test_get_reads_from_config(self, isolated_config):
        _write_config(isolated_config, {"client_id": "from-file"})
        assert credential_store.get("client_id") == "from-file"

    def test_get_returns_none_when_no_config(self, isolated_config):
        assert credential_store.get("client_id") is None

    def test_set_writes_to_config(self, isolated_config):
        credential_store.set("client_id", "new-id")
        cfg = json.loads((isolated_config / "config.json").read_text())
        assert cfg["client_id"] == "new-id"

    def test_delete_removes_from_config(self, isolated_config):
        _write_config(isolated_config, {"client_id": "x", "other": "y"})
        credential_store.delete("client_id")
        cfg = json.loads((isolated_config / "config.json").read_text())
        assert "client_id" not in cfg
        assert cfg["other"] == "y"

    def test_warns_when_keyring_unavailable(self, monkeypatch):
        """First check of keyring availability should issue a warning."""
        monkeypatch.setattr(credential_store, "_keyring_available", None)
        import builtins
        real_import = builtins.__import__

        def _fail_keyring(name, *args, **kwargs):
            if name == "keyring":
                raise ImportError("no keyring")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail_keyring)
        with pytest.warns(UserWarning, match="OS keyring is not available"):
            credential_store._check_keyring()


# ---------------------------------------------------------------------------
# Migration from config.json
# ---------------------------------------------------------------------------

class TestMigration:
    def test_migrates_persistent_credentials(self, mock_keyring, isolated_config):
        kr, storage = mock_keyring
        creds = {
            "client_id": "id1",
            "client_secret": "sec1",
            "refresh_token": "rt1",
            # access_token and expires_at are NOT migrated
        }
        _write_config(isolated_config, creds)
        credential_store.migrate_from_config()

        blob = _get_blob(storage)
        assert blob["client_id"] == "id1"
        assert blob["client_secret"] == "sec1"
        assert blob["refresh_token"] == "rt1"
        assert not (isolated_config / "config.json").exists()

    def test_preserves_non_credential_keys(self, mock_keyring, isolated_config):
        kr, storage = mock_keyring
        _write_config(isolated_config, {"client_id": "id1", "theme": "dark"})
        credential_store.migrate_from_config()

        assert _get_blob(storage)["client_id"] == "id1"
        cfg = json.loads((isolated_config / "config.json").read_text())
        assert cfg == {"theme": "dark"}

    def test_noop_when_no_config_file(self, mock_keyring, isolated_config):
        """No error when config.json doesn't exist."""
        credential_store.migrate_from_config()  # no error

    def test_noop_when_keyring_unavailable(self, isolated_config, monkeypatch):
        monkeypatch.setattr(credential_store, "_keyring_available", False)
        _write_config(isolated_config, {"client_id": "id1"})
        credential_store.migrate_from_config()
        assert (isolated_config / "config.json").exists()

    def test_idempotent(self, mock_keyring, isolated_config):
        kr, storage = mock_keyring
        _write_config(isolated_config, {"client_id": "id1"})
        credential_store.migrate_from_config()
        credential_store.migrate_from_config()  # second call should not error
        assert _get_blob(storage)["client_id"] == "id1"

    def test_noop_when_no_credential_keys(self, mock_keyring, isolated_config):
        """config.json with only non-credential keys should be left alone."""
        _write_config(isolated_config, {"theme": "dark"})
        credential_store.migrate_from_config()
        assert (isolated_config / "config.json").exists()


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------

class TestSessionCache:
    @pytest.fixture(autouse=True)
    def _clear_session(self):
        """Ensure session cache is cleared after each test."""
        yield
        credential_store.clear_session()

    def test_load_session_performs_one_keyring_read(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "cached-id", "refresh_token": "cached-rt"})

        credential_store.load_session()
        assert credential_store.get("client_id") == "cached-id"
        assert credential_store.get("refresh_token") == "cached-rt"

        # Keyring only accessed during load_session, not on subsequent get()
        initial_calls = kr.get_password.call_count
        credential_store.get("client_id")
        credential_store.get("refresh_token")
        assert kr.get_password.call_count == initial_calls

    def test_clear_session_wipes_cache(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "cached-id"})

        credential_store.load_session()
        assert credential_store.get("client_id") == "cached-id"

        credential_store.clear_session()
        # After clearing, get() goes back to keyring (one more read)
        assert credential_store.get("client_id") == "cached-id"
        assert kr.get_password.call_count > 0

    def test_set_updates_session_cache_and_keyring(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"refresh_token": "old-rt"})

        credential_store.load_session()
        credential_store.set("refresh_token", "new-rt")

        assert credential_store.get("refresh_token") == "new-rt"
        assert _get_blob(storage)["refresh_token"] == "new-rt"

    def test_set_access_token_updates_only_session_cache(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "id1"})

        credential_store.load_session()
        credential_store.set("access_token", "at123")

        assert credential_store.get("access_token") == "at123"
        assert "access_token" not in _get_blob(storage)

    def test_get_without_session_hits_keyring(self, mock_keyring):
        kr, storage = mock_keyring
        _set_blob(storage, {"client_id": "direct"})

        # No load_session — should read from keyring directly
        assert credential_store.get("client_id") == "direct"

    def test_session_missing_key_returns_none(self, mock_keyring):
        credential_store.load_session()
        assert credential_store.get("client_id") is None
