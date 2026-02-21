"""Tests for configuration loading, saving, and metatag loading."""

import json
from unittest.mock import patch

import pytest

from src import config


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    """Point all file paths at tmp_path for every test."""
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "METATAG_FILE", str(tmp_path / "metatag.json"))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.json"))


# -----------------------------------------------------------------------
# load_config
# -----------------------------------------------------------------------

class TestLoadConfig:
    def test_missing_file_returns_empty_dict(self):
        assert config.load_config() == {}

    def test_loads_existing_file(self, tmp_path):
        cfg = {"client_id": "abc", "client_secret": "xyz"}
        (tmp_path / "config.json").write_text(json.dumps(cfg))
        assert config.load_config() == cfg


# -----------------------------------------------------------------------
# save_config
# -----------------------------------------------------------------------

class TestSaveConfig:
    def test_writes_json(self, tmp_path):
        cfg = {"client_id": "abc", "access_token": "tok123"}
        config.save_config(cfg)
        written = json.loads((tmp_path / "config.json").read_text())
        assert written == cfg

    def test_overwrites_existing(self, tmp_path):
        (tmp_path / "config.json").write_text('{"old": true}')
        config.save_config({"new": True})
        written = json.loads((tmp_path / "config.json").read_text())
        assert written == {"new": True}


# -----------------------------------------------------------------------
# load_metatags
# -----------------------------------------------------------------------

class TestLoadMetatags:
    def test_missing_file_exits(self):
        with pytest.raises(SystemExit):
            config.load_metatags()

    def test_loads_metatags(self, tmp_path):
        tags = {"L": "laboratory", "R": "radiology"}
        (tmp_path / "metatag.json").write_text(json.dumps(tags))
        assert config.load_metatags() == tags


# -----------------------------------------------------------------------
# ensure_credentials
# -----------------------------------------------------------------------

class TestEnsureCredentials:
    def test_existing_credentials_returned_as_is(self):
        cfg = {"client_id": "abc", "client_secret": "xyz"}
        result = config.ensure_credentials(cfg)
        assert result == cfg

    @patch("builtins.input", side_effect=["my-id", "my-secret"])
    def test_prompts_when_missing(self, mock_input, tmp_path):
        result = config.ensure_credentials({})
        assert result["client_id"] == "my-id"
        assert result["client_secret"] == "my-secret"
        # Should also persist to disk
        saved = json.loads((tmp_path / "config.json").read_text())
        assert saved["client_id"] == "my-id"
