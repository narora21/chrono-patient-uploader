"""Secure credential storage using OS keyring with config.json fallback."""

import json
import os
import warnings

SERVICE_NAME = "chrono-patient-uploader"
CREDENTIAL_ACCOUNT = "credentials"  # single keychain account — one JSON blob

# Keys stored persistently in keyring (or config.json fallback)
CREDENTIAL_KEYS = frozenset({"client_id", "client_secret", "refresh_token"})

# Keys that live in memory only during a session (never written to keyring)
SESSION_ONLY_KEYS = frozenset({"access_token"})

ALL_KEYS = CREDENTIAL_KEYS | SESSION_ONLY_KEYS

_keyring_available: bool | None = None
_session_cache: dict[str, str] | None = None


def _check_keyring() -> bool:
    """Check if keyring is importable and functional (cached after first call)."""
    global _keyring_available
    if _keyring_available is not None:
        return _keyring_available
    try:
        import keyring as kr
        kr.get_password(SERVICE_NAME, CREDENTIAL_ACCOUNT)
        _keyring_available = True
    except Exception:
        _keyring_available = False
        warnings.warn(
            "OS keyring is not available. Credentials will be stored in "
            "plaintext config.json. Install the 'keyring' package and ensure "
            "your OS keychain is configured for secure storage.",
            UserWarning,
            stacklevel=2,
        )
    return _keyring_available


def _read_blob() -> dict[str, str]:
    """Read the single JSON credential blob from keyring."""
    import keyring as kr
    raw = kr.get_password(SERVICE_NAME, CREDENTIAL_ACCOUNT)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _write_blob(data: dict[str, str]) -> None:
    """Write the credential dict as a JSON blob to keyring."""
    import keyring as kr
    kr.set_password(SERVICE_NAME, CREDENTIAL_ACCOUNT, json.dumps(data))


def get(key: str) -> str | None:
    """Load a single credential value.

    Returns from the session cache if active, otherwise reads from keyring
    (or config.json fallback). Session-only keys (e.g. access_token) return
    None outside of an active session.
    """
    if key not in ALL_KEYS:
        raise ValueError(f"Unknown credential key: {key}")
    if _session_cache is not None:
        return _session_cache.get(key)
    if key in SESSION_ONLY_KEYS:
        return None
    if _check_keyring():
        return _read_blob().get(key)
    from src.config import load_config
    return load_config().get(key)


def get_all() -> dict[str, str | None]:
    """Load all credential values."""
    return {k: get(k) for k in ALL_KEYS}


def set(key: str, value: str) -> None:
    """Store a single credential value."""
    if key not in ALL_KEYS:
        raise ValueError(f"Unknown credential key: {key}")
    if _session_cache is not None:
        _session_cache[key] = value
    if key in SESSION_ONLY_KEYS:
        return  # never written to persistent storage
    if _check_keyring():
        if _session_cache is not None:
            # Rebuild blob from session cache — avoids an extra keyring read
            blob = {k: _session_cache[k] for k in CREDENTIAL_KEYS if k in _session_cache}
        else:
            blob = _read_blob()
            blob[key] = value
        _write_blob(blob)
    else:
        from src.config import load_config, save_config
        cfg = load_config()
        cfg[key] = value
        save_config(cfg)


def set_many(credentials: dict[str, str]) -> None:
    """Store multiple credential values."""
    for k, v in credentials.items():
        set(k, v)


def delete(key: str) -> None:
    """Remove a credential from the store."""
    if key not in ALL_KEYS:
        raise ValueError(f"Unknown credential key: {key}")
    if _session_cache is not None:
        _session_cache.pop(key, None)
    if key in SESSION_ONLY_KEYS:
        return
    if _check_keyring():
        if _session_cache is not None:
            blob = {k: _session_cache[k] for k in CREDENTIAL_KEYS if k in _session_cache}
        else:
            blob = _read_blob()
            blob.pop(key, None)
        _write_blob(blob)
    else:
        from src.config import load_config, save_config
        cfg = load_config()
        if key in cfg:
            del cfg[key]
            save_config(cfg)


def delete_all() -> None:
    """Remove all credentials from the store."""
    if _session_cache is not None:
        _session_cache.clear()
    if _check_keyring():
        import keyring as kr
        try:
            kr.delete_password(SERVICE_NAME, CREDENTIAL_ACCOUNT)
        except Exception:
            pass
    else:
        from src.config import load_config, save_config
        cfg = load_config()
        remaining = {k: v for k, v in cfg.items() if k not in CREDENTIAL_KEYS}
        save_config(remaining)


def load_session() -> None:
    """Load all credentials into an in-memory session cache.

    Performs exactly one keyring read. While the session is active, get()
    reads from memory instead of hitting the OS keyring on every call.
    Call clear_session() when the upload batch is done to wipe credentials
    from memory.
    """
    global _session_cache
    _session_cache = {}
    if _check_keyring():
        _session_cache.update(_read_blob())
    else:
        from src.config import load_config
        cfg = load_config()
        for key in CREDENTIAL_KEYS:
            if key in cfg:
                _session_cache[key] = cfg[key]


def clear_session() -> None:
    """Wipe the in-memory session cache."""
    global _session_cache
    if _session_cache is not None:
        _session_cache.clear()
    _session_cache = None


def _migrate_single_config(cfg_path: str) -> bool:
    """Migrate credentials from a single config.json path to keyring.

    Returns True if any credentials were migrated.
    """
    if not os.path.exists(cfg_path):
        return False

    with open(cfg_path, "r") as f:
        cfg = json.load(f)

    to_migrate = {k: cfg[k] for k in CREDENTIAL_KEYS if k in cfg}
    if not to_migrate:
        return False

    blob = _read_blob()
    blob.update(to_migrate)
    _write_blob(blob)

    remaining = {k: v for k, v in cfg.items() if k not in CREDENTIAL_KEYS}
    if remaining:
        with open(cfg_path, "w") as f:
            json.dump(remaining, f, indent=2)
    else:
        os.remove(cfg_path)

    return True


def migrate_from_config() -> None:
    """One-time migration: move credentials from config.json to keyring.

    Checks the home directory, next to the binary, and the data directory —
    covering all known locations the old app may have written config.json to.
    No-op if keyring is unavailable or no migration is needed.
    Safe to call on every startup (idempotent).
    """
    if not _check_keyring():
        return

    from src.config import APP_DIR, CONFIG_FILE

    migrated = False

    # Home directory — most common location when binary was run via PATH
    home_config = os.path.join(os.path.expanduser("~"), "config.json")
    if home_config not in (CONFIG_FILE,) and os.path.exists(home_config):
        migrated |= _migrate_single_config(home_config)

    # Next to the binary
    app_config = os.path.join(APP_DIR, "config.json")
    if app_config not in (CONFIG_FILE, home_config) and os.path.exists(app_config):
        migrated |= _migrate_single_config(app_config)

    # Data directory (new canonical location)
    migrated |= _migrate_single_config(CONFIG_FILE)

    if migrated:
        print("Credentials migrated to OS keyring.")
