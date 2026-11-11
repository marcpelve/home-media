"""Config + cache management endpoints.

Mounted at /config in main.py. GET /config reports the download + poster
directories and the poster-cache size on disk. POST /config/clear-cache wipes
the poster (image) cache. The metadata (tmdb_cache) table is left alone: it is
tiny and TTL-bounded, so clearing it would only cost re-fetches with no disk
benefit.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter(prefix="/config", tags=["config"])


def _dir_bytes(root: Path) -> int:
    """Total size in bytes of all files under root (recursive)."""
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _clear_dir(root: Path) -> None:
    """Delete all files under root (the directory itself is kept)."""
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


@router.get("")
async def config(request: Request) -> dict:
    poster_dir = request.app.state.poster_dir
    return {
        "download_dir": request.app.state.download_dir,
        "poster_dir": poster_dir,
        "cache_bytes": _dir_bytes(Path(poster_dir)),
    }


@router.post("/clear-cache")
async def clear_cache(request: Request) -> dict:
    poster_dir = Path(request.app.state.poster_dir)
    _clear_dir(poster_dir)
    return {"cache_bytes": _dir_bytes(poster_dir)}
