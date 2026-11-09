# home-media - Progress Tracker

Lightweight native Windows media app to replace Kodi.
Design: PLAN.md  ·  Detailed log: STATE.md  ·  This file: live phase progress.

**8 / 9 phases done - P7 (packaging) in progress**

## Phases

- [x] P0a  Backend scaffold (FastAPI + SQLite + /health)
- [x] P0b  Tauri v2 desktop shell
- [x] P1   TMDB metadata (search, title, posters, cache)
- [x] P2   Cocoscrapers source-finding (standalone)
- [x] P3   Torbox debrid resolve + downloads
- [x] P4   mpv playback (JSON-IPC + watch progress)
- [x] P5   Trakt sync (device flow + PKCE, local fallback)
- [x] P6   Frontend (browse, search, detail, picker, playback, Trakt panel, keyboard nav)
- [-] P7   Packaging  <- IN PROGRESS

## P7 - current focus

- [x] PyInstaller backend `home-media.exe` (onefile, windowed) - builds + runs
- [x] Packaging-aware config (exe-relative `.env`, `data/`, `mpv.exe` when frozen)
- [x] Portable layout verified: `home-media.exe` + `.env` + `data/` (Trakt + cache load)
- [x] Tauri sidecar wired (`binaries/home-media`, `externalBin`, spawn on launch / kill on close)
- [x] Frontend `dist/` built for bundling
- [-] Tauri app + Windows installer build  <- IN PROGRESS
- [ ] Installer verified (launches app, backend sidecar runs)

## Legend

`[x]` done  ·  `[-]` in progress  ·  `[ ]` not started
