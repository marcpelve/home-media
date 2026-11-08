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

## Next
- P0b: Tauri shell - native window + sidecar/process management. Needs Tauri
  CLI installed (not present yet).
- P1: TMDB metadata (search, home, categories, poster cache).
- P2: Cocoscrapers standalone (kodi_stub) + sources endpoints.
- P3: mpv playback + Trakt/local tracking.

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

## Blockers / notes
- Tauri CLI not installed (needed for P0b). Options: `npm create tauri-app`
  (pulls the CLI) or `cargo install tauri-cli`.
- Trakt client_secret not yet provided by user (needed for P4).
- Python is 3.14, not the 3.11 the plan assumed - verify native deps (cfscrape,
  pyaes) install cleanly on 3.14 when P1 lands.
- P0a worktree C:/ai/repos/home-media-p0a (branch p0a/backend-scaffold) is merged;
  can be removed with `git worktree remove`.
