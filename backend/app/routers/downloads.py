"""Download management endpoints.

Mounted at /downloads in main.py. Start, list, and remove debrid file
downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.downloads import DownloadManager

router = APIRouter(prefix="/downloads", tags=["downloads"])


class DownloadRequest(BaseModel):
    hash: str
    file_url: str
    title_id: int = 0
    episode: int | None = None
    size_bytes: int | None = None
    filename: str | None = None


def _get_manager(request: Request) -> DownloadManager:
    return request.app.state.downloads


@router.post("")
async def start_download(req: DownloadRequest, request: Request):
    mgr = _get_manager(request)
    dl_id = mgr.start(
        req.hash,
        req.file_url,
        title_id=req.title_id,
        episode=req.episode,
        size_bytes=req.size_bytes,
        filename=req.filename,
    )
    return {"id": dl_id}


@router.get("")
async def list_downloads(request: Request):
    mgr = _get_manager(request)
    return {"downloads": mgr.list()}


@router.delete("/{dl_id}")
async def delete_download(dl_id: int, request: Request):
    mgr = _get_manager(request)
    if not mgr.remove(dl_id):
        raise HTTPException(status_code=404, detail="download not found")
    return {"deleted": dl_id}
