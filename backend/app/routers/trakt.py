"""Trakt auth + sync routes (Device Code Flow).

POST /trakt/auth  -> start a device login, returns user_code + verification_url
GET  /trakt/auth  -> poll the pending flow (pending|authorized|expired|none)
GET  /trakt/status-> auth state + last sync result
POST /trakt/sync  -> push local progress to Trakt + pull Trakt activity
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.sync import sync_all
from app.trakt import TraktAuthError

router = APIRouter(prefix="/trakt", tags=["trakt"])


def _client(request: Request):
    return request.app.state.trakt


def _db_path(request: Request) -> str:
    return request.app.state.db_path


@router.post("/auth")
async def start_auth(request: Request) -> dict:
    client = _client(request)
    try:
        return await client.start_device_login()
    except TraktAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"trakt device code request failed: {e}"
        )


@router.get("/auth")
async def poll_auth(request: Request) -> dict:
    client = _client(request)
    try:
        return await client.poll_pending()
    except TraktAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/status")
async def status(request: Request) -> dict:
    client = _client(request)
    tokens = client.load_tokens()
    username = tokens["username"] if tokens else None
    # Self-heal a placeholder username by fetching the real account.
    if tokens and (not username or username == "unknown"):
        real = await client.get_username()
        if real:
            client.save_tokens(
                real, tokens["access_token"], tokens["refresh_token"],
                tokens["expires_at"],
            )
            username = real
    conn = sqlite3.connect(_db_path(request))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM trakt_sync ORDER BY last_sync_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return {
        "authenticated": client.is_authenticated(),
        "username": username,
        "last_sync": (
            {
                "last_sync_at": row["last_sync_at"],
                "pending_count": row["pending_count"],
                "last_error": row["last_error"],
            }
            if row
            else None
        ),
    }


@router.post("/sync")
async def sync(request: Request) -> dict:
    client = _client(request)
    return await sync_all(client, _db_path(request))
