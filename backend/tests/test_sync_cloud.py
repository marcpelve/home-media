"""Tests for the cloud storage sync engine (P-M3a): push / pull / LWW merge.

Uses a tmp SQLite DB (init_db) + LocalFolderBackend (tmp folder). Titles and
watch rows are inserted with direct SQL and EXPLICIT timestamp strings so the
LWW direction is controlled (e.g. local "2026-01-01 10:00:00" vs doc
"2026-01-02T10:00:00Z").
"""

import json
import sqlite3

from app.db import init_db
from app.storage import LocalFolderBackend, doc_is_valid
from app.sync import (
    pending_push_count,
    pull,
    push_title,
    record_sync_status,
    sync_all_cloud,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _add_watch(
    db_path,
    title_id,
    updated_at,
    season=None,
    episode=None,
    position_ms=0,
    duration_ms=0,
    completed=0,
    completed_at=None,
    device=None,
) -> None:
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO watch_progress (title_id, season, episode, position_ms, "
            "duration_ms, completed, completed_at, device, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title_id, season, episode, position_ms, duration_ms, completed,
             completed_at, device, updated_at),
        )
        conn.commit()
    finally:
        conn.close()


def _doc(tmdb_id, title, updated_at, type_="movie", watch=None, **title_kw) -> dict:
    """Build a minimal valid per-title doc for writing to a backend."""
    t = {"tmdb_id": tmdb_id, "type": type_, "title": title, "updated_at": updated_at}
    t.update(title_kw)
    return {
        "version": 1,
        "key": f"{type_}:{tmdb_id}",
        "tmdb_id": tmdb_id,
        "type": type_,
        "title": t,
        "watch": watch if watch is not None else [],
        "updated_at": updated_at,
    }


def _watch_entry(position_ms, duration_ms, completed, updated_at,
                 season=None, episode=None, completed_at=None, device=None):
    return {
        "season": season,
        "episode": episode,
        "position_ms": position_ms,
        "duration_ms": duration_ms,
        "completed": completed,
        "completed_at": completed_at,
        "device": device,
        "updated_at": updated_at,
    }


# ---------------------------------------------------------------------------
# push_title
# ---------------------------------------------------------------------------


def test_push_title_writes_doc(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix", "2026-01-01 10:00:00",
               poster_path="/p.jpg", device="dev-a")
    _add_watch(db, 603, "2026-01-01 11:00:00", position_ms=5000,
               duration_ms=100000, device="dev-a")

    be = LocalFolderBackend(tmp_path / "store")
    assert push_title(be, db, "movie:603") is True

    doc = be.read_title("movie:603")
    assert doc_is_valid(doc)
    assert doc["tmdb_id"] == 603
    assert doc["type"] == "movie"
    assert doc["title"]["title"] == "The Matrix"
    assert doc["title"]["poster_path"] == "/p.jpg"
    assert doc["title"]["device"] == "dev-a"
    assert doc["watch"][0]["position_ms"] == 5000
    # watch updated_at is stored verbatim (SQLite form) in the doc.
    assert doc["watch"][0]["updated_at"] == "2026-01-01 11:00:00"


def test_push_title_missing_title(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    be = LocalFolderBackend(tmp_path / "store")
    assert push_title(be, db, "movie:999") is False
    assert be.read_title("movie:999") is None


# ---------------------------------------------------------------------------
# pull / LWW merge
# ---------------------------------------------------------------------------


def test_pull_inserts_new_watch_row(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix", "2026-01-01 10:00:00")

    be = LocalFolderBackend(tmp_path / "store")
    # Doc title timestamp equals the local title's so only the watch insert
    # counts as a change (the title LWW is a no-op here).
    doc = _doc(603, "The Matrix", "2026-01-01 10:00:00")
    doc["watch"] = [_watch_entry(1234, 200000, 1, "2026-01-02T09:00:00Z",
                                 completed_at="2026-01-02T09:00:00Z",
                                 device="dev-b")]
    be.write_title("movie:603", doc)

    changed, errors = pull(be, db)
    assert errors == []
    assert changed == 1

    conn = _conn(db)
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["position_ms"] == 1234
    assert row["completed"] == 1
    # inserted with the DOC's updated_at verbatim (provenance preserved).
    assert row["completed_at"] == "2026-01-02T09:00:00Z"
    assert row["updated_at"] == "2026-01-02T09:00:00Z"


def test_pull_lww_title_both_directions(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix", "2026-01-01 10:00:00",
               overview="old", poster_path="/old.jpg")
    be = LocalFolderBackend(tmp_path / "store")

    # Newer doc wins: fields + updated_at (verbatim) updated.
    doc = _doc(603, "The Matrix Reloaded", "2026-01-02T10:00:00Z",
               overview="new", poster_path="/new.jpg")
    be.write_title("movie:603", doc)
    changed, errors = pull(be, db)
    assert errors == []
    assert changed >= 1
    conn = _conn(db)
    row = conn.execute("SELECT * FROM titles WHERE tmdb_id = 603").fetchone()
    conn.close()
    assert row["title"] == "The Matrix Reloaded"
    assert row["overview"] == "new"
    assert row["poster_path"] == "/new.jpg"
    assert row["updated_at"] == "2026-01-02T10:00:00Z"

    # Newer local row is untouched (the doc is now older).
    conn = _conn(db)
    conn.execute(
        "UPDATE titles SET title = 'Local Wins', overview = 'local', "
        "updated_at = '2026-01-03 10:00:00' WHERE tmdb_id = 603"
    )
    conn.commit()
    conn.close()
    doc2 = _doc(603, "Doc Again", "2026-01-01T10:00:00Z", overview="stale")
    be.write_title("movie:603", doc2)
    changed, errors = pull(be, db)
    assert errors == []
    assert changed == 0
    conn = _conn(db)
    row = conn.execute("SELECT * FROM titles WHERE tmdb_id = 603").fetchone()
    conn.close()
    assert row["title"] == "Local Wins"
    assert row["overview"] == "local"
    assert row["updated_at"] == "2026-01-03 10:00:00"


def test_pull_lww_watch_row_both_directions(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix", "2026-01-01 10:00:00")
    _add_watch(db, 603, "2026-01-01 11:00:00", position_ms=1000)
    be = LocalFolderBackend(tmp_path / "store")

    # Newer doc wins for the watch row. The doc title timestamp equals the
    # local title's so only the watch update counts as a change.
    doc = _doc(603, "The Matrix", "2026-01-01 10:00:00")
    doc["watch"] = [_watch_entry(9999, 100000, 1, "2026-01-02T10:00:00Z",
                                 device="dev-b")]
    be.write_title("movie:603", doc)
    changed, errors = pull(be, db)
    assert errors == []
    assert changed == 1
    conn = _conn(db)
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    assert row["position_ms"] == 9999
    assert row["completed"] == 1
    assert row["updated_at"] == "2026-01-02T10:00:00Z"

    # Newer local row untouched.
    conn = _conn(db)
    conn.execute(
        "UPDATE watch_progress SET position_ms = 5, "
        "updated_at = '2026-01-03 10:00:00' WHERE title_id = 603"
    )
    conn.commit()
    conn.close()
    doc2 = _doc(603, "The Matrix", "2026-01-01T10:00:00Z")
    doc2["watch"] = [_watch_entry(7777, 100000, 0, "2026-01-01T10:00:00Z",
                                  device="dev-b")]
    be.write_title("movie:603", doc2)
    changed, errors = pull(be, db)
    assert errors == []
    assert changed == 0
    conn = _conn(db)
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    assert row["position_ms"] == 5
    assert row["updated_at"] == "2026-01-03 10:00:00"


def test_pull_skips_unknown_title(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    be = LocalFolderBackend(tmp_path / "store")
    doc = _doc(999, "Ghost", "2026-01-02T10:00:00Z")
    doc["watch"] = [_watch_entry(1, 2, 0, "2026-01-02T10:00:00Z", device="dev-b")]
    be.write_title("movie:999", doc)

    changed, errors = pull(be, db)
    assert errors == []
    assert changed == 0

    conn = _conn(db)
    titles = conn.execute("SELECT * FROM titles").fetchall()
    watches = conn.execute("SELECT * FROM watch_progress").fetchall()
    conn.close()
    # Local catalog is authoritative: no title row -> nothing created.
    assert titles == []
    assert watches == []


# ---------------------------------------------------------------------------
# sync_all_cloud
# ---------------------------------------------------------------------------


def test_sync_all_cloud_converges_two_devices(tmp_path):
    dbA = tmp_path / "a.db"
    dbB = tmp_path / "b.db"
    init_db(dbA)
    init_db(dbB)
    be = LocalFolderBackend(tmp_path / "store")

    # Device A: a title + a watch row. Device B has the same local title
    # (the catalog is authoritative per device) but no watch row yet.
    _add_title(dbA, 603, "movie", "The Matrix", "2026-01-01 10:00:00")
    _add_watch(dbA, 603, "2026-01-01 11:00:00", position_ms=1000,
               duration_ms=100000, device="dev-a")
    _add_title(dbB, 603, "movie", "The Matrix", "2026-01-01 10:00:00")

    # Push from A -> shared store.
    result = sync_all_cloud(be, dbA)
    assert result["error"] is None
    assert result["pushed"] == 1

    # Pull into B: B gains A's watch row.
    changed, errors = pull(be, dbB)
    assert errors == []
    assert changed == 1
    conn = _conn(dbB)
    b_row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    assert b_row is not None
    assert b_row["position_ms"] == 1000

    # Change a watch row in B with a newer timestamp, then sync B (pushes it).
    conn = _conn(dbB)
    conn.execute(
        "UPDATE watch_progress SET position_ms = 5555, completed = 1, "
        "updated_at = '2026-01-05 10:00:00' WHERE title_id = 603"
    )
    conn.commit()
    conn.close()
    sync_all_cloud(be, dbB)

    # Pull into A: A's state equals B's for that row.
    pull(be, dbA)
    conn = _conn(dbA)
    a_row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    conn = _conn(dbB)
    b_row2 = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = 603"
    ).fetchone()
    conn.close()
    assert a_row["position_ms"] == b_row2["position_ms"] == 5555
    assert a_row["completed"] == b_row2["completed"] == 1
    assert a_row["updated_at"] == b_row2["updated_at"]


class _FakeBackend:
    """A backend whose read_title raises for one key (to exercise pull's
    per-title error isolation)."""

    def __init__(self, keys):
        self._keys = list(keys)

    def list_titles(self):
        return list(self._keys)

    def read_title(self, key):
        if key == "movie:1":
            raise RuntimeError("boom")
        return None

    def write_title(self, key, data):
        pass

    def delete_title(self, key):
        pass


def test_sync_all_cloud_records_error(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    be = _FakeBackend(["movie:1"])
    # Must not raise: the bad doc is recorded, not fatal.
    result = sync_all_cloud(be, db)
    assert result["error"] is not None
    assert "movie:1" in result["error"]
    assert "RuntimeError" in result["error"]


# ---------------------------------------------------------------------------
# sync_status + pending
# ---------------------------------------------------------------------------


def test_record_and_pending(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    _add_title(db, 603, "movie", "The Matrix", "2026-01-01 10:00:00")
    _add_watch(db, 603, "2026-01-01 11:00:00", position_ms=1000)  # old

    # record_sync_status writes a single row with last_push_at = now.
    record_sync_status(db, 3, None)
    conn = _conn(db)
    row = conn.execute("SELECT * FROM sync_status").fetchone()
    conn.close()
    assert row is not None
    assert row["pending_count"] == 3
    assert row["last_push_at"] is not None

    # Pin last_push_at to a controlled value so the pending count is exact.
    conn = _conn(db)
    conn.execute("UPDATE sync_status SET last_push_at = '2026-01-02 00:00:00'")
    conn.commit()
    conn.close()

    # Only the row newer than last_push_at counts as pending.
    _add_watch(db, 603, "2026-01-03 00:00:00", season=1, episode=2,
               position_ms=2000)
    assert pending_push_count(db) == 1
