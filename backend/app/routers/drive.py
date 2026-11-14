"""Drive OAuth routes.

POST /drive/connect -> start the browser flow (returns the URL to open)
GET  /drive/callback-> code arrives here from Google; store tokens
GET  /drive/status  -> connected / pending
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.drive_auth import (
    DriveAuthError,
    complete_connect,
    start_connect,
)

router = APIRouter(prefix="/drive", tags=["drive"])


def _db_path(request: Request) -> str:
    return request.app.state.db_path


def _get_setting(db_path: str, key: str) -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@router.post("/connect")
async def connect(request: Request) -> dict:
    db = _db_path(request)
    client_id = _get_setting(db, "drive_client_id")
    client_secret = _get_setting(db, "drive_client_secret")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="set drive_client_id and drive_client_secret first",
        )
    result = await asyncio.to_thread(start_connect, db, client_id, client_secret)
    return result


@router.get("/callback")
async def callback(request: Request) -> HTMLResponse:
    db = _db_path(request)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    client_id = _get_setting(db, "drive_client_id") or ""
    client_secret = _get_setting(db, "drive_client_secret") or ""
    try:
        await asyncio.to_thread(
            complete_connect, db, client_id, client_secret, code, state
        )
    except DriveAuthError as e:
        return HTMLResponse(
            content=f"<h1>Home Media</h1><p>{e}</p>", status_code=400
        )
    return HTMLResponse(
        content="<h1>Home Media</h1><p>Drive connected. You can close this window.</p>"
    )


@router.get("/status")
async def status(request: Request) -> dict:
    db = _db_path(request)
    access = _get_setting(db, "drive_access_token")
    refresh = _get_setting(db, "drive_refresh_token")
    pending_state = _get_setting(db, "drive_oauth_state")
    raw_expires = _get_setting(db, "drive_access_expires_at")
    expires_at = None
    if raw_expires:
        try:
            expires_at = float(raw_expires)
        except ValueError:
            pass
    return {
        "connected": bool(access and refresh),
        "pending": bool(pending_state),
        "expires_at": expires_at,
    }
