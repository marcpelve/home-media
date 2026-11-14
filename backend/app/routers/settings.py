"""Sync settings API: backend selection, device id, Drive credentials/folder.

Tokens (drive_access_token, drive_refresh_token, ...) are written only by
the OAuth flow, never by this endpoint.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app.drive_auth import effective_backend
from app.sync import pending_push_count

router = APIRouter(prefix="/settings", tags=["settings"])

# Keys the frontend may set via PUT /settings. Tokens are excluded --
# they are written exclusively by the OAuth flow.
_WHITELIST = {
    "storage_backend",
    "storage_local_root",
    "device_id",
    "drive_client_id",
    "drive_client_secret",
    "drive_root_folder_id",
}


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


def _set_setting(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def view(db_path: str) -> dict:
    """Return the current settings as a flat dict for the frontend."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    # Read sync_status for last_sync fields.
    sync_row = conn.execute("SELECT * FROM sync_status").fetchone()
    conn.close()

    settings = {row["key"]: row["value"] for row in rows}
    access = settings.get("drive_access_token")
    refresh = settings.get("drive_refresh_token")

    last_sync = None
    if sync_row is not None:
        last_sync = {
            "last_sync_at": sync_row["last_sync_at"],
            "last_push_at": sync_row["last_push_at"],
            "pending_count": sync_row["pending_count"],
            "last_error": sync_row["last_error"],
        }

    return {
        # Report the backend actually in use (auto-selected when no explicit
        # setting is present) so the UI shows the truth, not the raw value.
        "storage_backend": effective_backend(settings),
        "storage_local_root": settings.get("storage_local_root"),
        "device_id": settings.get("device_id"),
        "drive_client_id": settings.get("drive_client_id"),
        "drive_root_folder_id": settings.get("drive_root_folder_id"),
        "drive_connected": bool(access and refresh),
        "sync": {
            "last_sync": last_sync,
            "pending_count": pending_push_count(db_path),
        },
    }


def update(db_path: str, updates: dict) -> dict:
    """Apply whitelisted settings; return the refreshed view.

    Raises ValueError for an invalid storage_backend value.
    """
    # Validate storage_backend before writing anything.
    if "storage_backend" in updates:
        val = updates["storage_backend"]
        if val not in ("local", "drive"):
            raise ValueError(f"storage_backend must be 'local' or 'drive'")

    for key, value in updates.items():
        if key in _WHITELIST:
            _set_setting(db_path, key, str(value))
    return view(db_path)


@router.get("")
async def get_settings(request: Request) -> dict:
    return view(_db_path(request))


@router.put("")
async def put_settings(request: Request, body: dict) -> dict:
    try:
        return update(_db_path(request), body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
