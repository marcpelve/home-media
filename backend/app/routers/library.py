"""Local library endpoints.

Mounted at /watch in main.py. Serves the Continue Watching row for the home
screen: local watch_progress rows (source of truth) enriched with TMDB
metadata (cached, so it stays fast and works offline after first load).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import watch
from app.sync import push_key_for

router = APIRouter(prefix="/watch", tags=["library"])


@router.get("")
async def watch_list(request: Request):
    """Continue Watching: one card per show/movie, most-recently-touched first.

    A TV show with several episodes in progress yields a single card whose
    resume target is the first (lowest season, then episode) not-yet-completed
    episode that has a progress row. A show whose started episodes are all
    complete drops out of the row entirely. A movie is a single card while it
    is incomplete. Titles that cannot be resolved (offline, unknown id) are
    skipped so the row still renders the rest.
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

    # Group rows by title. Rows arrive most-recent-first, so each title's first
    # row is its most recent and the dict's insertion order is the display order.
    by_title = {}
    for r in rows:
        by_title.setdefault(r["title_id"], []).append(r)

    client = request.app.state.tmdb_client
    items = []
    for title_id, trows in by_title.items():
        ep_rows = [
            r for r in trows
            if r["season"] is not None and r["episode"] is not None
        ]
        movie_row = next(
            (r for r in trows if r["season"] is None and r["episode"] is None),
            None,
        )

        if ep_rows:
            # Resume target: the first not-yet-completed episode (lowest S/E).
            # If every started episode is complete, the show is done -> skip.
            incomplete = [r for r in ep_rows if not r["completed"]]
            if not incomplete:
                continue
            resume = min(incomplete, key=lambda r: (r["season"], r["episode"]))
        elif movie_row is not None:
            if movie_row["completed"]:
                continue
            resume = movie_row
        else:
            continue

        try:
            details = await client.title(title_id)
        except Exception:
            continue
        items.append({
            "title_id": title_id,
            "title": details.get("title") or details.get("name"),
            "type": details.get("_type"),
            "poster": f"/tmdb/poster/{title_id}?size=w342",
            "season": resume["season"],
            "episode": resume["episode"],
            "position_ms": resume["position_ms"],
            "duration_ms": resume["duration_ms"],
            "completed": bool(resume["completed"]),
            "updated_at": resume["updated_at"],
        })
    return {"items": items}


@router.get("/progress/{tmdb_id}")
async def watch_progress(request: Request, tmdb_id: int,
                        season: int | None = None, episode: int | None = None):
    """Progress for a title (and a specific episode when given).

    With season+episode, returns that exact row. Without them, returns the
    current episode: the most recently updated row that has an episode number
    (TV). Returns an empty object when there is none, so the caller falls back
    to its own default (e.g. S01E01).
    """
    conn = sqlite3.connect(request.app.state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        if season is not None and episode is not None:
            row = conn.execute(
                "SELECT season, episode, position_ms, duration_ms, completed "
                "FROM watch_progress WHERE title_id = ? AND season IS ? "
                "AND episode IS ?",
                (tmdb_id, season, episode),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT season, episode, position_ms, duration_ms, completed "
                "FROM watch_progress WHERE title_id = ? AND episode IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1",
                (tmdb_id,),
            ).fetchone()
            if row is None:
                # No episode rows (a movie): fall back to the title-level row so
                # resume can read a movie's saved position.
                row = conn.execute(
                    "SELECT season, episode, position_ms, duration_ms, completed "
                    "FROM watch_progress WHERE title_id = ? AND episode IS NULL "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (tmdb_id,),
                ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    return {
        "season": row["season"],
        "episode": row["episode"],
        "position_ms": row["position_ms"],
        "duration_ms": row["duration_ms"],
        "completed": bool(row["completed"]),
    }


@router.get("/progress/{tmdb_id}/season/{season}")
async def watch_season_progress(request: Request, tmdb_id: int, season: int):
    """All episodes' progress for a season: {episode: {completed, position_ms, duration_ms}}."""
    conn = sqlite3.connect(request.app.state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT episode, position_ms, duration_ms, completed "
            "FROM watch_progress WHERE title_id = ? AND season = ? "
            "AND episode IS NOT NULL",
            (tmdb_id, season),
        ).fetchall()
    finally:
        conn.close()
    result = {}
    for r in rows:
        result[r["episode"]] = {
            "completed": bool(r["completed"]),
            "position_ms": r["position_ms"],
            "duration_ms": r["duration_ms"],
        }
    return result


class CompleteRequest(BaseModel):
    title_id: int
    season: int | None = None
    episode: int | None = None
    whole_season: bool = False
    episode_count: int | None = None


@router.post("/complete")
def complete(req: CompleteRequest, request: Request):
    """Mark a movie, one episode, or a whole season as watched.

    whole_season with season + episode_count marks episodes 1..episode_count.
    Position is preserved (a half-watched item stays half-watched, just
    flagged complete) so the row still feeds the Trakt push on next sync.
    """
    db = request.app.state.db_path
    if req.whole_season and req.season is not None:
        count = req.episode_count or 0
        for ep in range(1, count + 1):
            watch.upsert_complete(db, req.title_id, req.season, ep)
    elif req.season is not None and req.episode is not None:
        watch.upsert_complete(db, req.title_id, req.season, req.episode)
        count = 1
    else:
        watch.upsert_complete(db, req.title_id, None, None)
        count = 1
    # Debounced cloud push: a whole-season mark is one title doc.
    pusher = getattr(request.app.state, "pusher", None)
    if pusher is not None:
        key = push_key_for(db, req.title_id)
        if key:
            pusher.schedule(key)
    return {"completed": count}
