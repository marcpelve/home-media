"""Local library endpoints.

Mounted at /watch in main.py. Serves the Continue Watching row for the home
screen: local watch_progress rows (source of truth) enriched with TMDB
metadata (cached, so it stays fast and works offline after first load).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

router = APIRouter(prefix="/watch", tags=["library"])


@router.get("")
async def watch_list(request: Request):
    """Continue Watching: recent local progress, newest first.

    Each row is enriched with the title's name/type/poster from TMDB. Titles
    that cannot be resolved (offline, unknown id) are skipped so the row still
    renders the rest.
    """
    conn = sqlite3.connect(request.app.state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT title_id, season, episode, position_ms, duration_ms, "
            "completed, updated_at FROM watch_progress "
            "ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()

    client = request.app.state.tmdb_client
    items = []
    for r in rows:
        try:
            details = await client.title(r["title_id"])
        except Exception:
            continue
        items.append({
            "title_id": r["title_id"],
            "title": details.get("title") or details.get("name"),
            "type": details.get("_type"),
            "poster": f"/tmdb/poster/{r['title_id']}?size=w342",
            "season": r["season"],
            "episode": r["episode"],
            "position_ms": r["position_ms"],
            "duration_ms": r["duration_ms"],
            "completed": bool(r["completed"]),
            "updated_at": r["updated_at"],
        })
    return {"items": items}
