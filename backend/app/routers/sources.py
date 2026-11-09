"""Source-finding endpoints (Cocoscrapers).

Mounted at /sources in main.py. POST /sources/{tmdb_id} returns a ranked
list of torrent sources for the given title. For TV, pass season/episode
in the request body.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.coco_driver import build_movie_data, build_tv_data, scrape
from app.tmdb import TmdbClient

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceQuery(BaseModel):
    season: int | None = None
    episode: int | None = None


def _get_tmdb_client(request: Request) -> TmdbClient:
    return request.app.state.tmdb_client


def _extract_year(date_str: str | None) -> str:
    if date_str and len(date_str) >= 4:
        return date_str[:4]
    return ""


def _extract_aliases(details: dict) -> list[str]:
    raw = details.get("aliases") or []
    if raw and isinstance(raw[0], dict):
        return [a.get("title", "") for a in raw if a.get("title")]
    return raw


@router.post("/{tmdb_id}")
async def get_sources(tmdb_id: int, query: SourceQuery, request: Request):
    client = _get_tmdb_client(request)
    try:
        details = await client.title(tmdb_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Title not found")

    imdb = details.get("imdb_id") or (details.get("external_ids") or {}).get("imdb_id", "")
    if not imdb:
        raise HTTPException(status_code=400, detail="No IMDb ID for this title")

    aliases = _extract_aliases(details)
    media_type = details.get("media_type") or (
        "tv" if details.get("first_air_date") else "movie"
    )

    if media_type == "tv" and query.season is not None and query.episode is not None:
        year = _extract_year(details.get("first_air_date"))
        show_title = details.get("name", "")
        data = build_tv_data(
            show_title=show_title,
            year=year,
            imdb=imdb,
            season=query.season,
            episode=query.episode,
            aliases=aliases,
        )
    else:
        year = _extract_year(details.get("release_date"))
        title = details.get("title", details.get("name", ""))
        data = build_movie_data(
            title=title,
            year=year,
            imdb=imdb,
            aliases=aliases,
        )

    results = await asyncio.to_thread(scrape, data)
    return {"sources": results, "count": len(results)}
