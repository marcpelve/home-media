# home-media - Design & Build Plan

A lightweight, purpose-built native media app to replace Kodi.
Netflix-style browsing, Cocoscrapers source-finding, Torbox resolve + offline
download, mpv playback, Trakt tracking with a local source-of-truth fallback.
Targets: 2 Windows machines.

Status: DESIGN (review) - no code yet.

------------------------------------------------------------------------
## 1. Goals

- Replace Kodi + Umbrella + Arctic Fuse 3 + Torbox addon with one light app.
- Netflix-like home: Continue Watching, Trending, Your Movies/TV, hero.
- One-click play: sources ranked + auto-resolved, no addon dialog juggling.
- Offline: download a title to local disk for offline playback.
- Tracking: local source of truth + Trakt sync across the 2 machines.
- Light footprint: a small native desktop app, not a media-server monolith.

## 2. Non-goals

- Not a general media server / not a multi-user backend.
- Not mirroring or redistributing any third-party catalog (see API policy in S8).
- Not a Linux/macOS build (Windows only, for now).
- Not a torrent client - all torrent resolution goes through Torbox.

## 3. Core design principles

1. Be a good API citizen. Read each provider's rate limits + policy. Cache
   aggressively, de-duplicate requests, honor 429 Retry-After with backoff +
   jitter. Never loop-retry. Keep traffic proportionate to real usage.
2. Local is the source of truth. The local SQLite DB owns watch progress,
   library, downloads, and settings. Cloud APIs (Trakt) are a sync/backup
   layer that degrades gracefully when offline or unauthenticated.
3. Offline-resilient. The app must be usable with no network (play local
   downloads, browse local library) and survive API outages / lost auth.
4. One scoped change at a time. Each build phase is a separate git worktree +
   one fresh `opencode run` (see S13).

## 4. Tech stack (final)

- Frontend: Tauri (Rust + system webview) + React + Tailwind. One small native
  .exe per machine, native window, spawns/controls the local backend + mpv.
- Backend: Python 3.11, FastAPI (async), SQLite (stdlib sqlite3). Runs as a
  local service bound to 127.0.0.1.
- Scrape: Cocoscrapers (standalone, see S8.4) -> best {hash, magnet, quality,
  size, seeders}.
- Resolve: Torbox API (official Python SDK / direct REST) -> direct stream URL
  or direct file URL (for download).
- Playback: mpv over JSON IPC (local socket). Plays a URL (online) or a local
  file path (offline).
- Track/sync: Trakt API (OAuth) + local DB source of truth.
- Browse/metadata: TMDB (v3 API key) for posters, titles, years, genres,
  trending, search, reviews.

## 5. Architecture

Components:
- Tauri shell (Rust) - native window, UI, process management (spawns backend
  as a sidecar .exe, spawns mpv).
- FastAPI backend (Python) - the brain: metadata, scrape, resolve, download
  manager, watch tracking, Trakt sync, mpv IPC.
- SQLite - local source of truth (see S7).
- mpv - child process, JSON IPC over a local socket.

Data flow (one play):
  1. UI browses a TMDB/Trakt row, user picks a title.
  2. UI -> backend POST /sources for the title.
  3. Backend calls Cocoscrapers -> ranked source list; caches it.
  4. User picks a source (or auto-best). UI -> backend POST /resolve {hash}.
  5. Backend: Torbox checkcached -> createtorrent (if not cached) -> requestdl
     (file URLs) or stream (stream URL). Caches debrid state.
  6. Backend -> mpv (JSON IPC) play the direct URL (online) or local file.
  7. Backend writes watch progress to local DB immediately (source of truth).
  8. Sync job pushes to Trakt when online -> other machine picks it up.

Offline play: same as above but step 5 uses requestdl to download the .mkv to
disk first (download manager), then step 6 plays the local file.

## 6. Repository layout

  home-media/
    PLAN.md                 this file
    AGENTS.md               conventions for AI-assisted work in this repo
    STATE.md                multi-session state (updated as phases progress)
    README.md               human overview
    .gitignore              secrets, venv, build artifacts
    frontend/               Tauri + React + Tailwind
      src/
        main.ts
        App.tsx
        screens/            Home, Search, TitleDetail, Downloads, Settings
        api/                typed client for the backend REST API
        state/              app state (rows, selected title, downloads)
      src-tauri/            Rust shell + sidecar/process management
    backend/
      app/
        main.py             FastAPI app + router wiring
        db.py               SQLite schema + access
        tmdb.py             TMDB client (cached)
        trakt.py            Trakt client (OAuth + sync, cached)
        torbox.py           Torbox client (resolve + download)
        coco/               Cocoscrapers (vendored, untouched) + kodi_stub.py
        coco_driver.py      builds data dict, calls source.sources(), ranks
        mpv.py              mpv JSON IPC wrapper
        downloads.py        download manager (queue, progress, resume)
        watch.py            watch progress (local source of truth)
        sync.py             Trakt sync engine (push/pull, backoff)
      config/               local config (gitignored)
    data/                   runtime data dir (SQLite, cache, downloads) - gitignored

## 7. Data model (SQLite, local source of truth)

  titles(id PK, tmdb_id, imdb_id, type[movie|tv], title, year, poster_path,
         backdrop_path, overview, runtime, genres JSON, first_air_date,
         status, added_at, updated_at)
  episodes(id PK, title_id FK, season, episode, title, overview, runtime,
           still_path, air_date)
  watch_progress(id PK, title_id FK, season NULL, episode NULL,
                 position_ms, duration_ms, completed, completed_at,
                 updated_at)               -- LOCAL SOURCE OF TRUTH
  sources_cache(id PK, title_id FK, hash, quality, size_gb, seeders,
                provider, scraped_at)       -- avoid re-scraping
  debrid_state(hash PK, cached, torbox_added, added_at, note)
  downloads(id PK, title_id FK, episode NULL, hash, file_path, size_bytes,
            status[queued|downloading|done|failed], progress, created_at,
            completed_at)
  trakt_sync(last_sync_at, pending_count, last_error)
  settings(key PK, value)

Watch tracking model (the key requirement):
- Every play/resume writes watch_progress to the local DB immediately. This
  is authoritative and works fully offline.
- A sync job (on change + periodic) pushes local changes to Trakt (POST
  history) and pulls Trakt watched/progress for the other machine, merging
  (union of watched; max progress per item; last-write-wins on ties).
- If Trakt is down or unauth: local keeps working; sync queue retries with
  backoff when the connection/auth returns. Nothing is lost.
- Compliance: storing the user's OWN watch history locally is explicitly
  permitted by Trakt's API Use Policy. We do NOT mirror Trakt's catalog
  (our catalog comes from TMDB).

## 8. API integrations (endpoints, limits, caching, policy)

### 8.1 TMDB (metadata)
- Auth: v4 Read Access Token sent as `Authorization: Bearer <token>` (free,
  from the TMDB account API settings page). This is TMDB's stated default;
  it gives identical access to the v3 api_key for all public reads, works on
  both /3/ and /4/ endpoints, and keeps the credential out of URLs (so it is
  not logged/cached/shared by proxies, CDNs, or history) - the least
  intrusive option. It never expires (do NOT regenerate it, which would
  invalidate the old one). We do NOT need the v4 user-access-token OAuth
  handshake - that is only for TMDB account features (watchlist/favorites/
  ratings), which we don't use; tracking is Trakt + local.
- Caching terms: TMDB forbids caching any TMDB data longer than 6 months.
  Our TTLs (days) are comfortably within that.
- Rate limits: legacy hard limits disabled; upper bound ~40 req/s against
  bulk scraping. Be conservative: cache + de-dupe.
- Caching:
  - Static metadata (title details, genres, posters) -> cache in SQLite
    `titles` with a long TTL (days); revalidate on demand.
  - Images -> download to local disk (cache dir) and serve locally; do not
    re-fetch. TMDB image CDN URLs are stable.
  - Trending/search -> short TTL (minutes) in-memory/DB.
- Best practice: batch by ID, reuse cached rows, never hammer the API.

### 8.2 Trakt (tracking + sync)
- Auth: OAuth 2.0 with NO client_secret. Trakt deprecated client_secret for
  user sign-in (server-to-server only; future apps may not get one). Register
  a free app at trakt.tv (needs a verified GitHub account) -> client_id only.
  - Flow: Device Code Flow (the fit for a desktop media center with no public
    https callback): POST /oauth/device/code -> show user_code +
    verification_url; user enters the code in a browser; poll POST
    /oauth/device/token at the returned interval until 200. No redirect_uri,
    no client_secret.
  - (PKCE Authorization Code Flow is Trakt's other no-secret option, but it
    requires an https:// redirect_uri and explicitly discourates
    localhost/custom schemes - the wrong shape for a local desktop app.)
  - Host: auth.trakt.tv (migrated from api.trakt.tv; enforced Feb 1 2027).
  - Tokens: access_token (7 days) + refresh_token stored locally per machine.
    Refresh tokens are SINGLE-USE: each refresh returns a new access+refresh
    pair and invalidates the old refresh token, so always rotate the stored
    tokens. Pre-migration (mid-July 2026) refresh tokens are dead - the user
    authorizes once per machine.
  - Same Trakt account on both machines -> watched history syncs through
    Trakt (each machine scrobbs to / pulls from the same account).
- Rate limits (current, from official docs):
  - Authed GET: 500 calls / 5 min.
  - Authed POST/PUT/DELETE: 1 call / second.
  - Unauth GET: 500 / 5 min (per client_id + IP).
- 429 handling: read Retry-After, pause; if absent, exponential backoff with
  jitter, bounded retries. Log X-Ratelimit + Retry-After for debugging.
- Endpoints used:
  - POST /users/{id}/items/history        mark watched (movie/episode)
  - GET  /users/{id}/movies/watched       watched movies
  - GET  /users/{id}/shows/{id}/episodes/watched
  - GET  /users/{id}/progress             continue-watching (in-progress)
  - GET  /users/{id}/watchlist           watchlist
- Caching: cache responses; images MUST be cached (policy requirement).
- Policy: do NOT scrape/mirror Trakt's catalog; local storage of the user's
  own watch history/ratings/lists is allowed. Requests come from each device
  directly (no shared proxy backend).

### 8.3 Torbox (resolve + download)
- Auth: Bearer API key. Base https://api.torbox.app, version v1.
- Rate limits (from OpenAPI spec):
  - General: 300 req/min per token.
  - POST /torrents/createtorrent: 60/hr for UNCACHED torrents, 300/min if
    already cached. -> Always checkcached first; only createtorrent when
    needed; cache debrid_state locally so we don't re-add.
- Flow:
  - GET  /torrents/checkcached {hash}     is it in debrid?
  - POST /torrents/createtorrent {hash}   add to debrid (if not cached)
  - GET  /torrents/requestdl {id}         direct HTTP file links (DOWNLOAD)
  - POST /stream/createstream + GET /stream/getstreamdata   (STREAM)
- Download-to-disk: requestdl returns direct file URLs; the download manager
  fetches the .mkv to local disk with progress + resume (HTTP Range).
- Caching: debrid_state (cached/added) in SQLite to avoid redundant calls.

### 8.4 Cocoscrapers (standalone) - the P1 spike
Verified from source (v1.0.32): it is pure Python. The ONLY files that touch
Kodi (`xbmc*`) are:
  - modules/control.py   (xbmc, xbmcgui, xbmcvfs, xbmcaddon) - NEEDED
  - modules/log_utils.py (import xbmc inside log funcs)            - NEEDED
  - service.py, windows/base.py, prowlarr.py                      - NOT imported
Everything else (client.py, source_utils.py, cache.py, dom_parser.py,
undesirables.py, and all sources_cocoscrapers/torrents/*.py) is stdlib +
pip (cfscrape, pyaes) with zero Kodi imports.

Approach (lowest risk - keep Cocoscrapers UNTOUCHED):
- Provide a `kodi_stub.py` that installs fake `xbmc`, `xbmcgui`, `xbmcaddon`,
  `xbmcvfs` modules into sys.modules BEFORE importing cocoscrapers:
  - xbmcgui.Window(10000) -> obj with getProperty/setProperty/clearProperty
    backed by an in-memory dict.
  - xbmcgui.Dialog / DialogProgress / WindowXMLDialog / ListItem /
    ControlProgress -> no-op objects; NOTIFICATION_* constants.
  - xbmcaddon.Addon('script.module.cocoscrapers') -> getAddonInfo('path' /
    'profile') return local dirs; getSetting/setSetting -> a local settings
    store (JSON) with sensible defaults (providers enabled).
  - xbmc: log() -> stdlib logging; Monitor -> no-op; getCondVisibility ->
    False; getInfoLabel -> ''; executebuiltin/executeJSONRPC -> no-op;
    sleep(ms) -> time.sleep(ms/1000).
  - xbmcvfs: translatePath -> identity; exists/delete/mkdir/mkdirs/File/
    rename -> os-based.
- pip install: cfscrape, pyaes.
- Driver (`coco_driver.py`): build the `data` dict from our metadata and call
  `source.sources(data, hostDict={})` per enabled source, then aggregate,
  filter (already done inside), dedupe by hash, and rank by (quality, seeders,
  size). Return ranked [{hash, magnet, quality, size, seeders, provider}].
- data dict:
  - movie: {title, year, imdb, aliases}
  - tv:    {tvshowtitle, season, episode, imdb, aliases, year}
- hostDict: pass {} (empty) - we don't use Kodi's host whitelist.

Acceptance (P1): given a real movie/episode, the driver returns a non-empty
ranked list of {hash, magnet, quality, size, seeders} without Kodi installed.

### 8.5 mpv (playback)
- Spawn mpv as a child process with `--input-ipc-server=<local socket>`.
- JSON IPC commands: load, play/pause, set position, get_property
  (time-pos, duration, pause), quit.
- Play either a direct URL (online) or a local file path (offline).
- Read back time-pos periodically -> update local watch_progress.
- Respawn on crash; health-check the socket.

## 9. Backend API surface (local FastAPI, 127.0.0.1 only)

  GET  /api/titles/{type}/trending
  GET  /api/titles/continue-watching
  GET  /api/titles/library
  GET  /api/search?q=
  GET  /api/titles/{id}
  POST /api/titles/{id}/sources          -> ranked source list (Cocoscrapers)
  POST /api/sources/resolve {hash}       -> {stream_url | file_urls} (Torbox)
  POST /api/downloads                    start offline download
  GET  /api/downloads
  DELETE /api/downloads/{id}
  POST /api/play {url | file_path}       -> launch mpv
  GET  /api/watch/{id}
  PUT  /api/watch/{id} {position_ms, duration_ms, completed}
  POST /api/trakt/login            start device-code flow -> {user_code, verification_url}
  GET  /api/trakt/status           auth state (pending/connected) + sync status
  POST /api/trakt/sync
  GET  /api/config
  PUT  /api/config

## 10. Frontend screens

- Home: hero (featured), Continue Watching row, Trending row, Your Movies/TV
  rows. Poster cards; resume chips on continue-watching.
- Search: typeahead, results grid (movie/tv tabs).
- Title detail: backdrop, poster, metadata, genres, reviews (TMDB); for TV,
  season/episode list; Play + Download buttons; source list (quality/size/
  seeders) shown when playing.
- Downloads: list with progress bars, cancel/remove, open-folder.
- Settings: TMDB key, Torbox key, Trakt login, download directory, quality
  prefs, cache size.

Styling: dark Netflix-like theme, Tailwind, poster rows with hover scale,
keyboard + mouse nav.

## 11. Config & secrets

- Local config: %APPDATA%/home-media/config.json (gitignored). Dev: a
  .env in backend/ (gitignored).
- Stored locally: TMDB api key, Torbox api key, Trakt client id + access/
  refresh token, download dir, quality prefs. (No client_secret - Trakt
  deprecated it for user sign-in; the app uses the Device Code Flow.)
- NEVER committed, NEVER printed in logs. .gitignore covers config/, data/,
  .env, venv, build output.
- Backend binds to 127.0.0.1 only; no public exposure.

## 12. Security

- Secrets local-only, gitignored, not logged.
- Backend loopback-only.
- mpv child process; IPC over a local socket (not network).
- Vendor Cocoscrapers read-only; no exec of untrusted input.

## 13. Build plan (each phase = one worktree + one fresh `opencode run`)

P0 Scaffold
  - git init, AGENTS.md, STATE.md, README.md, .gitignore.
  - Tauri shell (empty window) + FastAPI stub (health endpoint) + SQLite
    schema (S7) + mpv IPC helper stub.
  - Acceptance: `tauri dev` shows a window; backend /health returns 200; DB
    file created with schema.

P1 Cocoscrapers standalone (the spike - highest risk, do first after P0)
  - kodi_stub.py (fake xbmc/xbmcgui/xbmcaddon/xbmcvfs) + coco_driver.py.
  - Vendored Cocoscrapers (untouched) + pip cfscrape, pyaes.
  - Acceptance: driver returns ranked {hash, magnet, quality, size, seeders}
    for a real movie and a real episode, no Kodi.

P2 Torbox resolve + download manager
  - torbox.py (checkcached/createtorrent/requestdl/stream) + debrid_state
    cache.
  - downloads.py (queue, progress, resume via HTTP Range) writing to disk.
  - Acceptance: given a hash, resolve returns a usable stream/file URL; a
    download writes a complete .mkv to disk with progress + resume.

P3 mpv playback
  - mpv.py JSON IPC: play URL/local path, controls, read time-pos/duration,
    respawn on crash.
  - Acceptance: playing an online URL and a local file both work; progress
    read back updates watch_progress.

P4 Trakt tracking + sync
  - trakt.py (device-code login, single-use refresh-token rotation,
    mark-watched, watched/progress/watchlist) +
    sync.py (push/pull, merge, 429 backoff) + watch.py local source of truth.
  - Acceptance: marking watched locally persists offline; with auth, it
    syncs to Trakt and the other machine sees it; survives auth loss.

P5 Frontend UI
  - Screens (S10), Tailwind theme, backend API client, state.
  - Acceptance: browse -> search -> detail -> play -> download -> tracked,
    all through the UI.

P6 Packaging
  - PyInstaller backend .exe; bundle as Tauri sidecar; single Windows
    installer; config on first run; optional auto-update.
  - Acceptance: clean install on a fresh Windows machine; play + download +
    sync all work.

## 14. Risks & mitigations

- Cocoscrapers shim fragility -> fake the 4 Kodi modules, keep the library
  untouched; spike in P1 before building on it.
- Torbox createtorrent 60/hr uncached cap -> checkcached first, cache
  debrid_state, avoid redundant adds.
- TMDB/Trakt rate limits -> aggressive caching, de-dupe, backoff + jitter on
  429, proportionate traffic.
- mpv crashes -> respawn + socket health-check.
- Tauri + Python lifecycle on Windows -> backend as a managed sidecar .exe,
  spawned/killed with the shell.
- Secrets leak -> gitignored, loopback-only, never logged.

## 15. Open decisions (need user input before P0)

- Confirm repo is C:\ai\repos\home-media (already chosen).
- Confirm P0 -> P1 order (scaffold then spike) vs spike-first.
- Trakt app: user must register a free app (verified GitHub) to get
  client_id/secret.
- Torbox API key + plan tier (for P2 testing).
- TMDB API key (free, from themoviedb.org settings).
- Default download directory (e.g., a media folder on each machine).

## 16. Migration: Trakt -> Cloud-Storage Sync (appended)

Status: COMPLETE (2026-11-13). P-M0 decisions locked; P-M1..P-M6 executed
(this session); P-M7 runbook below. Supersedes the Trakt-sync portions of S7
(watch tracking model), S8.2 (Trakt endpoints), and P4 (Trakt tracking +
sync). The local DB stays the live source of truth; Trakt is reduced to a
one-time import; the shared record moves from Trakt to a per-title cloud
file behind a storage adapter.

Why: the Trakt dev app holds only `public offline_access` (no `scrobble:post`),
so push-to-Trakt is blocked with no self-serve or proven grant path, and
production access requires premium (rejected). A cloud file is offline-first,
portable, machine-agnostic (OAuth, no per-machine daemon), and swappable
(Drive -> B2 -> local folder).

Model:
- Local DB = live, offline-first, per-machine peer (NOT a disposable cache).
  It doubles as the offline outbox - no separate outbox file.
- Cloud = one file per title (metadata + watch state), the shared durable
  record. A fresh machine pulling the folder gets library + watch state.
- Sync = bidirectional last-write-wins merge by `updated_at`.
- Storage adapter (`backend/app/storage.py`): `LocalFolder` + `Drive`
  (OAuth, personal) backends behind one interface. Transport is swappable.

Phases (each stops cleanly; one commit per phase; no push to origin):

P-M0 Decisions (no code)
  - Confirm: Google Drive via OAuth (personal); folder = My Drive/HomeMedia/.
  - Confirm: stable per-machine device id (configured, not hostname).
  - Confirm: versioned per-title file format (metadata + watch state).
  - One-time manual: Google Cloud project + OAuth desktop client + enable
    Drive API. The app consumes the credentials.

P-M1 Schema retrofit (additive, safe)
  - Add `device` to watch_progress and titles (updated_at already in S7).
  - Backfill existing rows: updated_at = baseline now, device = machine id.
  - db.py: idempotent ALTER TABLE ADD COLUMN with defaults. No data loss.

P-M2 Storage adapter (new backend/app/storage.py)
  - Interface: list_titles(), read_title(key), write_title(key, data),
    delete_title(key) (optional).
  - LocalFolderBackend: per-title JSON in movies/, shows/.
  - DriveBackend: OAuth (personal) + Drive API, same folder layout.
  - Shared versioned per-title JSON schema. Config: backend + connection.
  - Tests: write->read round-trip (LocalFolder temp dir; Drive mocked).

P-M3 Sync engine (rewrite backend/app/sync.py)
  - pull(): cloud -> merge into DB (LWW by updated_at).
  - push(title_key): DB state -> cloud file.
  - merge(): LWW per title/episode (later updated_at wins).
  - Sync button = pull() + push pending. Debounce progress pushes.
  - Deprecate (not delete) the Trakt /scrobble/stop push path.
  - Tests: LWW cases, pull->DB, push->cloud (mock).

P-M4 Hooks + config (small)
  - Startup: pull() + merge (offline-safe).
  - State change: update DB (updated_at, device) + push(title_key) debounced.
  - Settings: backend selection, device id, Drive "Connect" (OAuth flow ->
    store refresh token), sync status.
  - Offline: changes land in DB, push on next successful sync.

P-M5 One-time Trakt import (preserve the past)
  - "Import from Trakt": get_history() -> map to titles + watch_progress by
    TMDB id -> write into DB (and thus the cloud). Reuses trakt.py.
  - After this, Trakt is optional (no ongoing sync).

P-M6 Cutover + verification
  - DB intact post-schema (titles + watch_progress rows unchanged).
  - Round-trip across two simulated machines (two LocalFolderBackends).
  - Offline: cloud down -> local works; changes persist; sync when back.
  - Import: Trakt history lands in DB. Conflict: LWW correct.
  - Add test_storage.py + test_sync.py; update test_trakt.py (import-only).

P-M7 Rollback / safety (runbook)
  Safety properties (why each layer is safe to roll back):
  - Schema additive: `device` was added via idempotent `ALTER TABLE ADD
    COLUMN` in init_db (P-M1); the P-M4 device backfill is also idempotent.
    Old builds ignore the new columns; no data can be lost by the schema.
  - Cloud additive: every storage operation is wrapped per-title and
    per-sync; a failed/absent backend (factory returns None) only skips the
    sync iteration. Cloud failures never write to the local DB. Deleting the
    cloud folder loses nothing locally — the local DB is the source of truth.
  - Trakt kept: the client (device-flow auth) and the one-time import
    (P-M5) remain; the removed ongoing-sync code is recoverable verbatim
    from git history (the parent of f752580).

  Rollback levels:
  - L1 — stop Drive sync (keep local-folder snapshot sync):
      `PUT /settings {"storage_backend": "local"}` (or delete the key).
      make_cloud_backend() then builds a LocalFolderBackend rooted at
      `data/storage` (machine-local, never shared), so the 5-minute loop
      keeps writing a local snapshot and nothing reaches Google Drive.
      No code change; effective on the next sync tick (~5 min).
  - L2 — stop Drive permanently: L1 + delete the Drive credentials from
      settings (drive_client_id/secret, drive_access_token/refresh_token,
      drive_root_folder_id). The /drive/* routes then report disconnected.
  - L3 — restore ongoing Trakt sync (only if Trakt's scrobble scope opens):
      `git revert f752580` restores the deprecated Trakt push/pull/loop and
      drops the one-time import; re-run the suite and re-merge.

  Phase commits (main, unpushed as of P-M7):
  - P-M1 08b4dda (device column + backfill)
  - P-M2 b3fc6c6 (storage adapter core), 5a3fe05 (DriveBackend)
  - P-M3 3e525a6 (sync engine), b663b87 (pusher + 5-min loop)
  - P-M4 0afa7cc (OAuth + settings), 3c45574 (wiring + device stamping)
  - P-M5 f752580 (one-time Trakt import; ongoing Trakt sync dropped)
  - P-M6 14ec160 (cutover verification tests)
  - P-M7 this section (doc-only)
  193 tests pass on main.

What does NOT change:
- Local DB tables (titles, watch_progress) - additive columns only.
- TMDB-id keying; watch.py dumb store; trakt.py client (kept for import).

Risks / open items:
- Google OAuth setup (project + client) is the one non-code manual step.
- LWW merge is the one design surface that most needs to be right.
- Refresh token copied to new machines (config) - same as existing auths.
- Drive rate limits non-binding at watch-state frequency; debounce keeps it low.
