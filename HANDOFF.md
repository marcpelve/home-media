# Project Handoff: home-media

**Date:** 2026-11-12 22:15
**Branch:** main
**Latest commit:** 2a1e0bd (STATE.md: record frozen-exe sources fix)

## Done
- Fixed "no sources available" in the FROZEN exe. Root cause: `cocoscrapers.sources()` discovers per-source modules at runtime via `walk_packages()` (a filesystem walk of `sources_cocoscrapers`); when the package is bundled as PYZ modules (compressed in the archive, not on disk) it finds none → every source "disabled" → `/sources` returns 0. The dev venv (source tree on disk) worked, which is why it looked like an external/Cloudflare issue — it was the bundle. Fix in `backend.spec`: bundle the coco tree as ON-DISK data files (`datas=[("app/coco/cocoscrapers","cocoscrapers")]` + `excludes=["cocoscrapers"]`) so `walk_packages` sees the modules, and add `xml.dom.minidom` to `hiddenimports` (on-disk files aren't statically analyzed). Commit 9095fcf.
- Rebuilt the one-dir backend and the full Tauri app. Verified `POST /sources/157336` → 103 results (2160p DV/HDR, Torbox `cached` flags).
- (Earlier) Continue Watching: one card per show, resume at the first unwatched episode. `routers/library.py watch_list` groups by title; the frontend passes the resume season/episode through navPush→openDetail. 136 tests pass. Commits e14bc80 + 0cfaeaa.
- STATE.md updated for both fixes.

## Current state
- Repo on `main`; clean except `desktop/src-tauri/Cargo.lock` (line-ending churn from the build — spurious).
- 5 commits local-only (NOT pushed): e14bc80, 0cfaeaa, 0d387c0, 9095fcf, 2a1e0bd. origin is at c3144e5.
- Fresh local build ready to test: `desktop/src-tauri/target/release/desktop.exe` + NSIS installer (`.../bundle/nsis/Home Media_0.1.7_x64-setup.exe`).
- No servers running; 8790 free.

## Blockers
- None. Awaiting the user's test of the fresh build to confirm sources show up in the app.

## Next steps
1. User tests `desktop.exe` — confirm sources appear (Interstellar should return ~100+ results).
2. On confirmation: push the 5 local commits to origin (standing rule: no push without explicit approval).
3. Optionally bump to 0.1.8 + cut a release — the sources fix is user-facing and v0.1.7 (the last release) has the old spec.

## Key decisions
- Bundle the coco tree as on-disk data (not PYZ) because `walk_packages` needs a filesystem walk. On-disk files aren't statically analyzed, so the coco tree's stdlib imports must be in `hiddenimports` (currently just `xml.dom.minidom`; add more if the frozen exe hits another `ModuleNotFoundError`).
- The `collect_all("cocoscrapers")` approach does NOT work — it still puts the modules in the PYZ, which `walk_packages` can't see. On-disk `datas` is required.
- `POST /sources/{tmdb_id}` requires a JSON body (SourceQuery pydantic model) — curl needs `-d '{}'`.
- Commit directly to main, concise messages, no AI attribution, no push/PR without being asked.
- No secrets in handoffs — reference by name only.

## References
- `backend/backend.spec` — the on-disk coco bundle fix (commit 9095fcf).
- `backend/app/coco_driver.py` — `cocoscrapers.sources(ret_all=False)`.
- `backend/app/coco/cocoscrapers/__init__.py` — `sources()` uses `walk_packages`.
- `backend/app/coco/cocoscrapers/modules/control.py` — `setting()` / `make_settings_dict()`; imports `xml.dom.minidom`.
- `desktop/build-tauri-msvc.bat` — copies `backend/dist/home-media` → `resources/home-media`, runs `tauri build`. Invoke as `cmd.exe /c "build-tauri-msvc.bat build"`.
- Repo: `C:\ai\repos\home-media` · GitHub: `https://github.com/marcpelve/home-media` · last release tag `v0.1.7` (predates the sources fix).
- Frozen config/data: `%LOCALAPPDATA%\HomeMedia\` (`home_media.db` holds keys incl. `torbox_api_key`; no `.env`).
