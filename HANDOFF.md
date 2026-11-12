# Project Handoff: home-media

**Date:** 2026-11-12 15:35
**Branch:** main
**Latest commit:** ce96d30 Bump version to 0.1.6

## Done
- Fixed Trakt 502: `TRAKT_CLIENT_ID_DEFAULT` constant in `backend/app/config.py` (public PKCE client, safe to embed as default) — no `.env` needed in the frozen build.
- Eliminated the console window: `#![windows_subsystem = "windows"]` in `desktop/src-tauri/src/main.rs`.
- Fixed sidecar "address already in use": probe-and-reuse logic in `desktop/src-tauri/src/lib.rs` — if :8790 is already serving, reuse it instead of spawning a sidecar that crashes.
- Fixed "localhost refused to connect": added `custom-protocol` to `desktop/src-tauri/Cargo.toml` features — without it, release builds navigate to `devUrl` (5173) instead of embedded assets.
- Fixed Trakt "Open link" not opening: replaced `<a target="_blank">` with a `<button>` calling `openExternal()` (the webview swallows `<a target=_blank>`).
- Fixed TMDB poster/backdrop proxy 500: `get_poster`/`get_image` in `backend/app/tmdb.py` now pass `headers={"Authorization": f"Bearer {self._effective_key()}"}` per-request (was reusing the httpx client's default header built from an empty token → `Illegal header value b'Bearer '`).
- Bumped 0.1.5 → 0.1.6 and pushed to `origin/main`.

## Current state
- v0.1.6 built (`desktop/src-tauri/target/release/desktop.exe` + sidecar `resources/home-media/home-media.exe`) and pushed to `origin/main`. Clean tree.
- Verified working in the frozen app: no console window, backend sidecar launches, Trakt device-code auth end-to-end, hero/poster/backdrop images load, "Open link" opens in the OS browser.
- **Sources BROKEN** — every title returns `{"sources":[],"count":0}`. Root cause is external (see Blockers), not a code regression.
- Dev servers (killed at handoff): backend `uvicorn app.main:app` on 8790, frontend `desktop/serve_nocache.py 8791` serving `desktop/dist/` (sync `src/`→`dist/` first). User opens `http://localhost:8791`. **Not 5173.**

## Blockers
- **Sources (external, not code):** the coco scrapers are walled.
  - `1337x.to` → HTTP 403 Cloudflare managed challenge ("Just a moment…"); unsolvable by cfscrape 1.2.60 (vendored), cfscrape 2.1.1, or curl_cffi (all tested).
  - `yts.mx`, `1337x.gs` → no DNS A record (dead/blocked; confirmed via Google DoH).
  - Confirmed it's the sites, not the build: the venv (the "old way") fails identically. This is a recent Cloudflare flip (worked days ago, not now), not gradual hardening.
- **Torbox search API (the clean fix) is unreachable:** `search-api.torbox.app` has no public A record (confirmed via Google + Cloudflare DoH). The search endpoints are NOT on the main `api.torbox.app` (404 with the key, via the backend's own httpx client, which CF accepts — `mylist` returns 14). So from this network we can't reach Torbox's search API.

## Next steps
1. Decide the sources path (user to choose): (a) chase the Torbox search API (confirm it's reachable from a different network/region, or ask Torbox), (b) headless-browser (Playwright + stealth) route to solve Cloudflare — high effort, low reliability, (c) park it.
2. If (a): wire `POST /sources/{id}` to call `https://search-api.torbox.app/torrents/tmdb:{id}` (Bearer = user's Torbox key) instead of `coco_driver.scrape()`. Torbox search returns torrents by TMDB/IMDB id with cache status.
3. Rebuild the backend (PyInstaller) → copy to `resources/home-media` → `cargo build --release` → verify sources return.

## Key decisions
- **`custom-protocol` feature** is required for release builds to serve embedded assets (Tauri `build.rs`: `dev = !custom_protocol`).
- **`openExternal()`** for OS-browser links; the webview swallows `<a target=_blank>`.
- **Per-request `Bearer` header** for the TMDB image proxy — never trust the httpx client's default header.
- **Sources root cause is external** — Cloudflare + dead DNS, not our code. Don't re-litigate.
- **Dev server:** backend 8790 + frontend 8791 (`serve_nocache.py` serving `dist/`); the frontend is NOT 5173 (that's the Tauri `devUrl`, unused in browser testing).
- **No secrets in handoffs** — reference by name only.
- Commit directly to main, concise messages, no AI attribution, no push/PR without being asked.

## References
- Repo: `C:\ai\repos\home-media` · GitHub: `https://github.com/marcpelve/home-media` · latest tag `v0.1.6`
- Version files: `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`
- Sources flow: `backend/app/routers/sources.py` → `backend/app/coco_driver.py` → `backend/app/coco/cocoscrapers/…` (1337x.py, ytsmx.py, client.py, cfscrape/)
- Torbox client: `backend/app/torbox.py` (base `https://api.torbox.app`; search is the separate `https://search-api.torbox.app`)
- Trakt client: `backend/app/trakt.py` · config: `backend/app/config.py`
- Frontend: `desktop/src/main.js` (`API = "http://127.0.0.1:8790"`) · `desktop/serve_nocache.py`
- Build: `cd backend && ./.venv/Scripts/pyinstaller.exe backend.spec --noconfirm` → copy `dist/home-media` → `desktop/src-tauri/resources/home-media` → `cd desktop/src-tauri && cargo build --release`
- Frozen config/data: `%LOCALAPPDATA%\HomeMedia\` (`.env` + `home_media.db`)
