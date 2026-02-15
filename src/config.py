"""Configuration loading and credential management."""

import json
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
METATAG_FILE = os.path.join(APP_DIR, "metatag.json")


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


def ensure_credentials(config):
    if not config.get("client_id") or not config.get("client_secret"):
        print("No DrChrono credentials found. Let's set them up.")
        print("(Register an app at https://app.drchrono.com/api-management/ first)\n")
        config["client_id"] = input("Client ID: ").strip()
        config["client_secret"] = input("Client Secret: ").strip()
        save_config(config)
    return config
