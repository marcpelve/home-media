"""Playback endpoints.

Mounted at /play in main.py. Launch mpv (online URL or local file), control
it (pause/resume/seek/quit), read back position, and persist progress to the
local watch_progress source of truth.

These are sync endpoints: MpvClient methods block on the IPC pipe, and
FastAPI runs sync endpoints in a worker thread.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import watch
from app.mpv import MpvClient, MpvError
from app.sync import push_key_for

router = APIRouter(prefix="/play", tags=["play"])


class PlayRequest(BaseModel):
    url: str | None = None
    file_path: str | None = None
    start_seconds: float | None = None


class SeekRequest(BaseModel):
    seconds: float


class ProgressRequest(BaseModel):
    title_id: int
    season: int | None = None
    episode: int | None = None


# Auto-mark a title/episode watched once this fraction of the runtime has
# elapsed - covers skipping end credits without a manual mark.
COMPLETE_FRACTION = 0.90


def _client(request: Request) -> MpvClient:
    return request.app.state.mpv


def _target(req: PlayRequest) -> str:
    target = req.url or req.file_path
    if not target:
        raise HTTPException(status_code=400, detail="url or file_path required")
    return target


def _safe_position(client: MpvClient) -> dict:
    try:
        return client.read_position()
    except MpvError:
        return {"time_pos": None, "duration": None, "pause": None}


@router.post("")
def play(req: PlayRequest, request: Request):
    client = _client(request)
    target = _target(req)
    client.ensure_running()
    client.play(target)
    # Resume: seek to the saved position. A seek before the file loads is a
    # no-op in mpv, so wait for it to load first.
    if req.start_seconds and req.start_seconds > 0:
        client.wait_loaded()
        client.set_position(req.start_seconds)
    return {"playing": target, "position": _safe_position(client)}


@router.get("/position")
def position(request: Request):
    client = _client(request)
    if not client.is_alive():
        return {"playing": False}
    return {"playing": True, **_safe_position(client)}


@router.post("/pause")
def pause(request: Request):
    _client(request).pause()
    return {"paused": True}


@router.post("/resume")
def resume(request: Request):
    _client(request).resume()
    return {"paused": False}


@router.post("/seek")
def seek(req: SeekRequest, request: Request):
    _client(request).set_position(req.seconds)
    return {"seeked_to": req.seconds}


@router.post("/progress")
def save_progress(req: ProgressRequest, request: Request):
    client = _client(request)
    pos = client.read_position()
    position_ms = int((pos.get("time_pos") or 0) * 1000)
    duration_ms = int((pos.get("duration") or 0) * 1000)
    completed = duration_ms > 0 and position_ms >= COMPLETE_FRACTION * duration_ms
    row = watch.upsert_progress(
        request.app.state.db_path,
        req.title_id,
        position_ms,
        duration_ms,
        completed=completed,
        season=req.season,
        episode=req.episode,
    )
    # Debounced cloud push: one write per title per quiet period.
    pusher = getattr(request.app.state, "pusher", None)
    if pusher is not None:
        key = push_key_for(request.app.state.db_path, req.title_id)
        if key:
            pusher.schedule(key)
    return row


@router.post("/quit")
def quit(request: Request):
    _client(request).stop()
    return {"stopped": True}
