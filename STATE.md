# STATE.md - home-media

Goal: build a lightweight native Windows media app to replace Kodi.
Full design: PLAN.md. This file tracks multi-session progress.

## Done
- (2026-11-08) Design plan written (PLAN.md), grounded in TMDB/Trakt/Torbox docs.
- (2026-11-08) Repo initialized (branch main). Secrets stored in backend/.env,
  gitignored and verified ignored (git check-ignore + status).
- (2026-11-08) P0 split into P0a (backend scaffold) + P0b (Tauri shell).
- (2026-11-08) P0a: backend scaffold merged to main (commit 761f0da). FastAPI
  /health, .env loader (secrets never logged), 8-table SQLite schema created on
  startup via lifespan, mpv JSON-IPC stub (lands P3), requirements.txt. 3 pytest
  tests pass; /health returns 200; data/home_media.db created on boot.
- (2026-11-08) P0b: Tauri v2 desktop shell merged to main (commit 0d64271).
  Tauri app in desktop/ (vanilla frontend): a minimal shell that probes
  http://127.0.0.1:8790/health and shows backend status, plus Rust entry points
  and tauri.conf.json. Frontend builds to dist/; Rust side compiles (cargo
  build, dev profile, 13 MB desktop.exe). Added icon.ico (Tauri on Windows
  requires it for the resource file) and fixed .gitignore for desktop/target.
- (2026-11-08) P1: TMDB metadata merged to main (commit e0778ac). TmdbClient
  (Bearer auth, SQLite cache with day-level TTLs, in-flight de-dupe, poster
  poster disk-caching) + /tmdb router (search, home, categories, title, reviews,
  poster). Added tmdb_cache table and 7 MockTransport tests (no real API).
  All 11 tests pass.
- (2026-11-08) P2: Cocoscrapers standalone merged to main (commit 3199a14).
  Vendored the full Cocoscrapers package UNTOUCHED into
  backend/app/coco/cocoscrapers/ (64 .py). kodi_stub.py fakes xbmc/xbmcgui/
  xbmcaddon/xbmcvfs (pre-seeds all provider.* settings to 'true', in-memory
  window property, writable profile dir) PLUS a urllib3 DEFAULT_CIPHERS shim
  (cfscrape breaks on 3.14's modern urllib3). coco_driver.py builds the real
  data dict, calls every enabled source, dedupes by hash (case-normalized),
  ranks by quality>seeders>size, and is defensive about None/non-dict/numeric
  results. POST /sources/{tmdb_id} router fetches TMDB details then scrapes
  via asyncio.to_thread. 35 new tests (46 total pass). Real no-Kodi e2e: The
  Matrix 1999 -> 192 ranked sources, Breaking Bad S01E01 -> 84.
- (2026-11-08) P3: Torbox debrid resolve + downloads merged to main (commit
  f5cf360). TorboxClient (checkcached/mylist/requestdl/createtorrent,
  debrid_state SQLite cache, Bearer auth) + DownloadManager (async Range-
  resumed downloads, progress tracking, resume header) + /sources/resolve and
  /downloads routers, wired into the lifespan. 15 new tests (61 total pass).
  Real e2e: known-cached CIA S01E12 hash -> resolve -> real CDN URL -> two
  5 MB Range chunks (206: 5242880 then 10485760 bytes). Delegation to opencode
  was abandoned (two runs produced zero files); implemented directly.
- (2026-11-08) P4: mpv playback merged to main (commit 2a4d691). MpvClient
  (mpv JSON-IPC over a Windows named pipe: ctypes CreateFileW, overlapped
  ReadFile for timeouts, request_id matching with unsolicited-event
  buffering) + watch.py (local watch_progress upsert/get, source of truth) +
  /play router (play/pause/resume/seek/position/stop, sync endpoints run in
  the worker pool). Transport seam: MpvClient(transport=...) so unit tests use
  an in-memory transport; e2e drives a real mpv binary. 14 new tests (66 total
  pass). Real e2e: spawn mpv --idle, load a 12s test file, mpv answers during
  playback, progress persists. Known limitation: seeking during active
  playback triggers an intermittent Windows ctypes access violation in the
  overlapped pipe read (needs a native debugger to root-cause); not exercised
  in e2e.
- (2026-11-08) P5: Trakt (Device Code Flow + local tracking fallback) on branch
  trakt. TraktClient (Device Code Flow with PKCE - exo is a public client with
  no secret - plus Token Refresh Rotation), token storage in the settings
  table, graceful degradation when unauthenticated/offline, /trakt router
  (auth, status, sync) and sync.py (push local progress, pull Trakt activity,
  merge with local as source of truth: max position, union of completed). Easy
  sign-in CLI (backend/trakt_auth.py). 18/18 trakt tests pass; verified live.
- (2026-11-09) P6 in progress: frontend home screen. Added CORS middleware to
  main.py (the webview fetches the API cross-origin) and a /watch router
  (Continue Watching: local watch_progress rows enriched with cached TMDB
  metadata). Built the desktop home screen in vanilla JS (no bundler):
  Continue Watching row (with progress bars), Trending row (/tmdb/home),
  debounced search (movies + TV), and a title detail view with a Play button
  wired to sources -> resolve -> play. Verified in a real browser: home
  renders 20 trending cards, search "matrix" returns 30 results, detail shows
  The Matrix with overview + Play, Continue Watching shows a progress bar at
  the correct percentage. Also fixed /sources to read imdb_id from the
  the top-level TMDB title object (it was looking in external_ids).
- (2026-11-09) P6 continued: source picker, now-playing view, and 10-foot
  keyboard navigation (commit 4ef77b7). Built the source picker overlay
  (quality/size/seeders/provider rows with 4K/HD badges) and a now-playing
  view (position polling via /play/position; pause/resume/seek/stop). Added a
  focus-based keyboard layer for home-theatre use (small bluetooth keyboard):
  one focused item at a time, Left/Right within a row, Up/Down between rows,
  Enter/Space activates, Esc goes back, "/" focuses search; a .focused ring
  marks the current item and a hint bar shows the controls. Fixed a picker bug
  where the panel was built but never attached to the overlay (rows never
  rendered). Browser-verified: home/search/detail/picker all navigate by
  keyboard; source rows render and step with Up/Down. E2E playback is blocked
  on a real Torbox API key (resolve is API-gated; the scrape itself works).
- (2026-11-09) P6 continued: search UX + real back navigation (commit 25a3b40).
  Search results are now a vertical CSS grid (auto-fill/minmax) with category
  tabs (All/Movies/TV) at the top instead of one horizontal row per category;
  tabs filter the already-fetched results in memory (no extra network calls).
  Added a lightweight history back-stack using native pushState/popstate plus a
  small bounded in-memory data cache, so Back returns to the previous screen
  and scroll position (search -> show -> back returns to the search, not home)
  rather than always going home. The logo is now a clickable home button, and a
  back arrow appears in the top bar on non-home views. Keyboard nav now handles
  the grid 2D (Left/Right across a visual row, Up/Down between rows).
  Browser-verified: grid + tabs, category filtering (10+20=30), card -> detail
  -> Back -> search -> Esc -> home, and 2D grid arrow movement. Kept light:
  CSS grid (no JS layout), lazy poster images, in-memory data cache, native
  history API - no framework.

## Next
- [-] P6: Frontend (home screen done; source picking, playback UI, Trakt panel pending)
- P7: Packaging.
- (deferred) Tauri sidecar/process management - let the Tauri app own the
  backend lifecycle instead of starting it separately.

## Decisions
- TMDB auth: v4 Read Access Token via `Authorization: *** header (not the v3
  query-param key). Identical access, keeps the secret out of URLs, never
  expires. Cache TMDB data < 6 months (their terms).
- Local SQLite = source of truth for watch/library/downloads. Trakt = sync
  layer on top (degrades gracefully offline). Compliant with Trakt API Use
  Policy (local storage of the user's own watch history is permitted).
- Cocoscrapers standalone: fake the 4 Kodi modules (xbmc, xbmcgui, xbmcaddon,
  xbmcvfs) via kodi_stub.py; keep the library untouched.
- Python 3.14 (only version on this box).
- P0a before P0b: the backend is the brain and is verifiable without Rust.
- DB path is <repo-root>/data/home_media.db (config.py resolves 3 parents up
  from app/config.py). main.py creates it on startup via the lifespan.

## Already tried / didn't work
- Delegating P0a to opencode: it wrote all files and passed 3 tests, but wedged
  on a FOREGROUND `uvicorn` start (the command never returns). Killed the server
  PID and the run, then verified (pytest, /health, DB) and committed/merged
  myself. Lesson: a task spec that starts a server must say "start it in the
  background and kill it after checking".
- P0b `cargo build` compiled all 445 deps then failed at the end: Tauri on
  Windows requires `icons/icon.ico` (the scaffold only made icon.png). Wrapped
  the PNG in a valid ICO container (PNG-in-ICO) and re-ran - deps were cached,
  so the re-build was 18s. The native build needs the MSVC env sourced
  (vcvars64.bat); drive it via a .bat file, not a bare `cargo build`.
- Running `opencode run` as a FOREGROUND terminal command hits the 180s Hermes
  timeout and gets killed (happened twice on P1). Launch it with
  background=true. Partial work survives on disk, so a killed run can be
  continued - the P1 code and tests were complete by the time of the kill; I
  just re-verified (pytest) and committed.

## Blockers / notes
- Tauri CLI installed (npm -g @tauri-apps/cli, v2.12.4) and on PATH.
- Trakt auth uses Device Code Flow (no client_secret - deprecated by Trakt).
  Needs a free trakt.tv app (client_id) registered with a verified GitHub
  account; the user enters a code once per machine. Details in PLAN.md.
- Python is 3.14 (not the 3.11 the plan assumed). RESOLVED in P2: pyaes
  installs fine; cfscrape (pip AND the bundled copy) breaks on 3.14's modern
  urllib3 (removed DEFAULT_CIPHERS) - fixed with a shim in kodi_stub.py that
  restores DEFAULT_CIPHERS before import. cfscrape is only ever imported as the
  bundled submodule (cocoscrapers.modules.cfscrape), so no top-level cfscrape
  is needed (the broken pip one was uninstalled).
- P2 worktree C:/ai/repos/home-media-p2 (branch p2/cocoscrapers) is merged; the
  venv (fastapi+httpx+pytest+pyaes) lives there. Remove with `git worktree remove`.
- P0a worktree C:/ai/repos/home-media-p0a (branch p0a/backend-scaffold) is merged;
  can be removed with `git worktree remove`.
- P1 worktree C:/ai/repos/home-media-p1 (branch p1/tmdb) is merged; the venv
  (httpx+pytest) lives there. Remove with `git worktree remove` when done.
- P3 worktree C:/ai/repos/home-media-torbox (branch torbox) is merged; the venv
  (fastapi+httpx+pytest+urllib3) lives there. Remove with `git worktree remove`.
- P4 worktree C:/ai/repos/home-media-mpv (branch mpv) is merged; the venv
  (fastapi+httpx+pytest) lives there. Remove with `git worktree remove`.
- mpv binary lives at C:/ai/mpv/mpv.exe (v0.42.0, portable with DLLs in
  C:/ai/mpv/mpv/). config.py `mpv_path` defaults to it.
