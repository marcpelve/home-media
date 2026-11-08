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
    updated_at    TEXT DEFAULT (datetime('now'))
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
    updated_at    TEXT DEFAULT (datetime('now'))
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
"""


def init_db(db_path: str | Path) -> None:
    """Create the data directory (if needed) and all tables (if not present)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db(db_path: str | Path) -> sqlite3.Connection:
    """Return a sqlite3 connection with Row factory for dict-like access."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
