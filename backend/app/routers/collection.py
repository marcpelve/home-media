"""Offline library endpoints.

Mounted at /library in main.py. Lists the local library (downloaded content)
straight from the DB and resolves a local file path for playback. Works with
no internet.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app import library as lib

router = APIRouter(prefix="/library", tags=["library"])


@router.get("")
async def list_library(request: Request):
    """Browsable local library, grouped by title, with playable file paths."""
    return lib.build_library(request.app.state.db_path)


@router.delete("/{tmdb_id}")
async def delete_library_title(request: Request, tmdb_id: int):
    """Remove a title from the library (hard delete).

    Cancels any in-progress downloads for it, then deletes its files and
    rows, so it no longer appears in the library.
    """
    mgr = request.app.state.downloads
    removed = mgr.remove_for_title(tmdb_id)
    return {"removed": removed}


@router.delete("/{tmdb_id}/episodes/{episode}")
async def delete_library_episode(request: Request, tmdb_id: int, episode: int):
    """Remove one episode from the library (hard delete)."""
    mgr = request.app.state.downloads
    removed = mgr.remove_for_episode(tmdb_id, episode)
    return {"removed": removed}


@router.get("/file")
async def library_file(
    request: Request, tmdb_id: int, season: int | None = None, episode: int | None = None
):
    """Resolve the local file path for a title (movie) or episode (TV)."""
    fp = lib.local_file_for(request.app.state.db_path, tmdb_id, season=season, episode=episode)
    if not fp:
        raise HTTPException(status_code=404, detail="No local file")
    return {"file_path": fp}
