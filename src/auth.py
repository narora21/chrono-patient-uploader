"""OAuth2 authorization flow for DrChrono API."""

import sys
import threading
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests

from src.credential_store import get as cred_get, set_many as cred_set_many

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
    """Persist refresh_token to keyring; keep access_token in session cache only."""
    cred_set_many({
        "refresh_token": data["refresh_token"],
        "access_token": data["access_token"],
    })
    config["access_token"] = data["access_token"]
    config["refresh_token"] = data["refresh_token"]


def authorize(config):
    """Run the full OAuth2 browser flow and return updated config with tokens."""
    client_id_val = cred_get("client_id") or config["client_id"]
    client_secret_val = cred_get("client_secret") or config["client_secret"]

    client_id_encoded = urllib.parse.quote(client_id_val)
    redirect = urllib.parse.quote(REDIRECT_URI)
    permissions = " ".join(SCOPED_PERMISSIONS)
    scopes = urllib.parse.quote(permissions)
    url = (
        f"{DRCHRONO_BASE}/o/authorize/"
        f"?redirect_uri={redirect}&response_type=code"
        f"&client_id={client_id_encoded}&scope={scopes}"
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
        "client_id": client_id_val,
        "client_secret": client_secret_val,
    })
    resp.raise_for_status()
    _store_tokens(config, resp.json())
    print("Authorization successful! Tokens saved.\n")
    return config


def refresh_token(config):
    """Refresh an expired access token."""
    resp = requests.post(f"{DRCHRONO_BASE}/o/token/", data={
        "refresh_token": cred_get("refresh_token") or config["refresh_token"],
        "grant_type": "refresh_token",
        "client_id": cred_get("client_id") or config["client_id"],
        "client_secret": cred_get("client_secret") or config["client_secret"],
    })
    resp.raise_for_status()
    _store_tokens(config, resp.json())
    return config


def ensure_auth(config):
    """Ensure we have a valid access token by refreshing or re-authorizing."""
    rt = cred_get("refresh_token") or config.get("refresh_token")
    if rt:
        try:
            return refresh_token(config)
        except requests.HTTPError:
            print("Token refresh failed, re-authorizing...")
    return authorize(config)


def api_headers(config):
    token = cred_get("access_token") or config.get("access_token")
    return {"Authorization": f"Bearer {token}"}
