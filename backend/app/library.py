"""Offline local-library service.

Builds the browsable library straight from the local DB (downloads,
watch_progress, titles) so it works with no internet. The tmdb_cache and the
poster disk cache are used only as best-effort enrichment, never as the
source of truth.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from app.db import get_db


def _year_from_meta(meta: dict) -> int | None:
    date = meta.get("release_date") or meta.get("first_air_date")
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _cached_title_meta(db_path: str, tmdb_id: int) -> dict | None:
    """Best-effort: read title details from tmdb_cache, ignoring TTL.

    Used only when no durable titles row exists, so a stale-but-present
    payload is better than nothing while offline.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for path in (f"movie/{tmdb_id}", f"tv/{tmdb_id}?append_to=external_ids"):
            row = conn.execute(
                "SELECT payload FROM tmdb_cache WHERE path = ?", (path,)
            ).fetchone()
            if row is not None:
                data = json.loads(row["payload"])
                data["_type"] = "movie" if path.startswith("movie") else "tv"
                return data
        return None
    finally:
        conn.close()


def _meta_for(db_path: str, tmdb_id: int, title_row) -> dict:
    """Resolve title metadata: durable titles row > tmdb_cache > guess."""
    if title_row is not None:
        return {
            "tmdb_id": tmdb_id,
            "type": title_row["type"],
            "title": title_row["title"],
            "year": title_row["year"],
            "poster_path": title_row["poster_path"],
            "overview": title_row["overview"],
        }
    cached = _cached_title_meta(db_path, tmdb_id)
    if cached is not None:
        return {
            "tmdb_id": tmdb_id,
            "type": cached.get("_type"),
            "title": cached.get("title") or cached.get("name"),
            "year": _year_from_meta(cached),
            "poster_path": cached.get("poster_path"),
            "overview": cached.get("overview"),
        }
    return {
        "tmdb_id": tmdb_id,
        "type": None,
        "title": None,
        "year": None,
        "poster_path": None,
        "overview": None,
    }


def _name_from_files(files: list[dict]) -> str | None:
    """Derive a readable name from the first local file's stem."""
    for f in files:
        stem = Path(f["file_path"]).stem
        if stem:
            return stem
    return None


def _device_id(conn: sqlite3.Connection) -> str:
    """This machine's device_id: configured value, else the Windows
    username, else 'default'."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'device_id'"
    ).fetchone()
    if row and row[0]:
        return row[0]
    return os.environ.get("USERNAME", "default")


def build_library(db_path: str) -> dict:
    """Return the local library, grouped by title, from the DB only.

    Each title carries its local files (with playable file_path), per-episode
    watch state, and best-effort metadata. Works fully offline.
    """
    conn = get_db(db_path)
    try:
        # Local files: finished downloads whose file still exists on disk.
        files: dict[int, list[dict]] = {}
        for r in conn.execute(
            "SELECT title_id, episode, file_path FROM downloads WHERE status='done'"
        ):
            fp = r["file_path"]
            if fp and Path(fp).exists():
                files.setdefault(r["title_id"], []).append(
                    {"episode": r["episode"], "file_path": fp}
                )
        # Watch progress keyed by (tmdb_id, season, episode).
        progress = {}
        for r in conn.execute(
            "SELECT title_id, season, episode, position_ms, duration_ms, completed, "
            "updated_at FROM watch_progress"
        ):
            progress[(r["title_id"], r["season"], r["episode"])] = r
        # Durable title metadata keyed by tmdb_id.
        titles = {}
        for r in conn.execute(
            "SELECT tmdb_id, type, title, year, poster_path, overview FROM titles"
        ):
            titles[r["tmdb_id"]] = r
    finally:
        conn.close()

    # Only titles with at least one local file belong in the library. A title
    # that was merely viewed (metadata upserted) but not downloaded is excluded.
    ids = set(files)
    out = []
    for tid in sorted(ids):
        meta = _meta_for(db_path, tid, titles.get(tid))
        local_files = files.get(tid, [])

        # Per-episode list (TV): one entry per local file, with best-effort
        # watch state matched by episode number.
        episodes = []
        for f in local_files:
            ep_num = f["episode"]
            match = next(
                (pr for (t2, s, e), pr in progress.items() if t2 == tid and e == ep_num),
                None,
            )
            episodes.append({
                "episode": ep_num,
                "file_path": f["file_path"],
                "completed": bool(match["completed"]) if match else False,
                "position_ms": match["position_ms"] if match else 0,
                "duration_ms": match["duration_ms"] if match else 0,
            })

        # Overall watch state (movie, or the most recent progress row).
        title_progress = [pr for (t2, s, e), pr in progress.items() if t2 == tid]
        overall = max(title_progress, key=lambda pr: pr["updated_at"]) if title_progress else None

        type_ = meta["type"]
        if type_ is None:
            type_ = "tv" if any(f["episode"] is not None for f in local_files) else "movie"
        name = meta["title"] or _name_from_files(local_files) or f"#{tid}"

        out.append({
            "tmdb_id": tid,
            "type": type_,
            "title": name,
            "year": meta["year"],
            "poster": f"/tmdb/poster/{tid}?size=w342" if meta["poster_path"] else None,
            "overview": meta["overview"],
            "file_count": len(local_files),
            "episodes": episodes,
            "watch": {
                "position_ms": overall["position_ms"],
                "duration_ms": overall["duration_ms"],
                "completed": bool(overall["completed"]),
            } if overall else None,
        })
    return {"titles": out}


def local_file_for(
    db_path: str, tmdb_id: int, season: int | None = None, episode: int | None = None
) -> str | None:
    """Resolve the local file_path for a title (movie) or episode (TV)."""
    conn = get_db(db_path)
    try:
        if episode is not None:
            row = conn.execute(
                "SELECT file_path FROM downloads "
                "WHERE status='done' AND title_id=? AND episode=?",
                (tmdb_id, episode),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT file_path FROM downloads "
                "WHERE status='done' AND title_id=? AND episode IS NULL",
                (tmdb_id,),
            ).fetchone()
        if row is None:
            return None
        fp = row["file_path"]
        return fp if (fp and Path(fp).exists()) else None
    finally:
        conn.close()


def local_poster_path(db_path: str, tmdb_id: int) -> str | None:
    """Return the durable poster_path for a title, if stored locally."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT poster_path FROM titles WHERE tmdb_id=? AND poster_path IS NOT NULL",
            (tmdb_id,),
        ).fetchone()
        return row["poster_path"] if row is not None else None
    finally:
        conn.close()


def upsert_title(db_path: str, tmdb_id: int, meta: dict) -> int:
    """Persist durable title metadata. Returns the local titles.id."""
    # _type is our authoritative media type ("movie"/"tv", set by the client);
    # TMDB's own "type" field is the TV content type ("Scripted", "Animated",
    # ...) and would fail the CHECK constraint if used.
    type_ = meta.get("_type") or meta.get("type")
    title = meta.get("title") or meta.get("name")
    year = _year_from_meta(meta)
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if row is None:
            # INSERT-only device stamping: a fresh title is this machine's;
            # the UPDATE path leaves device alone because the LWW merge owns
            # it for existing titles (a cloud pull may have set it).
            device = _device_id(conn)
            cur = conn.execute(
                "INSERT INTO titles "
                "(tmdb_id, imdb_id, type, title, year, poster_path, backdrop_path, "
                " overview, runtime, device, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    tmdb_id,
                    meta.get("imdb_id") or (meta.get("external_ids") or {}).get("imdb_id"),
                    type_,
                    title,
                    year,
                    meta.get("poster_path"),
                    meta.get("backdrop_path"),
                    meta.get("overview"),
                    meta.get("runtime"),
                    device,
                ),
            )
            new_id = cur.lastrowid
            assert new_id is not None
            conn.commit()
            return new_id
        conn.execute(
            "UPDATE titles SET "
            " type=COALESCE(?,type), title=COALESCE(?,title), year=COALESCE(?,year), "
            " poster_path=COALESCE(?,poster_path), backdrop_path=COALESCE(?,backdrop_path), "
            " overview=COALESCE(?,overview), updated_at=datetime('now') WHERE id=?",
            (
                type_,
                title,
                year,
                meta.get("poster_path"),
                meta.get("backdrop_path"),
                meta.get("overview"),
                row["id"],
            ),
        )
        conn.commit()
        return row["id"]
    finally:
        conn.close()
