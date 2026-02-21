"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring_in_tests(monkeypatch):
    """Ensure tests never touch the real OS keyring.

    With _keyring_available = False, credential_store.get() falls back to
    config.json (which doesn't exist in test), returning None.  This lets
    existing FAKE_CONFIG dicts continue to work via the ``or config.get(...)``
    fallback in api_headers / auth functions.
    """
    from src import credential_store
    monkeypatch.setattr(credential_store, "_keyring_available", False)
    monkeypatch.setattr(credential_store, "_session_cache", None)
