"""home-media backend entry point.

Run with:
    .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import load_config
from app.db import init_db
from app.tmdb import TmdbClient
from app.routers.tmdb import router as tmdb_router

# Load config at import; the DB path drives startup init. Secrets here are
# only read, never logged.
_config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the local DB (source of truth) on startup if it is absent.
    init_db(_config.db_path)
    # Create the TMDB client and store it on app state for routers.
    poster_dir = _config.download_dir or None
    app.state.tmdb_client = TmdbClient(
        token=_config.tmdb_read_access_token,
        db_path=_config.db_path,
        poster_dir=poster_dir,
    )
    async with app.state.tmdb_client:
        yield
    await app.state.tmdb_client.close()


app = FastAPI(title="home-media", lifespan=lifespan)
app.include_router(tmdb_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "home-media"}
