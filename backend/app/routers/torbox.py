"""Torbox resolve endpoint.

Mounted at /sources in main.py. POST /sources/resolve takes a hash, resolves it
against the debrid, and returns the account's files plus a real download URL
for the target file.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.torbox import TorboxClient

router = APIRouter(prefix="/sources", tags=["torbox"])


class ResolveRequest(BaseModel):
    hash: str
    file_id: int | None = None


def _get_client(request: Request) -> TorboxClient:
    return request.app.state.torbox_client


@router.post("/resolve")
async def resolve(req: ResolveRequest, request: Request):
    client = _get_client(request)
    result = await client.resolve(req.hash)

    # Attach a real download URL for the target file (first by default).
    if result.get("torrent_id") and result.get("files"):
        files = result["files"]
        target = next((f for f in files if f["file_id"] == req.file_id), files[0])
        url = await client.request_download(result["torrent_id"], target["file_id"])
        result["download_url"] = url
        result["file_id"] = target["file_id"]
    return result
