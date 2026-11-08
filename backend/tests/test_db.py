"""Test that init_db creates all 8 tables."""

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
