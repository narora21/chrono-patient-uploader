"""Configuration loading and credential management."""

import json
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
METATAG_FILE = os.path.join(APP_DIR, "metatag.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")


def load_config():
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
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def ensure_credentials(config):
    if not config.get("client_id") or not config.get("client_secret"):
        print("No DrChrono credentials found. Let's set them up.\n")
        print("  1. Go to https://drchrono.com/api-management/")
        print("  2. Click 'New Application' and name it 'Dr Chrono Document Uploader' or whatever you want")
        print("  3. Set the Redirect URI to: http://localhost:8585/callback")
        print("  4. Copy the Client ID and Client Secret from the app details")
        print("       * This will save credentials into a file `config.json` DO NOT SHARE THIS FILE\n")
        config["client_id"] = input("Client ID: ").strip()
        config["client_secret"] = input("Client Secret: ").strip()
        save_config(config)
    return config
