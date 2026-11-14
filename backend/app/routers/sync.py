"""Cloud sync routes: manual sync + status.

POST /sync          -> pull cloud docs, push all local titles
GET  /sync/status   -> storage backend + pending count + last sync result
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, Request

from app.drive_auth import effective_backend, make_cloud_backend
from app.sync import pending_push_count, sync_all_cloud

router = APIRouter(prefix="/sync", tags=["sync"])


def _db_path(request: Request) -> str:
    return request.app.state.db_path


def _settings(request: Request) -> dict:
    """The settings table as a {key: value} dict (drives backend selection)."""
    conn = sqlite3.connect(_db_path(request))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


@router.post("")
async def sync(request: Request) -> dict:
    # make_cloud_backend returns None when Drive is unconnected or config
    # is incomplete; report "not configured" instead of erroring the route.
    backend = await asyncio.to_thread(make_cloud_backend, _db_path(request))
    if backend is None:
        return {"pushed": 0, "pulled": 0, "error": "sync not configured"}
    # to_thread: blocking sqlite (and, later, blocking Drive calls) off the
    # event loop.
    return await asyncio.to_thread(sync_all_cloud, backend, _db_path(request))


@router.get("/status")
def status(request: Request) -> dict:
    settings = _settings(request)
    conn = sqlite3.connect(_db_path(request))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM sync_status").fetchone()
    conn.close()
    return {
        # Effective backend (auto-selected when no explicit setting), so the
        # status reflects what is actually in use.
        "storage_backend": effective_backend(settings),
        "pending_count": pending_push_count(_db_path(request)),
        "last_sync": (
            {
                "last_sync_at": row["last_sync_at"],
                "last_push_at": row["last_push_at"],
                "pending_count": row["pending_count"],
                "last_error": row["last_error"],
            }
            if row
            else None
        ),
    }
