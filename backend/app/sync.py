"""Trakt sync engine: push local watch progress to Trakt, pull Trakt
watched/progress, and merge into the local watch_progress source of truth.

Local is authoritative. Trakt is a sync/backup layer that degrades gracefully
when unauthenticated or offline (records an error, local keeps working).
Merge rule: max position_ms per item, union of completed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.trakt import TraktAuthError, TraktClient


def _now_iso() -> str:
    """ISO 8601 UTC - for Trakt `played_at` fields."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_sqlite(conn: sqlite3.Connection) -> str:
    """datetime('now') - same format as the schema's updated_at default."""
    return conn.execute("SELECT datetime('now')").fetchone()[0]


def _get_setting(db_path, key: str) -> str | None:
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_setting(db_path, key: str, value: str) -> None:
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _record_sync(db_path, pending_count: int, last_error: str | None) -> None:
    conn = _conn(db_path)
    try:
        conn.execute("DELETE FROM trakt_sync")
        conn.execute(
            "INSERT INTO trakt_sync (last_sync_at, pending_count, last_error) "
            "VALUES (?, ?, ?)",
            (_now_sqlite(conn), pending_count, last_error),
        )
        conn.commit()
    finally:
        conn.close()


def _local_title_by_tmdb(conn: sqlite3.Connection, tmdb_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM titles WHERE tmdb_id = ?", (tmdb_id,)
    ).fetchone()


def _find_progress(
    conn: sqlite3.Connection, title_id: int, season: int | None, episode: int | None
) -> sqlite3.Row | None:
    # NULL-safe match on (season, episode).
    s = -1 if season is None else season
    e = -1 if episode is None else episode
    return conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ? AND "
        "COALESCE(season, -1) = ? AND COALESCE(episode, -1) = ?",
        (title_id, s, e),
    ).fetchone()


def _ep(node: dict, key: str) -> int | None:
    ep = node.get("episode") or {}
    val = ep.get(key)
    return int(val) if isinstance(val, int) else None


def _progress_ms(node: dict, entry: dict) -> tuple[int, int]:
    """Convert Trakt node.runtime (min) + progress.percent to (ms, duration_ms)."""
    runtime = node.get("runtime")
    percent = (entry.get("progress") or {}).get("percent", 0.0)
    if not isinstance(runtime, (int, float)) or not isinstance(percent, (int, float)):
        return 0, 0
    duration_ms = int(runtime * 60000)
    position_ms = int(percent / 100.0 * duration_ms)
    return position_ms, duration_ms


def _history_item(title: sqlite3.Row, season: int | None, episode: int | None) -> dict | None:
    """Build a Trakt items/history entry from a local title + episode coords."""
    if not title["tmdb_id"]:
        return None
    ids: dict = {"tmdb": title["tmdb_id"]}
    if title["imdb_id"]:
        ids["imdb"] = title["imdb_id"]
    item: dict = {"played_at": _now_iso(), "type": title["type"]}
    if title["type"] == "movie":
        item["movie"] = {"ids": ids}
    else:
        item["episode"] = {"ids": ids, "season": season or 1, "number": episode or 1}
    return item


async def push_progress(client: TraktClient, db_path) -> int:
    """Scrobble local watch progress (updated since the last push) to Trakt.

    Returns the number of items pushed. No-op (0) when unauthenticated.
    """
    if not client.is_authenticated():
        return 0
    conn = _conn(db_path)
    try:
        last_push = _get_setting(db_path, "trakt_last_push_at") or ""
        rows = conn.execute(
            "SELECT * FROM watch_progress WHERE updated_at > ?", (last_push,)
        ).fetchall()
        pushed = 0
        for row in rows:
            title = conn.execute(
                "SELECT * FROM titles WHERE id = ?", (row["title_id"],)
            ).fetchone()
            if title is None:
                continue
            item = _history_item(title, row["season"], row["episode"])
            if item is None:
                continue
            await client.mark_watched(item)
            pushed += 1
        if pushed:
            _set_setting(db_path, "trakt_last_push_at", _now_sqlite(conn))
        return pushed
    finally:
        conn.close()


async def pull_progress(client: TraktClient, db_path) -> int:
    """Pull Trakt in-progress + watched movies; merge into local progress.

    Returns the number of local rows created/updated. No-op (0) when
    unauthenticated.
    """
    if not client.is_authenticated():
        return 0
    conn = _conn(db_path)
    merged = 0
    try:
        # In-progress items (movies + episodes) from /progress/watched.
        for entry in await client.get_progress():
            if not isinstance(entry, dict):
                continue
            node = entry.get("node") or {}
            tmdb_id = (node.get("ids") or {}).get("tmdb")
            if not tmdb_id:
                continue
            title = _local_title_by_tmdb(conn, int(tmdb_id))
            if title is None:
                continue
            season = _ep(node, "season")
            episode = _ep(node, "number")
            position_ms, duration_ms = _progress_ms(node, entry)
            row = _find_progress(conn, title["id"], season, episode)
            if row is None:
                conn.execute(
                    "INSERT INTO watch_progress (title_id, season, episode, "
                    "position_ms, duration_ms, completed) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (title["id"], season, episode, position_ms, duration_ms,
                     1 if entry.get("completed") else 0),
                )
                merged += 1
            else:
                new_completed = row["completed"] or entry.get("completed")
                conn.execute(
                    "UPDATE watch_progress SET position_ms = ?, duration_ms = ?, "
                    "completed = ?, completed_at = ? WHERE id = ?",
                    (
                        max(row["position_ms"], position_ms),
                        max(row["duration_ms"], duration_ms),
                        int(new_completed),
                        _now_sqlite(conn) if new_completed else row["completed_at"],
                        row["id"],
                    ),
                )
                merged += 1
        # Watched movies -> mark completed.
        for movie in await client.get_watched_movies():
            if not isinstance(movie, dict):
                continue
            tmdb_id = (movie.get("ids") or {}).get("tmdb")
            if not tmdb_id:
                continue
            title = _local_title_by_tmdb(conn, int(tmdb_id))
            if title is None:
                continue
            row = _find_progress(conn, title["id"], None, None)
            if row is None:
                conn.execute(
                    "INSERT INTO watch_progress (title_id, position_ms, duration_ms, "
                    "completed) VALUES (?, 0, 0, 1)",
                    (title["id"],),
                )
                merged += 1
            elif not row["completed"]:
                conn.execute(
                    "UPDATE watch_progress SET completed = 1, completed_at = ? "
                    "WHERE id = ?",
                    (_now_sqlite(conn), row["id"]),
                )
                merged += 1
        conn.commit()
        return merged
    finally:
        conn.close()


async def sync_all(client: TraktClient, db_path) -> dict:
    """Push local changes to Trakt, pull Trakt activity, record status.

    Degrades gracefully: no auth / offline -> no crash, error recorded. Returns
    {pushed, pulled, error}.
    """
    if not client.is_authenticated():
        _record_sync(db_path, 0, "not authenticated")
        return {"pushed": 0, "pulled": 0, "error": "not authenticated"}
    pushed = 0
    pulled = 0
    error = None
    try:
        pushed = await push_progress(client, db_path)
        pulled = await pull_progress(client, db_path)
    except TraktAuthError as e:
        error = f"auth: {e}"
    except Exception as e:  # network / API error - local keeps working
        error = f"{type(e).__name__}: {e}"
    _record_sync(db_path, pushed + pulled, error)
    return {"pushed": pushed, "pulled": pulled, "error": error}
