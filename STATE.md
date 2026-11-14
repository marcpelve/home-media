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
- (2026-11-09) P6 continued: Trakt sign-in / sync panel (commits 16e68b2,
  f9c7b32). Added a settings view reachable from a gear button in the top bar
  or the s key: shows Trakt auth status, the device-code sign-in flow (user
  code + activate link + polling), and a sync trigger. Backend: /trakt/status
  self-heals a stored "unknown" username by re-fetching /users/me; the sync
  pull now uses /users/{u}/history (history:read) instead of /progress/watched
  + /movies/watched, which need the scrobble:post scope the exo app lacks. Per-
  source errors are collected so one 403/405 no longer aborts the whole sync.
  85 backend tests pass; browser-verified the panel shows the connected user
  and a clean sync result.
- (2026-11-09) P7 packaging: one-dir sidecar + startup grace + installers.
  Migrated the PyInstaller backend from one-file to one-dir (fast startup,
  ~0s cold boot vs ~35s of extraction). The Tauri app now bundles the whole
  `resources/home-media/` folder (bundle.resources) and spawns the sidecar
  from resource_dir(); a dual-path spawn handles Tauri's folder layout. Built
  the MSI + NSIS installers. Added a frontend startup grace: the webview
  polls /health every 750ms with a "Starting" state and a Retry button, so a
  slower sidecar boot no longer shows a dead-end error. Committed across
  d3b579c (installer), 02f89d1 (one-dir), 83ed81f (startup grace).
- (2026-11-09) Offline library: browse + play downloaded content with no
  internet. Phase A: /tmdb/title now upserts durable metadata into the
  `titles` table on first sight. Phase B: new DB-direct /library and
  /library/file endpoints build the browsable library straight from
  downloads + watch_progress + titles (no TMDB calls), plus a library.py
  service. Phase C: a frontend Library screen (grid of cards -> title detail
  -> episode list with Play wired to POST /play). Phase D: a home "My
  Library" row via Promise.allSettled so one failed source never blocks the
  others. Phase E: empty/error states and honest episode labels. 3 new pytest
  tests (88 total pass); browser-verified the home row, library grid,
  title/episode view, and local playback (mpv launches with the file).
- (2026-11-10) P7 packaging finished (as far as possible without the user).
  Found the prior one-dir backend + MSI/NSIS installers were STALE: built
  Nov 9 04:11, before the offline library (routers/collection.py `/library`),
  the main.py router wiring, and the latest watch/library changes landed - the
  running exe 404'd on /library because of it. Rebuilt the one-dir backend
  (backend.spec, PyInstaller) and re-ran the Tauri MSI+NSIS build from current
  sources (Rust compiled in ~32s, mostly cached). Fixed a stale TMDB search
  test fixture (SEARCH_MOVIE/SEARCH_TV lacked a `popularity` field, so the
  popularity>=1.0 filter from commit b624424 dropped them and
  test_search_endpoint_shape failed); 111 tests now pass. Local clean-machine
  verification: fresh bundle boots in ~1s, /health ok, /library returns
  {"titles":[]} (offline route now present), /trakt/status degrades gracefully
  (no .env); NSIS confirmed to embed the fresh sidecar
  (resources/home-media/home-media.exe, exact byte match). Rust sidecar
  spawn (dual-path resource_dir) + kill-on-close present; frontend
  startup-grace (poll /health 750ms, 20s timeout, Starting/Retry) present.
  Remaining: clean-machine installer run (needs the user - handoff below).
- (2026-11-10) Fixed a windowed-build crash the local dev/test path never hit:
  on a clean machine the frozen one-dir exe (console=False) crashed at boot
  with `AttributeError: 'NoneType' object has no attribute 'isatty'` ->
  `ValueError: Unable to configure formatter 'default'`. Root cause: a windowed
  PyInstaller build leaves sys.stdout/sys.stderr as None, and uvicorn's default
  logging formatter calls .isatty() on them during configure_logging. The prior
  local verification ran the dev venv python (sys.stdout set), so it masked the
  bug. Fix: guard sys.stdout/sys.stderr -> os.devnull before `import uvicorn` in
  backend/server.py (no-op in console/dev mode). Rebuilt the one-dir backend +
  Tauri MSI+NSIS from current sources and verified the frozen windowed exe
  itself now boots: binds :8790, /health -> ok, /library -> {"titles":[]}. The
  clean-machine handoff below now applies to these fresh installers (NSIS/MSI
  Nov 10 14:10; sidecar byte-matched to the fixed exe).
- (2026-11-10) In-app auth for TMDB + Torbox (extends the Trakt settings
  pattern; no kanban task, direct implementation). Both clients now store
  their API key in the settings table with the .env value as fallback:
  `set_key`/`get_key`/`clear_key`/`_effective_key`/`is_configured`/`validate`
  on TmdbClient + TorboxClient. Requests send the effective key as a per-
  request Bearer header, so a key saved in-app takes effect immediately (no
  restart). New endpoints: GET /tmdb/status, POST /tmdb/auth (validate +
  store; 401 on a bad key, 400 on empty), DELETE /tmdb/auth; and GET
  /sources/status, POST /sources/auth, DELETE /sources/auth. Frontend: the
  settings view now renders TMDB + Torbox panels (key input + Save + "get a
  key" link when unconfigured; "Connected" + Remove when configured) plus a
  .key-input style. Keys are validated against the live API before storing
  (TMDB GET /configuration, Torbox GET /v1/api/torrents/mylist) and never
  logged; the API is 127.0.0.1-only. Settings-table key overrides the .env
  key (precedence). 12 new tests (123 total pass).
- (2026-11-10) Fixed the mouse hover/scroll feedback loop on the home rows
  and the episode grid (Kodi-style pointer-moved discriminator). Hovering a
  card moves focus + scrolls it into view, but that scroll left a different
  card under the stationary cursor, whose mouseenter re-focused and re-scrolled,
  chaining into a jitter loop. Added a pointerMoved flag (set on mousemove,
  cleared on each focus change) plus a 120ms scroll lock armed in setFocus /
  after the episode card.focus(). The card mouseenter handler now ignores
  hovers with no physical pointer move or within the lock, so scroll-induced
  re-hovers no longer change focus/scroll; a real mouse move still drives
  hover-to-navigate. Scoped to main.js (home rows + episode cards).
  Browser-verified both: real move -> focus/select, scroll-induced hover ->
  ignored, re-armed move -> focus/select.
- (2026-11-10) Service health UI: TMDB data endpoints return 503 "TMDB not
  configured" (not a raw 500) when no token is set. Top-bar status cluster
  shows one amber dot per unconfigured service (empty when all healthy);
  pure-CSS hover tooltip carries the message + an "Open Settings" button that
  jumps to the relevant panel. Home shows a contextual "set your TMDB key"
  note when TMDB is unconfigured. Refresh hooks on key save/remove + Trakt
  sign-in/out. 1 new test (124 total pass). Browser-verified the dot render
  + hidden-by-default tooltip.
- (2026-11-10) Service health dots slow-blink (commit 77d0064): a 2.4s
  ease-in-out opacity pulse on .svc-dot draws the eye to a problem service;
  the animation pauses on hover so the tooltip reads cleanly, and is disabled
  under prefers-reduced-motion.
- (2026-11-12) Sources working again + piratebay fix. The "no sources for
  every title" blocker was a transient window, not a code or network fault:
  the recent Cloudflare flip walled 1337x.to (confirmed a *managed* challenge
  - even curl_cffi with full Chrome/Safari/Firefox browser impersonation gets
  403 "Just a moment" from this IP), and the other trackers were flaky in the
  same window. Now the working providers are reachable: torrentio, kickass2,
  torrentdownload, knaben, bitcq all return results. Real e2e via POST
  /sources: Interstellar (157336) -> 102 sources, Shawshank (278) -> 53, all
  4K/DV/HDR, most cached=True on Torbox. Fixed a real bug in the vendored
  piratebay.py: a redundant local `from cocoscrapers.modules import log_utils`
  inside sources() shadowed the module-level import, so log_utils was unbound
  on the 200-response path and the trailing stats log raised
  UnboundLocalError, killing the whole provider (0 results). Removed the local
  import (commit 88b0242); piratebay now returns 80 sources for Interstellar.
  Bumped to 0.1.7.
- (2026-11-12) Continue Watching: one card per show, resume at the first
  unwatched episode. `GET /watch` (routers/library.py `watch_list`) now groups
  watch_progress rows by title: a TV show with several episodes in progress
  yields a single card whose resume target is the first (lowest season, then
  episode) not-yet-completed episode, and a show/movie whose started items are
  all complete drops out of the row entirely. The frontend (desktop/src/main.js)
  passes the card's resume season/episode through navPush -> openDetail so
  clicking the card opens that episode directly instead of re-deriving the
  most-recent one. Added test_watch_list_groups_by_show. Also fixed a
  pre-existing test bug in test_trakt.py (device-flow mock parsed JSON, but the
  client sends form-encoded bodies). 136 tests pass. Commits e14bc80 + 0cfaeaa.
- (2026-11-12) Sources in the FROZEN exe: bundle the coco tree as on-disk data
  so walk_packages finds the sources. The "no sources available" in the built
  app was NOT a Cloudflare window (the dev venv returned 96+33 results at the
  same time). Root cause: cocoscrapers.sources() discovers its per-source
  modules at runtime via walk_packages() — a FILESYSTEM walk of
  sources_cocoscrapers. When the package was bundled as PYZ modules
  (compressed in the archive, not on disk), walk_packages found none, so every
  source came back "disabled" and /sources returned 0 in the frozen exe while
  the dev venv (source tree on disk) worked. Fix in backend.spec: bundle the
  coco tree as ON-DISK data files (datas=[(app/coco/cocoscrapers,
  "cocoscrapers")], excludes=["cocoscrapers"]) so walk_packages can see the
  modules, and add xml.dom.minidom to hiddenimports (on-disk files are not
  statically analyzed, so the coco tree's stdlib imports must be listed
  explicitly). Rebuilt the one-dir backend (POST /sources/157336 -> 103
  results with Torbox cached flags) and the full Tauri app. Commit 9095fcf.
- (2026-11-13) P-M1 (Trakt -> cloud-storage sync migration, PLAN.md S16):
  schema retrofit. Added a nullable `device` column to `titles` and
  `watch_progress`: in `_SCHEMA` for fresh DBs, plus an idempotent
  `_migrate_device_column` called from `init_db` that, on a legacy DB, ALTERs
  the column in on the transition only (PRAGMA table_info check) and
  backfills the pre-existing NULL rows — device from `settings.device_id`
  (fallback `'default'`) and `updated_at` stamped to the migration baseline
  (clean cutover point for the first cloud push). Rows already carrying a
  device are never touched, and re-running init never re-stamps. 4 new tests
  (fresh schema, legacy migrate+backfill, settings device_id honored,
  idempotency). 140 tests pass. Commit 08b4dda.

- (2026-11-13) P-M2 (Trakt -> cloud-storage sync migration, PLAN.md S16):
  storage adapter. `backend/app/storage.py`: versioned per-title JSON doc
  (`build_doc`, `DOC_VERSION=1`, canonical UTC "Z" `updated_at` for LWW
  lexicographic compare), `make_key`/`parse_key` (`"movie:603"`),
  `StorageBackend` ABC (list/read/write/delete), `LocalFolderBackend`
  (root/movies|shows/{tmdb_id}.json, atomic tmp+os.replace writes), and
  `create_backend(settings)` factory (local now; drive in P-M2b).
  `DriveBackend`: Google Drive API v3 behind the same interface — root
  folder with movies/ + shows/ subfolders (folder-id cache), stateless
  name-based file search, multipart create/update, alt=media reads, 4xx/5xx
  -> RuntimeError. Two opencode runs (local model output budget): b3fc6c6
  (core + LocalFolder + 11 tests), 5a3fe05 (Drive + in-memory-Drive
  MockTransport tests, 7 tests). 158 tests pass.

## Session endpoint (2026-11-11) — GitHub release pipeline

State: repo pushed to GitHub (`marcpelve/home-media`), branch `main`.
`v0.1.0` release is live with the NSIS installer (`.exe` only — no updater
artifacts). `v0.1.1` tag is pushed but the CI run is **stuck in queued**
(GitHub Actions `windows-latest` runner pool congestion; both run attempts
are uncancelable via API).

### What works
- `v0.1.0` release: `Home.Media_0.1.0_x64-setup.exe` downloadable from
  `https://github.com/marcpelve/home-media/releases/tag/v0.1.0`
- Workflow builds the full Tauri + PyInstaller bundle successfully
- `gh auth` credential flow resolved (repo-local `credential.helper` set to
  `!gh auth git-credential`)

### What's missing for a complete release (v0.1.1)
1. **Updater artifacts** — `tauri-action` needs `tagName` set to generate
   `.sig` + `.nsis.zip` + `.nsis.zip.sig` (the files the Tauri updater
   downloads for self-updates). `tagName: ${{ github.ref_name }}` is now in
   the workflow; untested because the run is stuck.
2. **Updater manifest** — `latest.json` (or equivalent) needs to be
   uploaded alongside the artifacts so the in-app updater can discover
   new versions. `includeUpdaterManifest: true` is set on tauri-action;
   the manifest file needs to be picked up in the upload step.
3. **Asset upload** — the workflow creates the release via GitHub API
   (`curl`) then uploads assets via `gh release upload`. This path is
   untested end-to-end (the v0.1.0 release was created manually after
   multiple workflow iterations).
4. **v0.1.1 CI run** — stuck in `queued` since 2026-11-11 ~20:18 UTC.
   Two run attempts (43095230132, 43096541945) both uncancelable.
   Next step: once GitHub clears the queue (or the runs time out),
   verify the build produces all 4 NSIS artifacts + manifest, and that
   the release + assets appear on GitHub.

### Workflow file state (`.github/workflows/release.yml`)
- PyInstaller backend: `pip install -r requirements.txt` (fixed from
  `pip install -e .`)
- Resources staging: `mkdir -p` before `cp` (fixed missing dir)
- tauri-action: builds + signs; `tagName` restored for updater artifacts;
  does NOT handle release creation/upload (silently skips)
- Release creation: `curl` to GitHub API (creates release)
- Asset upload: `gh release upload` with `--clobber`
- Asset filenames are version-aware: the upload step derives `VERSION`
  from the tag (`${TAG#v}`), so no per-version edits are needed (the old
  "hardcoded to 0.1.1" note is stale - resolved).

### To do when the queue clears
1. Check if v0.1.1 run completed or timed out
2. If timed out: delete tag `v0.1.1`, re-push to retrigger
3. Verify release has all 4 assets + manifest
4. Test the in-app updater: install v0.1.0, check for update -> v0.1.1
5. (Done) The workflow derives the version from the tag - no further
   parameterization needed.

## Clean-machine handoff (the one thing that can't be done here)
Copy one installer to a fresh Windows box (no prior Home Media install, no
dev tools) and verify:
1. NSIS: `desktop/src-tauri/target/release/bundle/nsis/Home Media_0.1.0_x64-setup.exe`
   (or MSI: `desktop/src-tauri/target/release/bundle/msi/Home Media_0.1.0_x64_en-US.msi`).
   Run the installer.
2. Launch "Home Media". Expect a brief "Starting..." then the home screen
   (Continue Watching / Trending / My Library). The sidecar binds :8790 in ~1-2s.
3. Offline library works with no .env (empty until you download content);
   Trakt shows "not connected" until you add a .env next to the installed
   backend and sign in.
4. Close the window. Verify the backend exits: `netstat -ano | findstr 8790`
   shows nothing, and no stray home-media.exe remains in Task Manager.
Expected: clean install -> app launches to home -> closing the window kills
the backend (port 8790 released).

## Session endpoint (2026-11-10) — pick back up here
State: clean working tree. All servers spun down; unauthed test stack torn
down; scratch dir removed. 130/130 backend tests pass. dist/ synced with
src/.

Recent commits (branch trakt): a180bec (TMDB/Torbox in-app auth),
b38244f (hover/scroll pointer-moved guard — cleanly revertable),
24f971a (service health UI: 503 + top-bar dots + home note),
77d0064 (dots slow-blink), 6af3dbb (handoff docs).

Phase status: P0-P6 done; P7 packaging done pending the clean-machine
installer run (handoff above).

Next steps (corrected 2026-11-10 — verified against the code):
1. Clean-machine installer run (needs the user) — see the handoff above.

Done this session (Settings polish):
- Poster-cache size readout + Clear-cache button in Settings (GET /config,
  POST /config/clear-cache).
- Download dir defaults to <app>/data/downloads; posters to
  <app>/data/posters (decoupled from the download folder).
- Dropped: quality-pref (the source picker already surfaces quality/DV/HDR)
  and TMDB reviews (user declined).

Tabled (pending the user's own testing):
- Download-to-library UI — Download button in the detail view + a Downloads
  screen (progress / cancel / open-folder). Backend is ready
  (POST/GET/DELETE /downloads); the frontend is not wired yet.
- My Library end-to-end testing.

Resolved since the prior handoff:
- Source-picker quality sorting: done in coco_driver.py (quality desc
  4K>1080p, then Dolby Vision > HDR > no-HDR, then seeders/size;
  MIN_QUALITY_RANK=1080p drops sub-1080p).
- opencode version regression: gone — the installed binary reports 2.2.7.

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
