"""Google Drive OAuth (browser flow with local redirect) + token care.

The desktop server is bound to 127.0.0.1:8790, so the OAuth redirect can be
an endpoint of this app. access_type=offline + prompt=consent make Google
issue a refresh token even for an already-authorized account. Tokens live
in the settings table, never in logs.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from urllib.parse import urlencode

import httpx

from app.storage import StorageBackend, create_backend

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://127.0.0.1:8790/drive/callback"

# Refresh a touch before expiry so a call can't straddle the boundary.
_REFRESH_SKEW_S = 60.0


class DriveAuthError(Exception):
    """Raised when the OAuth connect or token exchange fails."""


# ---------------------------------------------------------------------------
# Settings helpers (same shape as trakt.py's)
# ---------------------------------------------------------------------------

def _get_setting(db_path: str, key: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_setting(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def start_connect(db_path: str, client_id: str, client_secret: str) -> dict:
    """Begin the browser OAuth flow; return the authorization URL to open."""
    state = secrets.token_urlsafe(16)
    _set_setting(db_path, "drive_oauth_state", state)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        # WHY offline + consent: even if the user already authorized this
        # client, Google will re-prompt and issue a refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    return {"authorization_url": url, "redirect_uri": REDIRECT_URI}


def complete_connect(
    db_path: str,
    client_id: str,
    client_secret: str,
    code: str,
    state: str,
    client: httpx.Client | None = None,
) -> dict:
    """Exchange the authorization code for tokens and store them."""
    pending = _get_setting(db_path, "drive_oauth_state")
    # compare_digest prevents timing-side-channel on the state check.
    if not pending or not secrets.compare_digest(pending, state):
        raise DriveAuthError("state mismatch")

    tokens = _token_request(client, {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    _store_tokens(db_path, tokens)
    # Clear the one-time state so a replay of the callback is rejected.
    _set_setting(db_path, "drive_oauth_state", "")
    return {"connected": True}


def _token_request(client: httpx.Client | None, form: dict) -> dict:
    """POST the token endpoint; return the JSON response.

    *client* is injectable for tests (httpx.MockTransport).
    """
    if client is None:
        with httpx.Client() as c:
            resp = c.post(TOKEN_URL, data=form)
    else:
        resp = client.post(TOKEN_URL, data=form)
    if resp.status_code != 200:
        raise DriveAuthError(f"token endpoint {resp.status_code}")
    return resp.json()


def _store_tokens(db_path: str, tokens: dict) -> None:
    """Persist OAuth token response into the settings table."""
    _set_setting(db_path, "drive_access_token", tokens["access_token"])
    # Google rotates the refresh token; a response without one keeps the
    # existing stored refresh token.
    if "refresh_token" in tokens:
        _set_setting(db_path, "drive_refresh_token", tokens["refresh_token"])
    expires_in = tokens.get("expires_in", 3600)
    _set_setting(
        db_path, "drive_access_expires_at", str(time.time() + expires_in)
    )


# ---------------------------------------------------------------------------
# Token care (refresh on expiry)
# ---------------------------------------------------------------------------

def get_valid_access_token(
    db_path: str, client: httpx.Client | None = None
) -> str | None:
    """Return a usable access token, refreshing if within 60s of expiry.

    Returns None when Drive is not connected (no tokens stored).
    """
    access = _get_setting(db_path, "drive_access_token")
    if not access:
        return None

    raw = _get_setting(db_path, "drive_access_expires_at")
    try:
        expires_at = float(raw) if raw else 0.0
    except ValueError:
        expires_at = 0.0

    # Token still valid (not within the skew window).
    if time.time() < expires_at - _REFRESH_SKEW_S:
        return access

    # Need a refresh: requires a stored refresh token + client credentials.
    refresh = _get_setting(db_path, "drive_refresh_token")
    client_id = _get_setting(db_path, "drive_client_id")
    client_secret = _get_setting(db_path, "drive_client_secret")
    if not (refresh and client_id and client_secret):
        return None

    tokens = _token_request(client, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    })
    _store_tokens(db_path, tokens)
    return tokens["access_token"]


# ---------------------------------------------------------------------------
# Backend factory (valid token per use)
# ---------------------------------------------------------------------------

def make_cloud_backend(
    db_path: str, client: httpx.Client | None = None
) -> StorageBackend | None:
    """Build a StorageBackend with a VALID access token.

    WHY per-use: a cached access token expires after ~1h, so every sync
    operation goes through this factory to get a fresh (or refreshed) token.
    Returns None when Drive is not connected or configuration is incomplete.
    """
    settings: dict = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    settings = {row["key"]: row["value"] for row in rows}

    if settings.get("storage_backend") == "drive":
        token = get_valid_access_token(db_path, client)
        if token is None:
            return None
        settings["drive_access_token"] = token

    try:
        return create_backend(settings)
    except (ValueError, KeyError):
        return None
