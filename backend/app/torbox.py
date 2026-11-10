"""Torbox debrid API client with a debrid_state-backed cache.

Wraps the Torbox REST API (base https://api.torbox.app, Bearer auth). Used by
the /sources/resolve router. Repeated resolve calls for the same hash short-
circuit through the debrid_state table so we do not re-hit the API. The API
key comes from Config and is never logged.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx


class TorboxClient:
    """Async Torbox client with a debrid_state cache for resolve results."""

    def __init__(
        self,
        api_key: str,
        db_path: str | Path,
        base_url: str = "https://api.torbox.app",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._db_path = str(db_path)
        self._base_url = base_url
        self._client = client
        self._owns_client = client is None
        self._locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> "TorboxClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- API key (in-app auth) ------------------------------------------------

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

    def set_key(self, key: str) -> None:
        """Store the API key in the settings table (in-app auth)."""
        self._set_setting("torbox_api_key", key.strip())

    def get_key(self) -> str | None:
        """The API key stored in the settings table, if any."""
        return self._get_setting("torbox_api_key")

    def clear_key(self) -> None:
        """Remove the stored API key."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("DELETE FROM settings WHERE key = 'torbox_api_key'")
            conn.commit()
        finally:
            conn.close()

    def _effective_key(self) -> str:
        """Key used for requests: the settings-table key wins over the
        .env-supplied key, so a key saved in-app takes effect immediately."""
        return self.get_key() or self._api_key

    def is_configured(self) -> bool:
        return bool(self._effective_key())

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._effective_key()}"}

    async def validate(self, key: str) -> bool:
        """Check that an API key is accepted by Torbox (GET /mylist)."""
        key = (key or "").strip()
        if not key:
            return False
        created = self._client is None
        if created:
            await self.__aenter__()
        client = self._client
        assert client is not None
        try:
            resp = await client.get(
                "/v1/api/torrents/mylist",
                headers={"Authorization": f"Bearer {key}"},
            )
            return resp.status_code == 200
        finally:
            if created:
                await client.aclose()

    # -- debrid_state cache --------------------------------------------------

    def _state_get(self, hash: str) -> sqlite3.Row | None:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT * FROM debrid_state WHERE hash = ?", (hash,)
            ).fetchone()
        finally:
            conn.close()

    def _state_upsert(self, hash: str, cached: int, torbox_added: int, note: str | None) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO debrid_state (hash, cached, torbox_added, added_at, note) "
                "VALUES (?, ?, ?, datetime('now'), ?) "
                "ON CONFLICT(hash) DO UPDATE SET "
                "cached = excluded.cached, torbox_added = excluded.torbox_added, "
                "note = excluded.note",
                (hash, cached, torbox_added, note),
            )
            conn.commit()
        finally:
            conn.close()

    # -- core fetch ----------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None):
        """GET a Torbox path; return the `data` field of the JSON envelope."""
        client = self._client
        resp = await client.get(path, params=params, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json().get("data")

    # -- public API methods --------------------------------------------------

    async def check_cached(self, hash: str) -> bool:
        """Is this hash in the debrid? Short-circuits via debrid_state."""
        row = self._state_get(hash)
        if row is not None and row["cached"] is not None:
            return bool(row["cached"])

        data = await self._get("/v1/api/torrents/checkcached", params={"hash": hash})
        cached = bool(data)
        self._state_upsert(hash, 1 if cached else 0, 0, None)
        return cached

    async def find_in_account(self, hash: str) -> dict | None:
        """Find a torrent in our account by hash (case-insensitive)."""
        data = await self._get("/v1/api/torrents/mylist")
        if not isinstance(data, list):
            return None
        h = str(hash).lower()
        for t in data:
            if isinstance(t, dict) and str(t.get("hash", "")).lower() == h:
                return t
        return None

    async def add_to_account(self, magnet: str) -> dict:
        """Add a torrent to the account via its magnet link.

        Field name `magnet` verified against the live API.
        """
        client = self._client
        resp = await client.post(
            "/v1/api/torrents/createtorrent",
            data={"magnet": magnet},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json().get("data")
        return data if isinstance(data, dict) else {}

    async def request_download(self, torrent_id: int, file_id: int) -> str | None:
        """Get a direct HTTP download URL for a specific file."""
        client = self._client
        resp = await client.get(
            "/v1/api/torrents/requestdl",
            params={
                "token": self._effective_key(),
                "torrent_id": torrent_id,
                "file_id": file_id,
            },
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return self._extract_url(resp.json().get("data"))

    @staticmethod
    def _extract_url(data) -> str | None:
        """requestdl `data` is underspecified; handle str/dict/list defensively."""
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for k in ("url", "download_url"):
                if isinstance(data.get(k), str):
                    return data[k]
            for v in data.values():
                if isinstance(v, str) and v.startswith("http"):
                    return v
        if isinstance(data, list):
            for v in data:
                if isinstance(v, str) and v.startswith("http"):
                    return v
        return None

    async def create_stream(self, torrent_id: int, file_id: int) -> str | None:
        """Get a presigned token for streaming a file."""
        data = await self._get(
            "/v1/api/stream/createstream",
            params={"id": torrent_id, "file_id": file_id},
        )
        if isinstance(data, dict):
            return data.get("token") or data.get("presigned_token")
        if isinstance(data, str):
            return data
        return None

    async def get_stream_data(self, presigned_token: str) -> dict:
        """Resolve a presigned token to stream data."""
        data = await self._get(
            "/v1/api/stream/getstreamdata",
            params={"token": self._effective_key(), "presigned_token": presigned_token},
        )
        return data if isinstance(data, dict) else {}

    # -- high-level resolve --------------------------------------------------

    async def resolve(self, hash: str) -> dict:
        """Resolve a hash to debrid status + our account's torrent/files."""
        cached = await self.check_cached(hash)
        result = {"hash": hash, "cached": cached, "torrent_id": None, "files": []}
        if cached:
            torrent = await self.find_in_account(hash)
            if torrent:
                result["torrent_id"] = torrent.get("id")
                result["files"] = [
                    {
                        "file_id": f.get("id"),
                        "name": f.get("name"),
                        "size": f.get("size"),
                        "mimetype": f.get("mimetype"),
                    }
                    for f in (torrent.get("files") or [])
                    if isinstance(f, dict)
                ]
        return result
