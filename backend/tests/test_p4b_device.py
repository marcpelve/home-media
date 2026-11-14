"""Device stamping + one-time 'default' backfill (P-M4b).

Local observations (progress, complete, fresh titles) are always stamped
with this machine's device_id from settings; the UPDATE path of upsert_title
leaves device alone (the LWW merge owns it for existing titles). Legacy rows
carrying the 'default' placeholder are backfilled to the real device_id once
the user sets one.
"""

import asyncio
import os
import sqlite3

from app.db import _migrate_default_device, init_db
from app.library import upsert_title
from app.storage import LocalFolderBackend
from app.sync import ProgressPusher
from app.watch import upsert_complete, upsert_progress


class CountingBackend(LocalFolderBackend):
    """LocalFolderBackend that counts write_title calls."""
    def __init__(self, root):
        super().__init__(root)
        self.writes = 0
    def write_title(self, key, data):
        self.writes += 1
        return super().write_title(key, data)


def _set_device(db_path, device_id: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('device_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (device_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _device(db_path, table: str, where: str, params: tuple) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            f"SELECT device FROM {table} WHERE {where}", params
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_upsert_progress_stamps_device(tmp_path):
    """A fresh progress row carries this machine's device_id."""
    db = tmp_path / "d.db"
    init_db(db)
    _set_device(db, "pc-a")
    upsert_progress(db, 603, 120000, 600000)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "pc-a"


def test_upsert_progress_update_stamps_device(tmp_path):
    """The update path re-stamps device (e.g. after a cloud pull set it)."""
    db = tmp_path / "d.db"
    init_db(db)
    _set_device(db, "pc-a")
    upsert_progress(db, 603, 120000, 600000)
    # Simulate a cloud pull having written another machine's device.
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE watch_progress SET device = 'pc-b' WHERE title_id = 603")
    conn.commit()
    conn.close()
    upsert_progress(db, 603, 180000, 600000)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "pc-a"


def test_upsert_progress_device_default_username(tmp_path, monkeypatch):
    """No device_id setting: stamps the Windows USERNAME when present."""
    db = tmp_path / "d.db"
    init_db(db)
    monkeypatch.setenv("USERNAME", "marcp")
    upsert_progress(db, 603, 120000, 600000)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "marcp"


def test_upsert_progress_device_default_fallback(tmp_path, monkeypatch):
    """No device_id setting and no USERNAME env: falls back to 'default'."""
    db = tmp_path / "d.db"
    init_db(db)
    monkeypatch.delenv("USERNAME", raising=False)
    upsert_progress(db, 603, 120000, 600000)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "default"


def test_upsert_progress_device_explicit_wins(tmp_path, monkeypatch):
    """An explicit device_id setting beats the username fallback."""
    db = tmp_path / "d.db"
    init_db(db)
    _set_device(db, "pc-a")
    monkeypatch.setenv("USERNAME", "marcp")
    upsert_progress(db, 603, 120000, 600000)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "pc-a"


def test_upsert_complete_stamps_device(tmp_path):
    """Fresh and update rows from upsert_complete carry this machine's id."""
    db = tmp_path / "d.db"
    init_db(db)
    _set_device(db, "pc-a")
    upsert_complete(db, 603)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "pc-a"
    # Second call takes the UPDATE path; it re-stamps too.
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE watch_progress SET device = 'pc-b' WHERE title_id = 603")
    conn.commit()
    conn.close()
    upsert_complete(db, 603)
    assert _device(db, "watch_progress", "title_id = ?", (603,)) == "pc-a"


def test_upsert_title_stamps_device(tmp_path):
    """A fresh title row is stamped; the update path leaves device unchanged."""
    db = tmp_path / "d.db"
    init_db(db)
    _set_device(db, "pc-a")
    meta = {"_type": "movie", "title": "The Matrix", "release_date": "1999-03-31"}
    upsert_title(db, 603, meta)
    assert _device(db, "titles", "tmdb_id = ?", (603,)) == "pc-a"
    # Device is LWW-owned for existing titles: tamper, then upsert again.
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE titles SET device = 'pc-b' WHERE tmdb_id = 603")
    conn.commit()
    conn.close()
    upsert_title(db, 603, meta)
    assert _device(db, "titles", "tmdb_id = ?", (603,)) == "pc-b"


def test_default_device_backfill(tmp_path):
    """'default'/NULL rows adopt the real device_id; re-running is a no-op."""
    db = tmp_path / "d.db"
    init_db(db)
    # Pre-seed: one placeholder row + one NULL row in each table.
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO settings (key, value) VALUES ('device_id', 'pc-a')")
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title, device) "
        "VALUES (603, 'movie', 'A', 'default')"
    )
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (604, 'movie', 'B')"
    )
    conn.execute(
        "INSERT INTO watch_progress (title_id, device) VALUES (603, 'default')"
    )
    conn.execute("INSERT INTO watch_progress (title_id) VALUES (604)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db))
    _migrate_default_device(conn)
    conn.commit()
    conn.close()
    for tid in (603, 604):
        assert _device(db, "titles", "tmdb_id = ?", (tid,)) == "pc-a"
        assert _device(db, "watch_progress", "title_id = ?", (tid,)) == "pc-a"

    # Idempotent: a second run matches no rows and changes nothing.
    conn = sqlite3.connect(str(db))
    _migrate_default_device(conn)
    conn.commit()
    conn.close()
    for tid in (603, 604):
        assert _device(db, "titles", "tmdb_id = ?", (tid,)) == "pc-a"
        assert _device(db, "watch_progress", "title_id = ?", (tid,)) == "pc-a"


def test_pusher_factory_none(tmp_path):
    """Factory returning None: schedule + wait -> no exception, no writes."""
    db = tmp_path / "d.db"
    init_db(db)

    async def run():
        pusher = ProgressPusher(db, lambda: None, delay_s=0.05)
        pusher.schedule("movie:603")
        await asyncio.sleep(0.2)

    asyncio.run(run())


def test_pusher_factory_debounces(tmp_path):
    """Factory returning a counting backend: 3 schedules -> exactly 1 write."""
    db = tmp_path / "d.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title, updated_at) "
        "VALUES (603, 'movie', 'The Matrix', datetime('now'))"
    )
    conn.commit()
    conn.close()

    be = CountingBackend(tmp_path / "store")

    async def run():
        pusher = ProgressPusher(db, lambda: be, delay_s=0.05)
        pusher.schedule("movie:603")
        await asyncio.sleep(0.02)
        pusher.schedule("movie:603")
        pusher.schedule("movie:603")
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert be.writes == 1
