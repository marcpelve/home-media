# Project Handoff: home-media

**Date:** 2026-11-15 14:10
**Branch:** main
**Latest commit:** 3ea42bf (Bump version to 0.2.1)

## Done
- Fixed `resolve()` to add cached-but-missing torrents to the Torbox account
  via magnet, then re-search with 3x2s retry (commit `4e084d2`).
- Added frontend guards: alert + return false when `download_url` is missing
  in both `pickSource` and `pickSourceDownload`.
- Fixed `_safe_name()` in `downloads.py` to strip torrent directory components
  (`Path(filename).name`) — torrent file paths like `Show.S01/Show.S01E01.mkv`
  were creating non-existent subdirectories, causing all downloads to fail at 0%.
- Added "Failed" section to the Downloads UI: shows failed downloads with
  individual Remove buttons and a "Clear all" button (commit `abd0015`).
- Player: simplified controls to Stop-only, fixed time separator,
  added download speed indicator (MB/s) (commit `8c843d5`).
- Bumped version to 0.2.1 (commit `3ea42bf`).
- Pushed to main, created GitHub release v0.2.1 with NSIS installer asset.

## Current state
- main == origin/main (fully pushed). v0.2.1 released.
- Release: https://github.com/marcpelve/home-media/releases/tag/v0.2.1
- 214/214 backend tests passing (as of `8c843d5`; not re-run after `abd0015`).
- Untracked: `desktop/src-tauri/build.log` (build artifact).
- `desktop/src-tauri/build-tauri.bat` still untracked (MSVC helper, CI doesn't use it).

## Blockers
- None.

## Next steps
1. Test the v0.2.1 installer: verify downloads work end-to-end and the
   failed-downloads UI shows/clears old attempts.
2. Run `pytest tests/ -x -q` to confirm 214/214 after `abd0015`.
3. Decide the fate of `desktop/src-tauri/build-tauri.bat` (commit vs gitignore).

## Key decisions
- `_safe_name` strips directory components from torrent file paths: the
  Torbox API returns `files[].name` as a full relative path including the
  torrent's folder structure; only the leaf filename is needed for the
  local downloads dir.
- `resolve()` adds missing torrents via magnet then retries: some cached
  sources are not yet in the Torbox account; the old code only searched
  existing torrents and returned empty, causing 422s on the download endpoint.
- Frontend guards use `alert()` + `return false`: prevents silent failures
  when a source can't produce a download URL (e.g. uncached source).
- Player controls reduced to Stop-only: mpv has its own keyboard shortcuts
  (space=play/pause, left/right=seek); the in-app buttons were redundant.

## References
- Key commits: `4e084d2` (resolve fix), `8c843d5` (player + speed),
  `abd0015` (download path + failed UI), `3ea42bf` (version bump).
- Local DB: `%LOCALAPPDATA%\HomeMedia\home_media.db` (settings table holds
  Torbox/TMDB/Drive credentials — never commit or print values).
- Release workflow: `.github/workflows/release.yml` (tag `v*.*.*` trigger).
- Build: `cd desktop/src-tauri && cmd.exe /c build-tauri.bat`.
- PyInstaller: `cd backend && .venv/Scripts/python -m PyInstaller backend.spec --clean -y`.
