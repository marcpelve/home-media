"""Easy Trakt sign-in.

Run from the repo (with the backend venv):
    .venv/Scripts/python backend/trakt_auth.py

It starts the Trakt device-code flow, prints the code and URL to authorize in
a browser, then polls until the login completes and the tokens are saved to the
local DB. Re-run any time you need to sign in again - a new machine, a revoked
or expired token, etc.

The client_id comes from backend/.env (TRAKT_CLIENT_ID); tokens are stored in
data/home_media.db. This talks to Trakt directly, so the backend does not need
to be running.
"""
import asyncio
import sys
import time
from pathlib import Path

# Make `app` importable when run as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import load_config  # noqa: E402
from app.trakt import TraktClient  # noqa: E402


async def run() -> int:
    config = load_config()
    if not config.trakt_client_id:
        print("No TRAKT_CLIENT_ID set in backend/.env - cannot sign in.")
        return 1

    client = TraktClient(client_id=config.trakt_client_id, db_path=config.db_path)
    async with client:
        info = await client.start_device_login()
        print("=== AUTHORIZE IN BROWSER ===")
        print("Code:  ", info.get("user_code"))
        print("Visit: ", info.get("verification_url"))
        print("==============================")
        print("Waiting for you to approve (this can take a minute)...")

        while True:
            result = await client.poll_pending()
            status = result.get("status")
            if status == "authorized":
                print("Signed in as:", result.get("username"))
                return 0
            if status in ("expired", "none"):
                print("Sign-in ended ({}). Re-run to try again.".format(status))
                return 1
            # pending: keep polling at Trakt's suggested pace.
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
