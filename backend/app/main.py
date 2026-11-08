"""home-media backend entry point.

Run with:
    .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import load_config
from app.db import init_db

# Load config at import; the DB path drives startup init. Secrets here are
# only read, never logged.
_config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the local DB (source of truth) on startup if it is absent.
    init_db(_config.db_path)
    yield


app = FastAPI(title="home-media", lifespan=lifespan)


# Sub-routers for PLAN S9 will be mounted here as they are implemented.
# e.g. app.include_router(titles.router, prefix="/api/titles")


@app.get("/health")
def health():
    return {"status": "ok", "app": "home-media"}
