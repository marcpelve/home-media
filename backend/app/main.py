"""home-media backend entry point.

Run with:
    .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import load_config
from app.db import init_db
from app.sync import sync_all
from app.tmdb import TmdbClient
from app.torbox import TorboxClient
from app.downloads import DownloadManager
from app.mpv import MpvClient
from app.trakt import TraktClient
from app.routers.tmdb import router as tmdb_router
from app.routers.sources import router as sources_router
from app.routers.torbox import router as torbox_router
from app.routers.downloads import router as downloads_router
from app.routers.play import router as play_router
from app.routers.trakt import router as trakt_router
from app.routers.library import router as library_router
from app.routers.collection import router as collection_router
from app.routers.config import router as config_router
from app.routers.sync import router as sync_router

# Load config at import; the DB path drives startup init. Secrets here are
# only read, never logged.
_config = load_config()

# Best-effort Trakt sync cadence. A sync is one history GET plus only the
# rows changed since the last push, so this stays far under Trakt's limits.
TRAKT_SYNC_INTERVAL_S = 1800


async def do_trakt_sync(app: FastAPI) -> None:
    """One best-effort sync; the local DB stays the source of truth on failure."""
    try:
        await sync_all(app.state.trakt, app.state.db_path)
    except Exception:
        pass


async def _trakt_sync_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(TRAKT_SYNC_INTERVAL_S)
        await do_trakt_sync(app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the local DB (source of truth) on startup if it is absent.
    init_db(_config.db_path)
    # Create the TMDB client and store it on app state for routers.
    # Posters cache in their own folder, decoupled from the download dir.
    app.state.tmdb_client = TmdbClient(
        token=_config.tmdb_read_access_token,
        db_path=_config.db_path,
        poster_dir=_config.poster_dir,
    )
    # Torbox debrid client + download manager, stored for the /sources and
    # /downloads routers.
    app.state.torbox_client = TorboxClient(
        api_key=_config.torbox_api_key, db_path=_config.db_path
    )
    app.state.downloads = DownloadManager(
        db_path=_config.db_path, dest_dir=_config.download_dir
    )
    # Local DB path for routers that persist (e.g. watch progress).
    app.state.db_path = _config.db_path
    # Download + poster dirs for the /config router (readout + clear cache).
    app.state.download_dir = _config.download_dir
    app.state.poster_dir = _config.poster_dir
    # mpv is spawned lazily on the first /play; stopped on shutdown.
    app.state.mpv = MpvClient(mpv_path=_config.mpv_path)
    # Trakt client (device code flow) for watched tracking + sync.
    app.state.trakt = TraktClient(
        client_id=_config.trakt_client_id, db_path=_config.db_path
    )
    sync_task = asyncio.create_task(_trakt_sync_loop(app))
    try:
        async with (
            app.state.tmdb_client,
            app.state.torbox_client,
            app.state.downloads,
            app.state.trakt,
        ):
            yield
    finally:
        sync_task.cancel()
    app.state.mpv.stop()


app = FastAPI(title="home-media", lifespan=lifespan)
# The desktop webview loads from a local origin and fetches this API
# cross-origin, so allow it. The server is bound to 127.0.0.1 only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(tmdb_router)
app.include_router(torbox_router)
app.include_router(sources_router)
app.include_router(downloads_router)
app.include_router(play_router)
app.include_router(trakt_router)
app.include_router(library_router)
app.include_router(collection_router)
app.include_router(config_router)
app.include_router(sync_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "home-media"}
