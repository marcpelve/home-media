"""Tests for init_db: table creation plus the P-M1 device-column migration."""

import sqlite3

from app.db import init_db

EXPECTED_TABLES = {
    "titles",
    "episodes",
    "watch_progress",
    "sources_cache",
    "debrid_state",
    "downloads",
    "trakt_sync",
    "settings",
}


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    conn.close()

    table_names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(table_names), (
        f"Missing tables: {EXPECTED_TABLES - table_names}"
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _build_legacy_db(db_path) -> None:
    """Create titles/watch_progress/settings with the pre-P-M1 schema (no device)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE titles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tmdb_id       INTEGER NOT NULL,
                imdb_id       TEXT,
                type          TEXT NOT NULL CHECK(type IN ('movie','tv')),
                title         TEXT NOT NULL,
                year          INTEGER,
                poster_path   TEXT,
                backdrop_path TEXT,
                overview      TEXT,
                runtime       INTEGER,
                genres        TEXT,
                first_air_date TEXT,
                status        TEXT,
                added_at      TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE watch_progress (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title_id      INTEGER NOT NULL REFERENCES titles(id),
                season        INTEGER,
                episode       INTEGER,
                position_ms   INTEGER NOT NULL DEFAULT 0,
                duration_ms   INTEGER NOT NULL DEFAULT 0,
                completed     INTEGER NOT NULL DEFAULT 0,
                completed_at  TEXT,
                updated_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO titles (tmdb_id, type, title, added_at, updated_at) "
            "VALUES (1, 'movie', 'Test', datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO watch_progress (title_id, position_ms, duration_ms, updated_at) "
            "VALUES (1, 1000, 2000, datetime('now'))"
        )
        conn.commit()
    finally:
        conn.close()


def test_fresh_db_has_device_column(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        titles_cols = _table_columns(conn, "titles")
        wp_cols = _table_columns(conn, "watch_progress")
    finally:
        conn.close()

    assert "device" in titles_cols
    assert "device" in wp_cols


def test_legacy_db_migrated_and_backfilled(tmp_path):
    db_path = tmp_path / "test.db"
    _build_legacy_db(db_path)
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        titles = conn.execute("SELECT device, updated_at FROM titles").fetchall()
        wp = conn.execute("SELECT device, updated_at FROM watch_progress").fetchall()
    finally:
        conn.close()

    assert titles and all(row[0] is not None and row[1] is not None for row in titles)
    assert wp and all(row[0] is not None and row[1] is not None for row in wp)


def test_backfill_honors_settings_device_id(tmp_path):
    db_path = tmp_path / "test.db"
    _build_legacy_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES ('device_id', 'machine-a')")
        conn.commit()
    finally:
        conn.close()

    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        titles = [row[0] for row in conn.execute("SELECT device FROM titles").fetchall()]
        wp = [row[0] for row in conn.execute("SELECT device FROM watch_progress").fetchall()]
    finally:
        conn.close()

    assert titles == ["machine-a"]
    assert wp == ["machine-a"]


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    _build_legacy_db(db_path)
    init_db(db_path)

    def snapshot():
        conn = sqlite3.connect(str(db_path))
        try:
            titles = conn.execute("SELECT device, updated_at FROM titles").fetchall()
            wp = conn.execute("SELECT device, updated_at FROM watch_progress").fetchall()
        finally:
            conn.close()
        return titles, wp

    before = snapshot()
    init_db(db_path)
    after = snapshot()

    assert before == after
