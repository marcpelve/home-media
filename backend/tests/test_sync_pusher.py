"""Tests for ProgressPusher (P-M3b): debounced per-title cloud push.

Uses a tmp SQLite DB (init_db) + a counting LocalFolderBackend wrapper to
verify debounce, flush, and missing-title no-op behavior.
"""

import asyncio
import sqlite3

from app.db import init_db
from app.storage import LocalFolderBackend
from app.sync import ProgressPusher, push_key_for


class CountingBackend(LocalFolderBackend):
    """LocalFolderBackend that counts write_title calls."""
    def __init__(self, root):
        super().__init__(root)
        self.writes = 0
    def write_title(self, key, data):
        self.writes += 1
        return super().write_title(key, data)


def _add_title(db_path, tmdb_id, type_, title) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO titles (tmdb_id, type, title, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (tmdb_id, type_, title),
        )
        conn.commit()
    finally:
        conn.close()


def test_pusher_debounces(tmp_path):
    """Two schedule calls within the delay produce exactly one write."""
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix")

    be = CountingBackend(tmp_path / "store")

    async def run():
        pusher = ProgressPusher(db, lambda: be, delay_s=0.05)
        pusher.schedule("movie:603")
        await asyncio.sleep(0.02)
        pusher.schedule("movie:603")
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert be.writes == 1
    assert be.read_title("movie:603") is not None


def test_pusher_flush(tmp_path):
    """flush() pushes all pending keys immediately and returns the count."""
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix")

    be = CountingBackend(tmp_path / "store")

    async def run():
        # Long delay so the timer won't fire before flush().
        pusher = ProgressPusher(db, lambda: be, delay_s=5.0)
        pusher.schedule("movie:603")
        return await pusher.flush()

    pushed = asyncio.run(run())
    assert pushed == 1
    assert be.read_title("movie:603") is not None


def test_pusher_missing_title_noop(tmp_path):
    """Scheduling a key with no local title row produces no write, no crash."""
    db = tmp_path / "d.db"
    init_db(db)
    # No title row for tmdb_id 999.

    be = CountingBackend(tmp_path / "store")

    async def run():
        pusher = ProgressPusher(db, lambda: be, delay_s=0.05)
        pusher.schedule("movie:999")
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert be.writes == 0


def test_push_key_for(tmp_path):
    """push_key_for returns make_key for an existing title, None otherwise."""
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix")
    _add_title(db, 1396, "tv", "Breaking Bad")

    assert push_key_for(db, 603) == "movie:603"
    assert push_key_for(db, 1396) == "tv:1396"
    assert push_key_for(db, 999) is None
