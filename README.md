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

## Trakt sign-in

Trakt sync is optional; the app works without it. To sign in (first time,
re-auth, or on a new machine), run:

    .venv/Scripts/python backend/trakt_auth.py

It prints a code and a URL. Open the URL in a browser, enter the code, and
approve. The tokens are then stored in `data/home_media.db`; the `client_id`
comes from `backend/.env` (`TRAKT_CLIENT_ID`). Re-run the command any time you
need to sign in again.

See `PLAN.md` for the full design and phased build plan.
See `STATE.md` for live progress. See `AGENTS.md` for working conventions.
