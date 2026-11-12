# Project Handoff: home-media

**Date:** 2026-11-12 04:20
**Branch:** main
**Latest commit:** 1ad76a0 Enable devtools feature in release builds

## Done
- Fixed the GitHub release pipeline through 7 iterations (zip→zipfile, sign→signer, unset env var, remove base64 -d, printf, tr -d whitespace, regenerate signing key pair). Release v0.1.5 is live with all 5 assets.
- Fixed fresh-install `PermissionError` on coco data dir: `_get_data_dir()` now uses `%LOCALAPPDATA%\Home Media\data\coco` (commit `5865b0e`).
- Fixed tooltip hover gap with CSS `.svc-tip::before` invisible bridge (confirmed working).
- Root-caused all JS Tauri API failures: `window.__TAURI__` is `undefined` in vanilla-JS Tauri webview. Added `tauriInvoke()` helper using `window.__TAURI_INTERNALS__.invoke()`.
- Fixed Torbox auth 422: swapped router mount order so `torbox_router` `/auth` matches before `sources_router` `/{tmdb_id}` catch-all.
- Fixed Trakt auth 502: OAuth device-code endpoints now use form-encoded bodies (`data=` + `_FORM_HEADERS`) instead of JSON.
- Added `app_get_version` and `open_url` Rust commands (both were missing — JS was calling non-existent commands).
- Added `open = "5"` crate dependency for the `open_url` command.
- Enabled devtools in both `tauri.conf.json` and `Cargo.toml` for release builds.
- Set up local build workflow with `run-build.bat` + Python venv at `backend/.venv`.

## Current state
- **Uncommitted changes** in working tree: `backend/app/main.py`, `backend/app/trakt.py`, `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/src/lib.rs`, `desktop/src/main.js`, `desktop/src-tauri/Cargo.lock`, `desktop/src-tauri/gen/schemas/*`
- Local build works: `desktop/src-tauri/target/release/desktop.exe`
- Verified in local build: get token/key buttons open browser, version shows in Updates panel, check button visible
- NOT yet verified: Torbox auth save, Trakt device-code auth, console window elimination
- Latest committed tag: `v0.1.5`

## Blockers
- None — all fixes are in place, need user verification + commit + push

## Next steps
1. User verifies Torbox auth save, Trakt auth, and console window in local build
2. Commit all changes
3. Bump version to 0.1.6, push to main, tag for release build
4. Verify release build + auto-update end-to-end

## Key decisions
- **`window.__TAURI__` is undefined in vanilla JS** — use `window.__TAURI_INTERNALS__.invoke(cmd, args)` for all Tauri API calls
- **Local build**: `cmd.exe /c "C:\Users\marcp\AppData\Local\hermes\cache\scratch\run-build.bat"`
- **Backend rebuild**: `cd backend && .venv/Scripts/python.exe -m PyInstaller backend.spec --noconfirm --distpath dist`, copy `dist/home-media` → `desktop/src-tauri/resources/home-media`
- **`desktop/dist/` is gitignored** — workflow's `npm run build` copies `src/` → `dist/`
- **User requires approval before push/build**
- **No secrets in handoffs** — reference by name only
- Commit directly to main, concise messages, no AI attribution, no push/PR without being asked

## References
- Repo: `C:\ai\repos\home-media`
- GitHub: `https://github.com/marcpelve/home-media`
- Latest release: `https://github.com/marcpelve/home-media/releases/tag/v0.1.5`
- Workflow: `.github/workflows/release.yml`
- Version files: `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`
- Rust commands: `desktop/src-tauri/src/lib.rs` (`check_for_update`, `install_update`, `app_get_version`, `open_url`)
- JS frontend: `desktop/src/main.js` (all Tauri calls via `tauriInvoke()`)
- Backend routers: `backend/app/routers/` (sources, torbox, trakt, tmdb, downloads, play, library, collection)
- Trakt client: `backend/app/trakt.py` (device-code flow, token rotation)
