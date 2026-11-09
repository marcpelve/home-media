"""Local watch progress - the source of truth.

The local SQLite DB owns watch progress and works fully offline. Trakt sync
(P5) is a best-effort overlay on top of this.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _conn(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def upsert_progress(
    db_path: str | Path,
    title_id: int,
    position_ms: int,
    duration_ms: int,
    completed: bool = False,
    season: int | None = None,
    episode: int | None = None,
) -> dict:
    """Insert or update the watch_progress row for a title (or episode)."""
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM watch_progress WHERE title_id = ? "
            "AND season IS ? AND episode IS ?",
            (title_id, season, episode),
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO watch_progress "
                "(title_id, season, episode, position_ms, duration_ms, "
                " completed, completed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, "
                " CASE WHEN ? THEN datetime('now') ELSE NULL END, datetime('now'))",
                (
                    title_id, season, episode, position_ms, duration_ms,
                    1 if completed else 0, 1 if completed else 0,
                ),
            )
            new_id = cur.lastrowid
        else:
            new_id = row["id"]
            conn.execute(
                "UPDATE watch_progress SET position_ms = ?, duration_ms = ?, "
                "completed = ?, "
                "completed_at = CASE WHEN ? THEN datetime('now') ELSE completed_at END, "
                "updated_at = datetime('now') WHERE id = ?",
                (
                    position_ms, duration_ms, 1 if completed else 0,
                    1 if completed else 0, new_id,
                ),
            )
        conn.commit()
        result = get_progress(db_path, title_id, season=season, episode=episode)
        assert result is not None
        return result
    finally:
        conn.close()


def get_progress(
    db_path: str | Path,
    title_id: int,
    season: int | None = None,
    episode: int | None = None,
) -> dict | None:
    """Fetch the watch_progress row for a title (or episode), if any."""
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM watch_progress WHERE title_id = ? "
            "AND season IS ? AND episode IS ?",
            (title_id, season, episode),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def upsert_complete(
    db_path: str | Path,
    title_id: int,
    season: int | None = None,
    episode: int | None = None,
) -> dict:
    """Mark a title (or episode) as watched, preserving any saved position.

    Backs manual "mark as watched" (a movie, one episode, or every episode
    in a season). A new row starts at 0/0; an existing row keeps its
    position_ms/duration_ms and only gains completed/completed_at.
    """
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM watch_progress WHERE title_id = ? "
            "AND season IS ? AND episode IS ?",
            (title_id, season, episode),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO watch_progress "
                "(title_id, season, episode, position_ms, duration_ms, "
                " completed, completed_at, updated_at) "
                "VALUES (?, ?, ?, 0, 0, 1, datetime('now'), datetime('now'))",
                (title_id, season, episode),
            )
        else:
            conn.execute(
                "UPDATE watch_progress SET completed = 1, "
                "completed_at = datetime('now'), updated_at = datetime('now') "
                "WHERE id = ?",
                (row["id"],),
            )
        conn.commit()
        result = get_progress(db_path, title_id, season=season, episode=episode)
        assert result is not None
        return result
    finally:
        conn.close()
