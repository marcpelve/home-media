"""Download manager: queues debrid file downloads to local disk.

Tracks each download in the `downloads` table and resumes partial files via
HTTP Range. Runs downloads as background asyncio tasks. The destination
directory defaults to Config.download_dir (or ./downloads).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.db import get_db


class DownloadManager:
    """Manages debrid file downloads with progress + Range resume."""

    def __init__(
        self,
        db_path: str | Path,
        dest_dir: str | Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._dest_dir = Path(dest_dir) if dest_dir else Path("downloads")
        self._client = client
        self._owns_client = client is None
        self._tasks: dict[int, asyncio.Task] = {}

    async def __aenter__(self) -> "DownloadManager":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        self._dest_dir.mkdir(parents=True, exist_ok=True)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- db helpers ----------------------------------------------------------

    def _db_update(self, dl_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [dl_id]
        conn = get_db(self._db_path)
        try:
            conn.execute(f"UPDATE downloads SET {sets} WHERE id = ?", vals)
            conn.commit()
        finally:
            conn.close()

    # -- public API ----------------------------------------------------------

    def start(
        self,
        hash: str,
        file_url: str,
        title_id: int = 0,
        episode: int | None = None,
        size_bytes: int | None = None,
        filename: str | None = None,
    ) -> int:
        """Queue a download; returns the downloads.id. Runs in a background task."""
        # Dedupe: an in-progress download for the same (title, episode) is
        # returned instead of starting a duplicate.
        if title_id:
            conn = get_db(self._db_path)
            try:
                existing = conn.execute(
                    "SELECT id FROM downloads WHERE title_id = ? AND episode IS ? "
                    "AND status IN ('queued', 'downloading')",
                    (title_id, episode),
                ).fetchone()
                if existing is not None:
                    return existing[0]
            finally:
                conn.close()
        conn = get_db(self._db_path)
        try:
            cur = conn.execute(
                "INSERT INTO downloads (title_id, episode, hash, size_bytes, status, progress) "
                "VALUES (?, ?, ?, ?, 'queued', 0)",
                (title_id, episode, hash, size_bytes),
            )
            dl_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        if dl_id is None:
            raise RuntimeError("download insert failed")
        self._tasks[dl_id] = asyncio.create_task(
            self._download(dl_id, file_url, size_bytes, filename)
        )
        return dl_id

    async def wait_all(self) -> None:
        """Await all in-flight download tasks (used by tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    def list(self) -> list[dict]:
        conn = get_db(self._db_path)
        try:
            rows = conn.execute("SELECT * FROM downloads ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def cancel(self, dl_id: int) -> None:
        t = self._tasks.get(dl_id)
        if t:
            t.cancel()
        self._db_update(dl_id, status="failed")

    def remove(self, dl_id: int) -> bool:
        t = self._tasks.get(dl_id)
        if t:
            t.cancel()
        conn = get_db(self._db_path)
        try:
            row = conn.execute(
                "SELECT file_path FROM downloads WHERE id = ?", (dl_id,)
            ).fetchone()
            if row is None:
                return False
            if row["file_path"] and Path(row["file_path"]).exists():
                Path(row["file_path"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM downloads WHERE id = ?", (dl_id,))
            conn.commit()
        finally:
            conn.close()
        return True

    def _ids_for(self, title_id: int, episode: int | None) -> list[int]:
        conn = get_db(self._db_path)
        try:
            if episode is None:
                rows = conn.execute(
                    "SELECT id FROM downloads WHERE title_id = ?", (title_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM downloads WHERE title_id = ? AND episode = ?",
                    (title_id, episode),
                ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def remove_for_title(self, title_id: int) -> int:
        """Cancel + delete every download for a title. Returns count removed."""
        ids = self._ids_for(title_id, None)
        for i in ids:
            self.remove(i)
        return len(ids)

    def remove_for_episode(self, title_id: int, episode: int) -> int:
        """Cancel + delete every download for one episode. Returns count removed."""
        ids = self._ids_for(title_id, episode)
        for i in ids:
            self.remove(i)
        return len(ids)

    # -- worker --------------------------------------------------------------

    @staticmethod
    def _safe_name(filename: str | None, file_url: str) -> str:
        if filename:
            return filename
        name = Path(urlparse(file_url).path).name
        return name or "download"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    async def _download(
        self, dl_id: int, file_url: str, size_bytes: int | None, filename: str | None
    ) -> None:
        dest = self._dest_dir / self._safe_name(filename, file_url)
        offset = dest.stat().st_size if dest.exists() else 0
        headers = {}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        self._db_update(dl_id, status="downloading", file_path=str(dest))
        try:
            async with self._client.stream("GET", file_url, headers=headers) as resp:
                mode = "wb"
                if resp.status_code == 206:
                    mode = "ab"
                elif resp.status_code != 200:
                    resp.raise_for_status()

                total = None
                cr = resp.headers.get("content-range")
                if cr and "/" in cr:
                    total = int(cr.rsplit("/", 1)[1])
                if total is None and size_bytes:
                    total = size_bytes
                if total is None:
                    cl = resp.headers.get("content-length")
                    total = int(cl) if cl else None
                written = offset if resp.status_code == 206 else 0

                with open(dest, mode) as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
                        written += len(chunk)
                        if total:
                            self._db_update(dl_id, progress=min(1.0, written / total))

            self._db_update(
                dl_id, status="done", progress=1.0, completed_at=self._now()
            )
        except Exception:
            self._db_update(dl_id, status="failed")
        finally:
            self._tasks.pop(dl_id, None)
