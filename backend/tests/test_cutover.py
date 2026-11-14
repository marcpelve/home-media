"""Cutover verification tests (P-M6): prove the new sync path is safe.

1. OFFLINE: local watch-state writes work with zero cloud involvement;
   when the cloud is back, the next sync pushes them.
2. IMPORT -> CLOUD: a Trakt one-time import lands rows in the DB, and
   the cloud sync then propagates them to the storage backend.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import httpx

from app.db import init_db
from app.storage import LocalFolderBackend, make_key
from app.sync import ProgressPusher, import_from_trakt, sync_all_cloud
from app.trakt import TraktClient
from app.watch import upsert_complete, upsert_progress


MOCK_ID = "test_client_id"
USER = "raymond"
FRESH = time.time() + 100000  # far-future expiry: no refresh needed


def _conn(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _add_title(db_path, tmdb_id, type_, title, updated_at, **kw) -> None:
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO titles (tmdb_id, type, title, updated_at, poster_path, "
            "backdrop_path, overview, imdb_id, genres, device) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tmdb_id,
                type_,
                title,
                updated_at,
                kw.get("poster_path"),
                kw.get("backdrop_path"),
                kw.get("overview"),
                kw.get("imdb_id"),
                json.dumps(kw["genres"]) if kw.get("genres") else None,
                kw.get("device"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 1: OFFLINE writes persist, then push when back online
# ---------------------------------------------------------------------------


def test_offline_writes_persist_then_push_when_back(tmp_path: Path):
    db = tmp_path / "d.db"
    init_db(db)

    # Create two titles directly (the way test_sync_cloud._add_title does).
    _add_title(db, 603, "movie", "The Matrix", "2026-01-01 10:00:00")
    _add_title(db, 704, "movie", "Inception", "2026-01-01 10:00:00")

    # --- OFFLINE phase: no backend involved ---
    # upsert_progress on title 603 (in-progress, not completed).
    upsert_progress(db, 603, position_ms=12345, duration_ms=90000)
    # upsert_complete on title 704 (mark watched).
    upsert_complete(db, 704)

    # Both rows must be in watch_progress with correct flags + updated_at.
    conn = _conn(db)
    row603 = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    row704 = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 704"
    ).fetchone()
    conn.close()
    assert row603 is not None
    assert row603["completed"] == 0
    assert row603["position_ms"] == 12345
    assert row603["duration_ms"] == 90000
    assert row603["updated_at"] is not None
    assert row704 is not None
    assert row704["completed"] == 1
    assert row704["updated_at"] is not None

    # ProgressPusher with a factory that returns None (disconnected case):
    # must survive schedule() + flush() without exception, DB unchanged.
    # Constructed the way main.py does it (db_path, factory, delay_s).
    async def pusher_offline():
        pusher = ProgressPusher(db, lambda: None, delay_s=30.0)
        key = make_key("movie", 603)
        pusher.schedule(key)
        return await pusher.flush()

    flushed = asyncio.run(pusher_offline())
    assert flushed == 0  # not connected: nothing to flush

    # DB rows unchanged after the no-op pusher cycle.
    conn = _conn(db)
    row603_after = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    assert row603_after["position_ms"] == 12345
    assert row603_after["completed"] == 0

    # --- ONLINE phase: build a real backend and sync ---
    be = LocalFolderBackend(tmp_path / "store")
    result = sync_all_cloud(be, db)
    assert result["error"] is None
    assert result["pushed"] >= 2

    # Read the docs back and verify the pushed watch state.
    keys = be.list_titles()
    assert "movie:603" in keys
    assert "movie:704" in keys

    doc603 = be.read_title("movie:603")
    assert doc603 is not None
    assert doc603["watch"][0]["position_ms"] == 12345
    assert doc603["watch"][0]["completed"] == 0

    doc704 = be.read_title("movie:704")
    assert doc704 is not None
    assert doc704["watch"][0]["completed"] == 1


# ---------------------------------------------------------------------------
# Test 2: Trakt import feeds the cloud sync
# ---------------------------------------------------------------------------


def _make_handler(history_payload: list):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.trakt.tv":
            if request.url.path.endswith("/history"):
                return httpx.Response(200, json=history_payload)
        return httpx.Response(404, json={})
    return handler


def test_import_feeds_cloud(tmp_path: Path):
    db = tmp_path / "d.db"
    init_db(db)

    # One movie history item: tmdb 603, played_at "2025-06-01T12:00:00Z".
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 603}, "title": "The Matrix", "year": 1999},
        }
    ]

    transport = httpx.MockTransport(_make_handler(history))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=str(db), client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result["imported"] == 1
    assert result["titles_created"] == 1
    assert result["error"] is None

    # Cloud sync propagates the imported row to the storage backend.
    be = LocalFolderBackend(tmp_path / "store")
    sync_result = sync_all_cloud(be, db)
    assert sync_result["pushed"] == 1
    assert sync_result["error"] is None

    # Read the doc back: the title doc exists, watch state shows completed,
    # and completed_at is the played_at string VERBATIM (provenance survives).
    key = make_key("movie", 603)
    doc = be.read_title(key)
    assert doc is not None
    assert doc["watch"][0]["completed"] == 1
    assert doc["watch"][0]["completed_at"] == "2025-06-01T12:00:00Z"

    asyncio.run(http_client.aclose())
