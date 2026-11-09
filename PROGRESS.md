# home-media - Progress Tracker

Lightweight native Windows media app to replace Kodi.
Design: PLAN.md  ·  Detailed log: STATE.md  ·  This file: live phase progress.

**7 / 9 phases done - P6 (frontend) in progress**

## Phases

- [x] P0a  Backend scaffold (FastAPI + SQLite + /health)
- [x] P0b  Tauri v2 desktop shell
- [x] P1   TMDB metadata (search, title, posters, cache)
- [x] P2   Cocoscrapers source-finding (standalone)
- [x] P3   Torbox debrid resolve + downloads
- [x] P4   mpv playback (JSON-IPC + watch progress)
- [x] P5   Trakt sync (device flow + PKCE, local fallback)
- [-] P6   Frontend  <- IN PROGRESS
- [ ] P7   Packaging
- [ ] (deferred) Tauri sidecar / process management

## P6 - current focus

- [-] Library browsing (Continue Watching + Trending done; My List, hero pending)
- [x] Search + title detail view (vertical grid + category tabs)
- [x] Back navigation (history stack) + clickable home (logo)
- [-] Source picking + one-click play (picker UI + keyboard nav done; E2E blocked on Torbox key)
- [-] Playback + progress display (now-playing view done; E2E blocked on Torbox key)
- [x] Arrow-key / 10-foot keyboard navigation (home, search, detail, picker)
- [x] Trakt sign-in / sync panel (auth status, device-code flow, sync)

## Legend

`[x]` done  ·  `[-]` in progress  ·  `[ ]` not started
