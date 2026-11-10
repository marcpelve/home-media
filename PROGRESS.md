# home-media - Progress Tracker

Lightweight native Windows media app to replace Kodi.
Design: PLAN.md  ·  Detailed log: STATE.md  ·  This file: live phase progress.

**8 / 9 phases done - P7 (packaging) complete pending clean-machine installer run; offline library built**

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

## Legend

`[x]` done  ·  `[-]` in progress  ·  `[ ]` not started
