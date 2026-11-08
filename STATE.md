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
  disk-caching) + /tmdb router (search, home, categories, title, reviews,
  poster). Added tmdb_cache table and 7 MockTransport tests (no real API).
  All 11 tests pass.

## Next
- P2: Cocoscrapers standalone (kodi_stub) + sources endpoints.
- P3: mpv playback + Trakt/local tracking.
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
- Trakt client_secret not yet provided by user (needed for P4).
- Python is 3.14, not the 3.11 the plan assumed - verify native deps (cfscrape,
  pyaes) install cleanly on 3.14 when P1 lands.
- P0a worktree C:/ai/repos/home-media-p0a (branch p0a/backend-scaffold) is merged;
  can be removed with `git worktree remove`.
- P1 worktree C:/ai/repos/home-media-p1 (branch p1/tmdb) is merged; the venv
  (httpx+pytest) lives there. Remove with `git worktree remove` when done.
