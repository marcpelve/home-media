# Project Handoff: home-media

**Date:** 2026-11-27
**Branch:** fix/cors-and-docs (from main @ 916bf07, v0.2.1)

## Done
- CORS locked down (backend/app/main.py). The wildcard is gone: CORS allows only
  `tauri://localhost`, `http(s)://tauri.localhost` (the Tauri v2 webview) and
  `http(s)://localhost|127.0.0.1[:port]` (dev servers). A new
  `reject_foreign_origins` middleware returns 403 for any request whose
  Origin header is not on that list. Without it, CORS would still let
  body-less "simple" POSTs from any web page run. Requests with no Origin are unaffected.
- New `backend/tests/test_cors.py` covers the allowed origins, the refused ones
  (look-alike hosts, `null`), a foreign POST /play/quit blocked, no-Origin
  requests, and the webview preflight.
- Refreshed the stale docs: README (Drive sync, one-time Trakt import, desktop/
  layout), AGENTS (desktop/ not frontend/, Python versions, origin allowlist
  rule, the piratebay.py exception), PLAN (status + "As built" notes), PROGRESS
  (rewritten to current state), STATE (v0.2.0 / v0.2.1 / CORS entries,
  resolved sections marked).

## Verify before merging
1. Run the full suite on the dev box:
   `backend/.venv/Scripts/python -m pytest backend/tests -q`
   (test_cors.py has NOT been run yet - it was written in an environment
   without FastAPI).
2. Launch the installed or dev build and confirm the app still loads data. If the
   webview's origin is something other than `http://tauri.localhost`, every
   request will 403. In that case, check DevTools -> Network for the Origin
   header and add it to `ALLOWED_ORIGINS`.
3. Connect Drive once (or re-run Sync now) to confirm the OAuth callback still works.

## Next steps (carried over)
1. Test the v0.2.1 installer: downloads end-to-end and the Failed-downloads UI.
2. Clean-machine installer run + in-app updater end-to-end test.
3. Optional hardening: a Host-header check (127.0.0.1 / localhost) to block
   DNS-rebinding. It needs "testserver" allowed for TestClient.

## Resolved since the last handoff
- `desktop/src-tauri/build-tauri.bat` is committed (it landed in 8c843d5).

## References
- Local DB: `%LOCALAPPDATA%\HomeMedia\home_media.db` (the settings table holds
  Torbox/TMDB/Drive credentials, so never commit or print the values).
- Release: push a `v*.*.*` tag, and `.github/workflows/release.yml` builds, signs and publishes.
- Build: `cd desktop/src-tauri && cmd.exe /c build-tauri.bat`.
- PyInstaller: `cd backend && .venv/Scripts/python -m PyInstaller backend.spec --clean -y`.
