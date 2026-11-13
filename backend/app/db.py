"""SQLite schema and connection helper.

The local DB is the source of truth for all watch progress and library state.
It works fully offline; Trakt sync is a best-effort overlay.
"""

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
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
    updated_at    TEXT DEFAULT (datetime('now')),
    device        TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id  INTEGER NOT NULL REFERENCES titles(id),
    season    INTEGER NOT NULL,
    episode   INTEGER NOT NULL,
    title     TEXT,
    overview  TEXT,
    runtime   INTEGER,
    still_path TEXT,
    air_date  TEXT
);

CREATE TABLE IF NOT EXISTS watch_progress (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id      INTEGER NOT NULL REFERENCES titles(id),
    season        INTEGER,
    episode       INTEGER,
    position_ms   INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    completed     INTEGER NOT NULL DEFAULT 0,
    completed_at  TEXT,
    updated_at    TEXT DEFAULT (datetime('now')),
    device        TEXT
);

CREATE TABLE IF NOT EXISTS sources_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id   INTEGER NOT NULL REFERENCES titles(id),
    hash       TEXT NOT NULL,
    quality    TEXT,
    size_gb    REAL,
    seeders    INTEGER,
    provider   TEXT,
    scraped_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS debrid_state (
    hash         TEXT PRIMARY KEY,
    cached       INTEGER NOT NULL DEFAULT 0,
    torbox_added INTEGER NOT NULL DEFAULT 0,
    added_at     TEXT,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS downloads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id     INTEGER NOT NULL REFERENCES titles(id),
    episode      INTEGER,
    hash         TEXT NOT NULL,
    file_path    TEXT,
    size_bytes   INTEGER,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK(status IN ('queued','downloading','done','failed')),
    progress     REAL NOT NULL DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS trakt_sync (
    last_sync_at  TEXT,
    pending_count INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tmdb_cache (
    path         TEXT PRIMARY KEY,
    payload      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    ttl_seconds  INT  NOT NULL
);
"""


def _migrate_device_column(conn: sqlite3.Connection) -> None:
    """Add a nullable ``device`` column to titles/watch_progress if absent.

    Idempotent: on a fresh DB the column is created by the schema and this
    is a no-op. On a legacy DB the column is missing, so we ALTER it in and,
    on that transition only, backfill the pre-existing NULL rows with the
    configured ``device_id`` (falling back to ``'default'``) and stamp
    ``updated_at``. Stamping establishes a clean cutover baseline for the
    first cloud push (PLAN.md S16 P-M1); rows already carrying a device are
    left untouched, and re-running init never re-stamps them.
    """
    for table in ("titles", "watch_progress"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "device" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN device TEXT")
            conn.execute(
                f"""
                UPDATE {table}
                SET device = COALESCE(
                        (SELECT value FROM settings WHERE key = 'device_id'),
                        'default'
                    ),
                    updated_at = datetime('now')
                WHERE device IS NULL
                """
            )


def init_db(db_path: str | Path) -> None:
    """Create the data directory (if needed) and all tables (if not present)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        _migrate_device_column(conn)
        conn.commit()
    finally:
        conn.close()


def get_db(db_path: str | Path) -> sqlite3.Connection:
    """Return a sqlite3 connection with Row factory for dict-like access."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
