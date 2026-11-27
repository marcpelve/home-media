# home-media

A lightweight native Windows media app to replace Kodi.

- Netflix-style browsing (TMDB metadata: posters, titles, years, genres,
  reviews, trending, search)
- One-click play: Cocoscrapers source-finding + Torbox resolution
- Offline: download a title to local disk for offline playback
- Playback: mpv (JSON IPC)
- Tracking: a local SQLite source of truth on each machine, synced across
  machines through per-title files in Google Drive (`My Drive/HomeMedia/`).
  Works fully offline; changes push when the connection returns.
- Trakt: optional one-time import of your existing watch history.

Stack: Tauri v2 shell (Rust + system webview) with a vanilla-JS frontend in
`desktop/`, Python/FastAPI + SQLite backend in `backend/`.

## Trakt sign-in (for the one-time history import)

Trakt is optional; the app works without it. It is only used to import your
past watch history (Settings -> Import from Trakt). To sign in (first time,
re-auth, or on a new machine), run:

    .venv/Scripts/python backend/trakt_auth.py

It prints a code and a URL. Open the URL in a browser, enter the code, and
approve. The tokens are then stored in `data/home_media.db`; the `client_id`
comes from `backend/.env` (`TRAKT_CLIENT_ID`). Re-run the command any time you
need to sign in again.

See `PLAN.md` for the full design and phased build plan.
See `STATE.md` for the detailed log, `PROGRESS.md` for the phase checklist,
`HANDOFF.md` for the latest session handoff, and `AGENTS.md` for working
conventions.
