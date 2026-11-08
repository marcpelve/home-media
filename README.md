# home-media

A lightweight native Windows media app to replace Kodi.

- Netflix-style browsing (TMDB metadata: posters, titles, years, genres,
  reviews, trending, search)
- One-click play: Cocoscrapers source-finding + Torbox resolution
- Offline: download a title to local disk for offline playback
- Playback: mpv (JSON IPC)
- Tracking: Trakt sync across devices, with a local SQLite source of truth
  so it keeps working offline / without auth

Stack: Tauri (Rust + system webview) frontend, Python/FastAPI + SQLite
backend.

See `PLAN.md` for the full design and phased build plan.
See `STATE.md` for live progress. See `AGENTS.md` for working conventions.
