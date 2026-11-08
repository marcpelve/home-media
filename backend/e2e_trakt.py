"""Real-API e2e gate for the Trakt client (Device Code Flow).

Requests a device code from the real Trakt API - verifies the client_id and
auth host work without user interaction. Full auth (poll -> token) needs the
user to enter the code in a browser, so that step is not exercised here.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.trakt import TraktClient  # noqa: E402


def _client_id() -> str:
    cid = os.environ.get("TRAKT_CLIENT_ID", "").strip()
    if cid:
        return cid
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("TRAKT_CLIENT_ID="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    client_id = _client_id()
    if not client_id:
        print("SKIP: TRAKT_CLIENT_ID not set")
        return 0

    db_path = Path(__file__).parent / "e2e_trakt.db"

    async def run():
        client = TraktClient(client_id=client_id, db_path=db_path)
        async with client:
            return await client.start_device_login()

    result = asyncio.run(run())
    user_code = result.get("user_code")
    verification_url = result.get("verification_url")
    assert user_code, f"no user_code in device login: {result}"
    assert verification_url, f"no verification_url in device login: {result}"
    print(f"PASS: device code flow works. user_code={user_code} url={verification_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
