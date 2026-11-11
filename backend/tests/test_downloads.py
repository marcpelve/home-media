"""DownloadManager: in-progress dedupe and title/episode removal.

Exercises the DB-direct + background-task code path with a mocked HTTP client
(no network, no real debrid).
"""

import asyncio
import sqlite3
from pathlib import Path

import httpx

from app.db import init_db
from app.downloads import DownloadManager


def _seed_title(db_path: Path, tmdb_id: int = 1) -> None:
    """Insert a title row so the downloads FK has a parent to point at."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title, year) "
        "VALUES (?, 'tv', 'T', 2020)",
        (tmdb_id,),
    )
    conn.commit()
    conn.close()


def test_download_start_dedupes_in_progress(tmp_path: Path):
    db_path = tmp_path / "dl.db"
    init_db(db_path)
    _seed_title(db_path)
    dest_dir = tmp_path / "downloads"
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"body"))
    )
    mgr = DownloadManager(db_path=db_path, dest_dir=dest_dir, client=http_client)

    async def run():
        async with mgr:
            # No await between starts, so the first row is still 'queued'.
            id1 = mgr.start("h1", "https://cdn.example.com/a.mkv",
                            title_id=1, episode=1, size_bytes=4, filename="a.mkv")
            id2 = mgr.start("h1", "https://cdn.example.com/a.mkv",
                            title_id=1, episode=1, size_bytes=4, filename="a.mkv")
            id3 = mgr.start("h2", "https://cdn.example.com/b.mkv",
                            title_id=1, episode=2, size_bytes=4, filename="b.mkv")
            return id1, id2, id3

    id1, id2, id3 = asyncio.run(run())
    asyncio.run(http_client.aclose())
    assert id1 == id2, "an in-progress download for the same (title, episode) is deduped"
    assert id3 != id1, "a different episode gets its own row"


def test_download_remove_for_episode(tmp_path: Path):
    db_path = tmp_path / "dl.db"
    init_db(db_path)
    _seed_title(db_path)
    dest_dir = tmp_path / "downloads"
    body = b"test file content"
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    mgr = DownloadManager(db_path=db_path, dest_dir=dest_dir, client=http_client)

    async def run():
        async with mgr:
            mgr.start("h1", "https://cdn.example.com/a.mkv",
                      title_id=1, episode=1, size_bytes=len(body), filename="a.mkv")
            mgr.start("h2", "https://cdn.example.com/b.mkv",
                      title_id=1, episode=2, size_bytes=len(body), filename="b.mkv")
            await mgr.wait_all()
            removed = mgr.remove_for_episode(1, 1)
            return removed, mgr.list()

    removed, rows = asyncio.run(run())
    asyncio.run(http_client.aclose())
    assert removed == 1
    assert all(r["episode"] != 1 for r in rows), "episode 1 row is gone"
    assert any(r["episode"] == 2 for r in rows), "episode 2 row remains"
    assert not (dest_dir / "a.mkv").exists(), "episode 1 file deleted"
    assert (dest_dir / "b.mkv").exists(), "episode 2 file kept"


def test_download_remove_for_title(tmp_path: Path):
    db_path = tmp_path / "dl.db"
    init_db(db_path)
    _seed_title(db_path)
    dest_dir = tmp_path / "downloads"
    body = b"test file content"
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    mgr = DownloadManager(db_path=db_path, dest_dir=dest_dir, client=http_client)

    async def run():
        async with mgr:
            mgr.start("h1", "https://cdn.example.com/a.mkv",
                      title_id=1, episode=1, size_bytes=len(body), filename="a.mkv")
            mgr.start("h2", "https://cdn.example.com/b.mkv",
                      title_id=1, episode=2, size_bytes=len(body), filename="b.mkv")
            await mgr.wait_all()
            removed = mgr.remove_for_title(1)
            return removed, mgr.list()

    removed, rows = asyncio.run(run())
    asyncio.run(http_client.aclose())
    assert removed == 2
    assert rows == [], "both rows for the title are gone"
    assert not (dest_dir / "a.mkv").exists()
    assert not (dest_dir / "b.mkv").exists()
