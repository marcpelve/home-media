"""Cloud sync routes: manual sync + status.

POST /sync          -> pull cloud docs, push all local titles
GET  /sync/status   -> sync mode + pending count + last sync result
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, Request

from app.sync import pending_push_count, sync_all_cloud
from app.storage import create_backend

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


@router.post("/sync")
async def sync(request: Request) -> dict:
    # No sync_mode / unknown backend -> create_backend raises ValueError;
    # report "not configured" instead of erroring the route.
    try:
        backend = create_backend(_settings(request))
    except ValueError:
        return {"pushed": 0, "pulled": 0, "error": "sync not configured"}
    # to_thread: blocking sqlite (and, later, blocking Drive calls) off the
    # event loop.
    return await asyncio.to_thread(sync_all_cloud, backend, _db_path(request))


@router.get("/sync/status")
def status(request: Request) -> dict:
    settings = _settings(request)
    conn = sqlite3.connect(_db_path(request))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM sync_status").fetchone()
    conn.close()
    return {
        "sync_mode": settings.get("sync_mode"),
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
