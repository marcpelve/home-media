"""Torbox client + download manager tests using httpx.MockTransport (no real
API calls)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import init_db
from app.torbox import TorboxClient
from app.downloads import DownloadManager
from app.routers.torbox import router as torbox_router
from app.routers.downloads import router as downloads_router

MOCK_KEY = "test_torbox_key"
HASH = "aabbccddeeff00112233445566778899aabbccdd"

CHECK_TRUE = {HASH: {"name": "Show S01E01", "size": 100, "hash": HASH}}
CHECK_FALSE: dict = {}
MYLIST = [
    {
        "id": 123,
        "hash": HASH,
        "name": "Show S01E01",
        "size": 100,
        "cached": True,
        "files": [
            {"id": 1, "name": "show.s01e01.mkv", "size": 100,
             "mimetype": "video/x-matroska"}
        ],
    },
    {"id": 999, "hash": "otherhash", "name": "Other", "size": 1, "files": []},
]
REQUEST_DL = "https://cdn.example.com/file.mkv"


def _torbox_handler(calls: list, check_payload: dict, mylist_payload: list,
                    dl_payload):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = str(request.url.path)
        if path.endswith("/checkcached"):
            return httpx.Response(
                200, json={"success": True, "data": check_payload})
        if path.endswith("/mylist"):
            return httpx.Response(200, json={"success": True, "data": mylist_payload})
        if path.endswith("/requestdl"):
            return httpx.Response(200, json={"success": True, "data": dl_payload})
        if path.endswith("/createtorrent"):
            return httpx.Response(200, json={"success": True, "data": {"id": 555}})
        return httpx.Response(404, json={"success": False, "data": None})
    return handler


@pytest.fixture
def torbox(tmp_path: Path):
    db_path = tmp_path / "tb.db"
    init_db(db_path)
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(
        _torbox_handler(calls, CHECK_TRUE, MYLIST, REQUEST_DL))
    http_client = httpx.AsyncClient(
        base_url="https://api.torbox.app",
        headers={"Authorization": f"Bearer {MOCK_KEY}"},
        transport=transport,
    )
    client = TorboxClient(api_key=MOCK_KEY, db_path=db_path, client=http_client)
    yield {"client": client, "calls": calls, "db_path": db_path,
           "http": http_client}
    asyncio.run(http_client.aclose())


# ---------------------------------------------------------------------------
# TorboxClient
# ---------------------------------------------------------------------------

def test_bearer_header_sent(torbox):
    calls = torbox["calls"]
    asyncio.run(torbox["client"].check_cached(HASH))
    assert calls, "expected at least one request"
    assert calls[0].headers.get("authorization") == f"Bearer {MOCK_KEY}"


def test_check_cached_true(torbox):
    assert asyncio.run(torbox["client"].check_cached(HASH)) is True


def test_check_cached_false(tmp_path: Path):
    db_path = tmp_path / "tb2.db"
    init_db(db_path)
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(
        _torbox_handler(calls, CHECK_FALSE, MYLIST, REQUEST_DL))
    http_client = httpx.AsyncClient(base_url="https://api.torbox.app",
                                    transport=transport)
    client = TorboxClient(api_key=MOCK_KEY, db_path=db_path, client=http_client)

    async def run():
        async with client:
            return await client.check_cached(HASH)

    assert asyncio.run(run()) is False


def test_find_in_account(torbox):
    torrent = asyncio.run(torbox["client"].find_in_account(HASH))
    assert torrent is not None
    assert torrent["id"] == 123
    assert torrent["files"][0]["id"] == 1


def test_find_in_account_none(torbox):
    assert asyncio.run(torbox["client"].find_in_account("nonexistent")) is None


def test_request_download_url(torbox):
    url = asyncio.run(torbox["client"].request_download(123, 1))
    assert url == REQUEST_DL


def test_debrid_state_short_circuit(torbox):
    """A second resolve for the same hash must not re-call checkcached."""
    client = torbox["client"]
    calls = torbox["calls"]

    async def run():
        await client.resolve(HASH)
        first = sum(1 for c in calls if "/checkcached" in str(c.url.path))
        await client.resolve(HASH)
        second = sum(1 for c in calls if "/checkcached" in str(c.url.path))
        return first, second

    first, second = asyncio.run(run())
    assert first == 1, "first resolve should hit checkcached once"
    assert second == 1, "second resolve should be served from debrid_state"


def test_resolve_shape(torbox):
    result = asyncio.run(torbox["client"].resolve(HASH))
    assert result["hash"] == HASH
    assert result["cached"] is True
    assert result["torrent_id"] == 123
    assert result["files"][0]["file_id"] == 1
    assert result["files"][0]["name"] == "show.s01e01.mkv"


# ---------------------------------------------------------------------------
# DownloadManager
# ---------------------------------------------------------------------------

def test_download_writes_file(tmp_path: Path):
    db_path = tmp_path / "dl.db"
    init_db(db_path)
    body = b"hello world this is a test file body"
    dest_dir = tmp_path / "downloads"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mgr = DownloadManager(db_path=db_path, dest_dir=dest_dir, client=http_client)

    async def run():
        async with mgr:
            mgr.start(HASH, "https://cdn.example.com/file.mkv",
                      size_bytes=len(body), filename="file.mkv")
            await mgr.wait_all()
            rows = mgr.list()
            dest = dest_dir / "file.mkv"
            return rows, dest

    rows, dest = asyncio.run(run())
    asyncio.run(http_client.aclose())
    assert len(rows) == 1
    assert rows[0]["status"] == "done"
    assert rows[0]["progress"] == 1.0
    assert dest.exists()
    assert dest.read_bytes() == body


def test_download_resume(tmp_path: Path):
    db_path = tmp_path / "dl.db"
    init_db(db_path)
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "file.mkv"
    dest.write_bytes(b"hello")  # 5 bytes already on disk

    seen_ranges: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_ranges.append(request.headers.get("range"))
        return httpx.Response(
            206, content=b"world",
            headers={"content-range": "bytes 5-9/10"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mgr = DownloadManager(db_path=db_path, dest_dir=dest_dir, client=http_client)

    async def run():
        async with mgr:
            mgr.start(HASH, "https://cdn.example.com/file.mkv",
                      size_bytes=10, filename="file.mkv")
            await mgr.wait_all()
            return mgr.list()

    rows = asyncio.run(run())
    asyncio.run(http_client.aclose())
    assert seen_ranges == ["bytes=5-"], "expected a Range resume header"
    assert dest.read_bytes() == b"helloworld"
    assert rows[0]["status"] == "done"
    assert rows[0]["progress"] == 1.0


def test_remove_download(tmp_path: Path):
    db_path = tmp_path / "dl.db"
    init_db(db_path)
    body = b"somefilebytes"
    dest_dir = tmp_path / "downloads"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mgr = DownloadManager(db_path=db_path, dest_dir=dest_dir, client=http_client)

    async def run():
        async with mgr:
            dl_id = mgr.start(HASH, "https://cdn.example.com/f.bin",
                              size_bytes=len(body), filename="f.bin")
            await mgr.wait_all()
            removed = mgr.remove(dl_id)
            return dl_id, removed, mgr.list()

    dl_id, removed, rows = asyncio.run(run())
    asyncio.run(http_client.aclose())
    assert removed is True
    assert rows == []
    assert not (dest_dir / "f.bin").exists()


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

@pytest.fixture
def torbox_app(tmp_path: Path):
    db_path = tmp_path / "app.db"
    init_db(db_path)
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(
        _torbox_handler(calls, CHECK_TRUE, MYLIST, REQUEST_DL))
    http_client = httpx.AsyncClient(base_url="https://api.torbox.app",
                                    transport=transport)
    client = TorboxClient(api_key=MOCK_KEY, db_path=db_path, client=http_client)
    mgr = DownloadManager(db_path=db_path, dest_dir=tmp_path / "downloads",
                          client=http_client)

    app = FastAPI()
    app.state.torbox_client = client
    app.state.downloads = mgr
    app.include_router(torbox_router)
    app.include_router(downloads_router)

    with TestClient(app) as tc:
        yield {"client": tc, "calls": calls}
    asyncio.run(http_client.aclose())


def test_resolve_endpoint(torbox_app):
    tc = torbox_app["client"]
    resp = tc.post("/sources/resolve", json={"hash": HASH})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is True
    assert data["torrent_id"] == 123
    assert data["download_url"] == REQUEST_DL
    assert data["file_id"] == 1


def test_start_download_endpoint(torbox_app):
    tc = torbox_app["client"]
    resp = tc.post("/downloads", json={"hash": HASH, "file_url": REQUEST_DL})
    assert resp.status_code == 200
    assert "id" in resp.json()
    # the download row was created
    listed = tc.get("/downloads").json()["downloads"]
    assert len(listed) == 1
    assert listed[0]["hash"] == HASH


def test_list_downloads_endpoint(torbox_app):
    tc = torbox_app["client"]
    resp = tc.get("/downloads")
    assert resp.status_code == 200
    assert "downloads" in resp.json()


def test_delete_download_endpoint(torbox_app):
    tc = torbox_app["client"]
    created = tc.post("/downloads", json={"hash": HASH, "file_url": REQUEST_DL})
    dl_id = created.json()["id"]
    resp = tc.delete(f"/downloads/{dl_id}")
    assert resp.status_code == 200
    assert tc.get("/downloads").json()["downloads"] == []
