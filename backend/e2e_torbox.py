"""Real end-to-end check for Torbox resolve + Range-resumed download.

Uses the live Torbox API (key from backend/.env, never printed). Resolves a
known-cached hash already in the account, gets a real download URL, then
downloads two 5 MB Range chunks (first + resume) to prove the URL supports
Range and resume works. Does NOT download the full file.

Run:  .venv/Scripts/python e2e_torbox.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import httpx

from app.config import load_config
from app.db import init_db
from app.torbox import TorboxClient

# Known-cached torrent already in the account (verified): CIA 2026 S01E12.
HASH = "13ae90d1d2ea2c388b5f49f8affbf937cda42d1e"
CHUNK = 5 * 1024 * 1024  # 5 MB


async def main() -> None:
    cfg = load_config()
    init_db(cfg.db_path)  # same as the app lifespan
    ok = True
    async with TorboxClient(api_key=cfg.torbox_api_key, db_path=cfg.db_path) as client:
        cached = await client.check_cached(HASH)
        print(f"check_cached: {cached}")
        if not cached:
            print("FAIL: hash not cached")
            return

        torrent = await client.find_in_account(HASH)
        if not torrent:
            print("FAIL: hash not found in account")
            return
        print(f"torrent_id: {torrent.get('id')}")
        files = torrent.get("files") or []
        print(f"files: {[(f.get('id'), f.get('name'), f.get('size')) for f in files]}")
        if not files:
            print("FAIL: no files")
            return

        url = await client.request_download(torrent["id"], files[0]["id"])
        if not url:
            print("FAIL: no download URL")
            return
        print(f"download_url: {url[:100]}...")

        dest = os.path.join(tempfile.gettempdir(), "e2e_torbox_test.bin")
        if os.path.exists(dest):
            os.remove(dest)

        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as hc:
            # First 5 MB
            r1 = await hc.get(url, headers={"Range": f"bytes=0-{CHUNK - 1}"})
            with open(dest, "wb") as f:
                f.write(r1.content)
            size1 = os.path.getsize(dest)
            print(f"after first 5MB (status {r1.status_code}): {size1} bytes")

            # Resume the next 5 MB
            r2 = await hc.get(url, headers={"Range": f"bytes={size1}-{size1 + CHUNK - 1}"})
            with open(dest, "ab") as f:
                f.write(r2.content)
            size2 = os.path.getsize(dest)
            print(f"after resume 5MB (status {r2.status_code}): {size2} bytes")

        ok = (r1.status_code == 206 and r2.status_code == 206
              and size1 > 0 and size2 > size1)
        print(f"\nRESULT: {'PASS' if ok else 'FAIL'} "
              f"(first={size1}, resumed={size2})")


if __name__ == "__main__":
    asyncio.run(main())
