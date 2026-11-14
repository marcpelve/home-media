"""home-media backend entry point.

Run with:
    .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import load_config
from app.db import init_db
from app.drive_auth import make_cloud_backend
from app.sync import ProgressPusher, pull, sync_all_cloud
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
from app.routers.drive import router as drive_router
from app.routers.settings import router as settings_router

log = logging.getLogger("app.main")

# Load config at import; the DB path drives startup init. Secrets here are
# only read, never logged.
_config = load_config()

# Cloud sync cadence: full push+pull every 5 min. The loop always runs;
# iterations with no connected backend (factory returns None) are skipped.
CLOUD_SYNC_INTERVAL_S = 300


async def _cloud_sync_loop(app: FastAPI) -> None:
    """Periodic full cloud sync (push + pull); failures are logged, never
    fatal.

    Each iteration builds a FRESH backend: the cached access token expires
    ~1h, so this long-lived loop must not reuse one backend (the factory
    also refreshes an expired token). None means Drive is not (yet)
    connected; skip the iteration and retry on the next tick.
    """
    while True:
        await asyncio.sleep(CLOUD_SYNC_INTERVAL_S)
        backend = await asyncio.to_thread(
            make_cloud_backend, app.state.db_path
        )
        if backend is None:
            continue
        try:
            await asyncio.to_thread(sync_all_cloud, backend, app.state.db_path)
        except Exception as e:
            log.warning("cloud sync failed: %s", e)


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
    # Cloud storage backend (P-M4b): make_cloud_backend returns a backend
    # with a valid token, or None when Drive is not connected — cloud sync
    # stays disabled until the user connects (local mode is unaffected).
    app.state.cloud_backend = make_cloud_backend(_config.db_path)
    # Pusher takes a factory, not a backend: it outlives any single access
    # token (which expires ~1h) and gets a fresh one per push. It can always
    # be created; the factory returns None when not connected.
    app.state.pusher = ProgressPusher(
        _config.db_path,
        lambda: make_cloud_backend(_config.db_path),
        delay_s=30.0,
    )
    # Startup pull: seed the local DB with the newest cloud docs. Best-effort
    # and offline-safe — a failed pull must not block startup.
    if app.state.cloud_backend is not None:
        try:
            await asyncio.to_thread(
                pull, app.state.cloud_backend, _config.db_path
            )
        except Exception as e:
            log.warning("startup cloud pull failed: %s", e)
    # Always started: the loop builds a fresh backend each iteration, so a
    # Drive connection made after startup is picked up on the next tick.
    cloud_task = asyncio.create_task(_cloud_sync_loop(app))
    try:
        async with (
            app.state.tmdb_client,
            app.state.torbox_client,
            app.state.downloads,
            app.state.trakt,
        ):
            yield
    finally:
        # Flush pending pushes before cancelling tasks so no progress is lost.
        await app.state.pusher.flush()
        cloud_task.cancel()
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
app.include_router(drive_router)
app.include_router(settings_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "home-media"}
