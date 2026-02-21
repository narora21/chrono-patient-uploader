"""Configuration loading and credential management."""

import json
import os
import platform
import sys

if getattr(sys, "frozen", False):
    # PyInstaller bundle — sys.executable is the actual binary path
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
METATAG_FILE = os.path.join(APP_DIR, "metatag.json")


def _data_dir() -> str:
    """Return the platform-appropriate user data directory, creating it if needed."""
    system = platform.system()
    if system == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    elif system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:  # Linux / other
        base = os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))
    data = os.path.join(base, "chrono-patient-uploader")
    os.makedirs(data, exist_ok=True)
    return data


DATA_DIR = _data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


def _migrate_file(filename: str) -> None:
    """Move a file from the old APP_DIR location to the new DATA_DIR if needed."""
    old_path = os.path.join(APP_DIR, filename)
    new_path = os.path.join(DATA_DIR, filename)
    if old_path != new_path and os.path.exists(old_path) and not os.path.exists(new_path):
        import shutil
        shutil.move(old_path, new_path)


def load_config():
    _migrate_file("config.json")
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def load_metatags():
    if not os.path.exists(METATAG_FILE):
        print(f"Error: metatag.json not found at {METATAG_FILE}")
        print("Create it with tag code -> full name mappings, e.g.:")
        print('  {"L": "laboratory", "R": "radiology", ...}')
        sys.exit(1)
    with open(METATAG_FILE, "r") as f:
        return json.load(f)


def save_metatags(metatags):
    with open(METATAG_FILE, "w") as f:
        json.dump(metatags, f, indent=2)


def load_settings():
    _migrate_file("settings.json")
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def ensure_credentials(config):
    from src.credential_store import get as cred_get, set as cred_set
    client_id = cred_get("client_id") or config.get("client_id")
    client_secret = cred_get("client_secret") or config.get("client_secret")
    if not client_id or not client_secret:
        print("No DrChrono credentials found. Let's set them up.\n")
        print("  1. Go to https://drchrono.com/api-management/")
        print("  2. Click 'New Application' and name it 'Dr Chrono Document Uploader' or whatever you want")
        print("  3. Set the Redirect URI to: http://localhost:8585/callback")
        print("  4. Copy the Client ID and Client Secret from the app details\n")
        client_id = input("Client ID: ").strip()
        client_secret = input("Client Secret: ").strip()
        cred_set("client_id", client_id)
        cred_set("client_secret", client_secret)
    config["client_id"] = client_id
    config["client_secret"] = client_secret
    return config
