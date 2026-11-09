"""TMDB metadata endpoints.

Mounted at /tmdb in main.py. All data is served through TmdbClient which
handles caching, de-duplication, and rate-awareness.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.tmdb import TmdbClient

router = APIRouter(prefix="/tmdb", tags=["tmdb"])


def get_tmdb_client(request: Request) -> TmdbClient:
    """Retrieve the TmdbClient instance stored on the app state."""
    return request.app.state.tmdb_client


@router.get("/search")
async def search(request: Request, q: str = Query(..., min_length=1)):
    client = get_tmdb_client(request)
    return await client.search(q)


@router.get("/home")
async def home(request: Request):
    client = get_tmdb_client(request)
    return await client.home()


@router.get("/popular")
async def popular(request: Request):
    client = get_tmdb_client(request)
    return await client.popular()


@router.get("/latest")
async def latest(request: Request):
    client = get_tmdb_client(request)
    return await client.latest()


@router.get("/categories")
async def categories(request: Request):
    client = get_tmdb_client(request)
    return await client.genres()


@router.get("/title/{tmdb_id}")
async def title(request: Request, tmdb_id: int):
    client = get_tmdb_client(request)
    try:
        data = await client.title(tmdb_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Title not found")
    # Persist durable metadata so the offline library can resolve this title.
    db_path = getattr(request.app.state, "db_path", None)
    if db_path:
        from app.library import upsert_title

        upsert_title(db_path, tmdb_id, data)
    return data


@router.get("/reviews/{tmdb_id}")
async def reviews(request: Request, tmdb_id: int):
    client = get_tmdb_client(request)
    try:
        return await client.reviews(tmdb_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Reviews not found")


@router.get("/poster/{tmdb_id}")
async def poster(request: Request, tmdb_id: int, size: str = Query("w342")):
    """Serve the cached poster file; downloads + caches on miss."""
    client = get_tmdb_client(request)
    # Prefer the durable local poster_path (offline-safe); fall back to
    # fetching details, which needs the 7-day title cache or a live call.
    db_path = getattr(request.app.state, "db_path", None)
    poster_path = None
    if db_path:
        from app.library import local_poster_path

        poster_path = local_poster_path(db_path, tmdb_id)
    if poster_path is None:
        try:
            details = await client.title(tmdb_id)
        except Exception:
            raise HTTPException(status_code=404, detail="Title not found")
        poster_path = details.get("poster_path")
    if not poster_path:
        raise HTTPException(status_code=404, detail="No poster available")

    dest = await client.get_poster(tmdb_id, poster_path, size)
    if dest is None:
        raise HTTPException(status_code=404, detail="Poster unavailable")

    return FileResponse(str(dest))
