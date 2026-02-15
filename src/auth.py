"""OAuth2 authorization flow for DrChrono API."""

import datetime
import sys
import threading
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

from src.config import save_config

DRCHRONO_BASE = "https://app.drchrono.com"
REDIRECT_PORT = 8585
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPED_PERMISSIONS = [
    "patients:summary:read", 
    "patients:read",
    "patients:write", 
    "clinical:read",
    "clinical:write"
]


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect and captures the authorization code."""

    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "error" in params:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization denied. You can close this tab.")
            _CallbackHandler.auth_code = None
        elif "code" in params:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization successful! You can close this tab.")
            _CallbackHandler.auth_code = params["code"][0]
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Unexpected response. You can close this tab.")
            _CallbackHandler.auth_code = None

        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format, *args):
        pass


def _store_tokens(config, data):
    """Save token data into config and persist to disk."""
    config["access_token"] = data["access_token"]
    config["refresh_token"] = data["refresh_token"]
    config["expires_at"] = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=data["expires_in"])
    ).isoformat()
    save_config(config)


def authorize(config):
    """Run the full OAuth2 browser flow and return updated config with tokens."""
    client_id = urllib.parse.quote(config["client_id"])
    redirect = urllib.parse.quote(REDIRECT_URI)
    permissions = " ".join(SCOPED_PERMISSIONS)
    scopes = urllib.parse.quote(permissions)
    url = (
        f"{DRCHRONO_BASE}/o/authorize/"
        f"?redirect_uri={redirect}&response_type=code"
        f"&client_id={client_id}&scope={scopes}"
    )

    _CallbackHandler.auth_code = None
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)

    print("Opening browser for DrChrono authorization...")
    webbrowser.open(url)
    print("Waiting for authorization (complete the login in your browser)...")
    server.handle_request()
    server.server_close()

    code = _CallbackHandler.auth_code
    if not code:
        print("Authorization failed or was cancelled.")
        sys.exit(1)

    resp = requests.post(f"{DRCHRONO_BASE}/o/token/", data={
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
    })
    resp.raise_for_status()
    _store_tokens(config, resp.json())
    print("Authorization successful! Tokens saved.\n")
    return config


def refresh_token(config):
    """Refresh an expired access token."""
    resp = requests.post(f"{DRCHRONO_BASE}/o/token/", data={
        "refresh_token": config["refresh_token"],
        "grant_type": "refresh_token",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
    })
    resp.raise_for_status()
    _store_tokens(config, resp.json())
    return config


def ensure_auth(config):
    """Ensure we have a valid access token, refreshing or re-authorizing as needed."""
    if not config.get("access_token"):
        return authorize(config)

    expires_at = datetime.datetime.fromisoformat(config["expires_at"])
    if datetime.datetime.now(datetime.timezone.utc) >= expires_at:
        print("Access token expired, refreshing...")
        try:
            return refresh_token(config)
        except requests.HTTPError:
            print("Refresh failed, re-authorizing...")
            return authorize(config)

    return config


def api_headers(config):
    return {"Authorization": f"Bearer {config['access_token']}"}
