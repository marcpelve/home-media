"""Trakt API client: Device Code Flow auth + watched/progress data calls.

Auth lives on https://auth.trakt.tv (OAuth via PKCE - Trakt deprecated the
client_secret for user sign-in); data lives on https://api.trakt.tv. Uses the
Device Code Flow with PKCE: request a device code carrying a code_challenge, the
user enters it in a browser, we poll for the token carrying the code_verifier.
Refresh tokens are SINGLE-USE, so each refresh rotates the stored pair. Tokens
are stored per machine in the local `settings` table.

Limits honored: 500 authed GET/5min, 1 authed POST/sec, 429 Retry-After with
bounded backoff + jitter. The client id is never logged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import random
import secrets
import sqlite3
import time
from pathlib import Path

import httpx

AUTH_HOST = "https://auth.trakt.tv"
API_HOST = "https://api.trakt.tv"
SCOPES = "scrobble:post history:read watchlist:read"

# Trakt allows 1 authed POST/sec; keep a floor between writes.
_POST_MIN_INTERVAL = 1.0
# Bounded 429 backoff (never loop-retry).
_MAX_429_RETRIES = 3
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 30.0
# Refresh a touch before expiry so a call can't straddle the boundary.
_REFRESH_SKEW_S = 300.0
# OAuth endpoints expect form-encoded bodies, not the client's JSON default.
_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


def _pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the PKCE device flow.

    The verifier is 64 url-safe chars (Trakt wants 43-128); the challenge is
    base64url(sha256(verifier)) with padding stripped (method S256).
    """
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class TraktAuthError(Exception):
    """Raised when not authenticated, or the device-code flow fails."""


class TraktClient:
    """Async Trakt client with Device Code Flow + token rotation."""

    def __init__(
        self,
        client_id: str,
        db_path: str | Path,
        api_host: str = API_HOST,
        auth_host: str = AUTH_HOST,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._db_path = str(db_path)
        self._api_host = api_host
        self._auth_host = auth_host
        self._client = client
        self._owns_client = client is None
        self._token_lock = asyncio.Lock()
        self._post_lock = asyncio.Lock()
        self._last_post = 0.0
        # Pending device-code flow (single machine, short-lived).
        self._pending: dict | None = None
        self._pending_lock = asyncio.Lock()

    async def __aenter__(self) -> "TraktClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api_host,
                headers={
                    "Content-Type": "application/json",
                    "Trakt-Api-Version": "2",
                    "Trakt-Api-Key": self._client_id,
                },
                timeout=20.0,
            )
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- token storage (local `settings` table) ------------------------------

    def _set_setting(self, key: str, value: str) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_setting(self, key: str) -> str | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def save_tokens(
        self, username: str, access_token: str, refresh_token: str,
        expires_at: float,
    ) -> None:
        self._set_setting("trakt_username", username)
        self._set_setting("trakt_access_token", access_token)
        self._set_setting("trakt_refresh_token", refresh_token)
        self._set_setting("trakt_access_expires_at", str(expires_at))

    def load_tokens(self) -> dict | None:
        username = self._get_setting("trakt_username")
        access = self._get_setting("trakt_access_token")
        refresh = self._get_setting("trakt_refresh_token")
        if not (username and access and refresh):
            return None
        raw = self._get_setting("trakt_access_expires_at")
        try:
            expires_at = float(raw) if raw else 0.0
        except ValueError:
            expires_at = 0.0
        return {
            "username": username,
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": expires_at,
        }

    def is_authenticated(self) -> bool:
        return self.load_tokens() is not None

    def _username(self) -> str:
        tokens = self.load_tokens()
        if tokens is None:
            raise TraktAuthError("not authenticated")
        return tokens["username"]

    # -- access token (single-use refresh rotation) --------------------------

    async def _refresh_tokens(self, refresh_token: str) -> dict:
        client = self._client
        resp = await client.post(
            f"{self._auth_host}/oauth/token",
            data={
                "client_id": self._client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers=_FORM_HEADERS,
        )
        if resp.status_code != 200:
            raise TraktAuthError(f"refresh failed: {resp.status_code}")
        return resp.json()

    async def get_access_token(self) -> str:
        """Return a valid access token, rotating on refresh (single-use)."""
        async with self._token_lock:
            tokens = self.load_tokens()
            if tokens is None:
                raise TraktAuthError("not authenticated")
            if time.time() >= tokens["expires_at"] - _REFRESH_SKEW_S:
                data = await self._refresh_tokens(tokens["refresh_token"])
                self.save_tokens(
                    tokens["username"],
                    data["access_token"],
                    data["refresh_token"],
                    time.time() + data.get("expires_in", 604800),
                )
                return data["access_token"]
            return tokens["access_token"]

    # -- device code flow ----------------------------------------------------

    async def start_device_login(self) -> dict:
        """Request a device code; the user enters it at the verification URL."""
        client = self._client
        verifier, challenge = _pkce()
        resp = await client.post(
            f"{self._auth_host}/oauth/device/code",
            json={
                "client_id": self._client_id,
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._pending = {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "code_verifier": verifier,
            "verification_url": data.get(
                "verification_url", f"{self._auth_host}/activate"
            ),
            "expires_in": data.get("expires_in", 900),
            "started_at": time.time(),
        }
        return {
            "user_code": data["user_code"],
            "verification_url": self._pending["verification_url"],
            "expires_in": self._pending["expires_in"],
        }

    async def poll_pending(self) -> dict:
        """Attempt one poll of the pending device-code flow.

        Returns {status: none|pending|authorized|expired}. On authorized, the
        tokens are saved and the pending state cleared.
        """
        async with self._pending_lock:
            if self._pending is None:
                return {"status": "none"}
            p = self._pending
            if time.time() - p["started_at"] > p["expires_in"]:
                self._pending = None
                return {"status": "expired"}
            client = self._client
            resp = await client.post(
                f"{self._auth_host}/oauth/device/token",
                json={
                    "client_id": self._client_id,
                    "code": p["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "code_verifier": p["code_verifier"],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                expires_at = time.time() + data.get("expires_in", 604800)
                username = data.get("trakt", {}).get("username")
                self.save_tokens(
                    username or "unknown",
                    data["access_token"],
                    data["refresh_token"],
                    expires_at,
                )
                # The token response carries no username; fetch it now that the
                # token is stored so status/re-auth show the real account.
                if not username:
                    try:
                        username = (await self._authed_get("/users/me")).json().get(
                            "username"
                        )
                    except Exception:
                        username = None
                    if username:
                        self.save_tokens(
                            username,
                            data["access_token"],
                            data["refresh_token"],
                            expires_at,
                        )
                self._pending = None
                return {
                    "status": "authorized",
                    "username": username,
                }
            if resp.status_code == 400:
                # Trakt sends a JSON error, but can return an empty body during
                # the auth transition; never let a non-JSON body crash the poll.
                try:
                    err = resp.json().get("error", "pending")
                except (ValueError, TypeError):
                    err = "pending"
                if err == "expired_token":
                    self._pending = None
                    return {"status": "expired"}
                return {"status": "pending"}
            if resp.status_code == 429:
                # Rate-limited; the poll loop spaces the next attempt.
                return {"status": "pending"}
            resp.raise_for_status()
            return {"status": "pending"}

    # -- data endpoints (api.trakt.tv) ---------------------------------------

    async def _request_with_retry(self, method: str, path: str, **kwargs):
        client = self._client
        attempt = 0
        while True:
            resp = await client.request(method, path, **kwargs)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after is not None
                    else min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)
                )
                delay += random.uniform(0, 1.0)
                attempt += 1
                if attempt > _MAX_429_RETRIES:
                    resp.raise_for_status()
                await asyncio.sleep(delay)
                continue
            return resp

    async def _authed_get(self, path: str, params: dict | None = None):
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Trakt-Api-Version": "2",
            "Trakt-Api-Key": self._client_id,
        }
        return await self._request_with_retry("GET", path, headers=headers, params=params)

    async def _authed_post(self, path: str, json_body):
        # Enforce the 1 POST/sec floor (serialized with the token lock order).
        async with self._post_lock:
            wait = self._last_post + _POST_MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_post = time.monotonic()
        token = await self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Trakt-Api-Version": "2",
            "Trakt-Api-Key": self._client_id,
        }
        return await self._request_with_retry("POST", path, headers=headers, json=json_body)

    async def mark_watched(self, item: dict) -> list:
        """Scrobble one item (movie or episode) to history."""
        username = self._username()
        resp = await self._authed_post(f"/users/{username}/items/history", [item])
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    async def get_watched_movies(self) -> list:
        username = self._username()
        resp = await self._authed_get(
            f"/users/{username}/movies/watched", {"extended": "full"}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_history(self) -> list:
        """The user's recent watch history (movies + episodes), newest first.

        Uses /users/{u}/history, which works with history:read (no
        scrobble:post needed, unlike /progress/watched).
        """
        username = self._username()
        resp = await self._authed_get(
            f"/users/{username}/history", {"extended": "full", "limit": 100}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_watched_episodes(self, show_tmdb_id: int) -> list:
        username = self._username()
        resp = await self._authed_get(
            f"/users/{username}/shows/{show_tmdb_id}/episodes/watched",
            {"extended": "full"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_progress(self) -> list:
        """Continue-watching (in-progress) items."""
        username = self._username()
        resp = await self._authed_get(
            f"/users/{username}/progress/watched", {"extended": "full"}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_watchlist(self) -> list:
        username = self._username()
        resp = await self._authed_get(
            f"/users/{username}/watchlist", {"extended": "full"}
        )
        resp.raise_for_status()
        return resp.json()

    async def get_recommendations(self) -> dict:
        """Recommendations from watch history (movies + shows).

        Trakt's /recommendations/* items are keyed by "movie" or "show" with
        the TMDB id under ids.tmdb. Any failure (not authed, network, 404)
        yields an empty list so the home row simply omits itself.
        """
        import asyncio as _aio

        async def _rec(path: str) -> list:
            try:
                resp = await self._authed_get(path)
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else []
            except Exception:
                return []

        movie, show = await _aio.gather(
            _rec("/recommendations/movie"),
            _rec("/recommendations/show"),
        )
        return {"movie": movie, "show": show}

    async def get_username(self) -> str | None:
        """Fetch the authenticated username from /users/me (None on failure)."""
        try:
            me = (await self._authed_get("/users/me")).json()
            return me.get("username")
        except Exception:
            return None
