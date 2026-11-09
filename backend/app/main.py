"""home-media backend entry point.

Run with:
    .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import load_config
from app.db import init_db
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
    # Torbox debrid client + download manager, stored for the /sources and
    # /downloads routers.
    app.state.torbox_client = TorboxClient(
        api_key=_config.torbox_api_key, db_path=_config.db_path
    )
    app.state.downloads = DownloadManager(
        db_path=_config.db_path, dest_dir=_config.download_dir or None
    )
    # Local DB path for routers that persist (e.g. watch progress).
    app.state.db_path = _config.db_path
    # mpv is spawned lazily on the first /play; stopped on shutdown.
    app.state.mpv = MpvClient(mpv_path=_config.mpv_path)
    # Trakt client (device code flow) for watched tracking + sync.
    app.state.trakt = TraktClient(
        client_id=_config.trakt_client_id, db_path=_config.db_path
    )
    async with (
        app.state.tmdb_client,
        app.state.torbox_client,
        app.state.downloads,
        app.state.trakt,
    ):
        yield
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
app.include_router(sources_router)
app.include_router(torbox_router)
app.include_router(downloads_router)
app.include_router(play_router)
app.include_router(trakt_router)
app.include_router(library_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "home-media"}
