# home-media - Progress Tracker

Lightweight native Windows media app to replace Kodi.
Design: PLAN.md  ·  Detailed log: STATE.md  ·  This file: live phase progress.

**8 / 9 phases done - P7 (packaging) complete pending clean-machine installer run; offline library + TMDB/Torbox in-app auth built**

## Phases

- [x] P0a  Backend scaffold (FastAPI + SQLite + /health)
- [x] P0b  Tauri v2 desktop shell
- [x] P1   TMDB metadata (search, title, posters, cache)
- [x] P2   Cocoscrapers source-finding (standalone)
- [x] P3   Torbox debrid resolve + downloads
- [x] P4   mpv playback (JSON-IPC + watch progress)
- [x] P5   Trakt sync (device flow + PKCE, local fallback)
- [x] P6   Frontend (browse, search, detail, picker, playback, Trakt panel, keyboard nav)
- [-] P7   Packaging  <- all buildable work done; only clean-machine run remains

## P7 - Packaging (complete pending clean-machine run)

- [x] PyInstaller backend `home-media.exe` (one-DIR, windowed) - fast startup (~0s)
- [x] Packaging-aware config (exe-relative `.env`, `data/`, `mpv.exe` when frozen)
- [x] Portable layout verified: `home-media.exe` + `.env` + `data/` (Trakt + cache load)
- [x] Tauri sidecar wired (`resources/home-media` folder, spawn on launch / kill on close)
- [x] Frontend `dist/` built for bundling
- [x] Frontend startup grace (polls /health, "Starting" state, Retry button)
- [x] Tauri app + Windows installer built (MSI + NSIS) - **rebuilt from current
  sources 2026-11-10** (prior installers were stale: predated the offline library)
- [x] Backend tests green (111 pass) - fixed a stale TMDB search fixture
- [ ] Installer verified on a clean machine (needs the user - see STATE.md handoff)

## Offline library - browse + play downloaded content with no internet

- [x] Phase A: persist title metadata to `titles` on first sight (`/tmdb/title` upsert)
- [x] Phase B: DB-direct `/library` + `/library/file` endpoints (no TMDB calls)
- [x] Phase C: frontend Library screen (grid -> title detail -> episodes + Play)
- [x] Phase D: home "My Library" row (`Promise.allSettled`, offline-safe)
- [x] Phase E: empty/error states, honest episode labels

## In-app auth - TMDB + Torbox keys in the settings view

- [x] TmdbClient + TorboxClient: settings-table key (set/get/clear), per-request Bearer, .env fallback, `validate()`
- [x] Endpoints: `GET /tmdb/status` + `POST|DELETE /tmdb/auth`, `GET /sources/status` + `POST|DELETE /sources/auth`
- [x] Frontend settings panels (key input + Save + "get a key" link; Connected + Remove)
- [x] 12 new tests (123 total pass)

## Hover/scroll guard - pointer-moved discriminator (Kodi-style)

- [x] pointerMoved flag (mousemove) + 120ms scroll lock; hover only re-focuses on a real pointer move
- [x] Home rows: card mouseenter guard (breaks the scroll-induced re-hover loop)
- [x] Episode grid: same guard on card mouseenter (after card.focus())
- [x] Browser-verified home + episode: real move focuses, scroll-induced ignored, re-arm works

## Service health UI - "set your key" messaging + 503 degradation

- [x] Backend: TMDB data endpoints return `503 "TMDB not configured"` (not raw 500) when no token
- [x] Frontend: top-bar status cluster - one amber dot per unconfigured service; empty when all healthy
- [x] Pure-CSS hover tooltip: service name + message + "Open Settings" (jumps to the panel)
- [x] Home: contextual "set your TMDB key" note when TMDB is unconfigured
- [x] Refresh hooks on key save/remove + Trakt sign-in/out
- [x] 1 new test (124 total pass)
- [x] Dots slow-blink (2.4s pulse; pauses on hover; reduced-motion safe)

## Next steps (corrected 2026-11-10 — verified against the code)

- [ ] 1. Clean-machine installer run (needs the user — see STATE.md handoff)
- [ ] 2. Settings: download-dir / quality-prefs / cache-size controls
- [ ] 3. Detail view: render TMDB reviews (/tmdb/reviews endpoint exists)

Tabled (pending the user's own testing):
- [ ] Download-to-library UI (Download button in detail + Downloads screen;
      backend ready, frontend not wired)
- [ ] My Library end-to-end testing

Resolved since the prior handoff: source-picker quality sorting (done in
coco_driver.py: 4K>1080p, DV>HDR>no-HDR, seeders/size, MIN_QUALITY_RANK=1080p)
and the opencode version regression (binary now reports 2.2.7).

## Legend

`[x]` done  ·  `[-]` in progress  ·  `[ ]` not started
