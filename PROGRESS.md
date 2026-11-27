# home-media - Progress Tracker

Lightweight native Windows media app to replace Kodi.
Design: PLAN.md  ·  Detailed log: STATE.md  ·  Latest handoff: HANDOFF.md  ·  This file: phase checklist.

**All build phases done. Current release: v0.2.1 (2026-11-15). Remaining: clean-machine installer run + end-to-end updater test.**

## Phases

- [x] P0a  Backend scaffold (FastAPI + SQLite + /health)
- [x] P0b  Tauri v2 desktop shell
- [x] P1   TMDB metadata (search, title, posters, cache)
- [x] P2   Cocoscrapers source-finding (standalone)
- [x] P3   Torbox debrid resolve + downloads
- [x] P4   mpv playback (JSON-IPC + watch progress)
- [x] P5   Trakt (device flow) - now a one-time history import (see S16 below)
- [x] P6   Frontend (browse, search, detail, picker, playback, settings, keyboard nav)
- [x] P7   Packaging (PyInstaller one-dir sidecar, NSIS installer, signed updater)
- [x] S16  Cloud-storage sync migration (Trakt sync -> Google Drive), P-M0..P-M7

## Releases

- [x] v0.1.0 (2026-11-11) - first GitHub release, NSIS installer only
- [x] v0.2.0 (2026-11-14) - cloud-storage sync; CI built + signed + published
      installer, .sig files, updater zip and latest.json
- [x] v0.2.1 (2026-11-15) - Torbox resolve fix, download path fix, Failed
      downloads UI, Stop-only player with download speed

## Cloud-storage sync (PLAN.md S16) - complete

- [x] P-M1 `device` column + idempotent backfill
- [x] P-M2 storage adapter: per-title JSON docs, LocalFolderBackend + DriveBackend
- [x] P-M3 sync engine: LWW pull/push, debounced ProgressPusher, 5-min loop
- [x] P-M4 Drive OAuth + settings API; minimal Sync panel (P-M4f/g)
- [x] P-M4h-k live fixes: root-folder bootstrap, resumable uploads,
      replace-on-update, selective parallel sync (~2s idle), per-title writer lock
- [x] P-M5 one-time Trakt import (ongoing Trakt sync dropped)
- [x] P-M6 cutover verification tests
- [x] P-M7 rollback runbook (PLAN.md S16)

## Offline library + downloads

- [x] Title metadata persisted to `titles` on first sight
- [x] DB-direct `/library` endpoints (no TMDB calls) + Library screen + home row
- [x] Watch / Download toggle in the source picker
- [x] Downloads screen: active (progress, speed, cancel) + Failed (remove, clear all)

## Settings + auth

- [x] TMDB + Torbox keys in-app (validated, stored in the settings table, .env fallback)
- [x] Trakt sign-in + Import from Trakt
- [x] Drive Connect + Sync now + last-sync / pending readout
- [x] Service-health dots in the top bar, poster-cache readout + clear, update check

## Security

- [x] Secrets gitignored, never logged; backend bound to 127.0.0.1
- [x] Browser-origin allowlist: CORS limited to the Tauri webview + local dev
      origins; requests with a foreign Origin get 403

## Open

- [ ] Clean-machine installer run (needs the user - see STATE.md handoff)
- [ ] In-app updater tested end-to-end (install older build -> update)
- [ ] Test v0.2.1 downloads end-to-end + failed-downloads UI (HANDOFF.md)
- [ ] Re-run the full backend suite after `abd0015` (and with test_cors.py)
- [ ] Known: intermittent ctypes access violation when seeking during playback (mpv.py)

## Legend

`[x]` done  ·  `[-]` in progress  ·  `[ ]` not started
