# Project Handoff: home-media

**Date:** 2026-11-14 06:45
**Branch:** main
**Latest commit:** 6fd6c48 (version: 0.2.0 — cloud storage sync migration)

## Done
- Cloud storage sync migration complete (P-M0–P-M7): Trakt ongoing sync
  removed (demoted to one-time import), Google Drive is the sync backend
  (per-title versioned JSON under `My Drive/HomeMedia/{movies,shows}/`).
- P-M4i: Drive upload fixes — resumable-upload protocol for creates;
  updates use replace (delete + create) because in-place `PUT /files/{id}`
  is unroutable on this network (HTML 404, curl-confirmed).
- P-M4j: sync speed — push only changed titles (data-ts LWW comparison),
  parallel pull/push (8 workers), per-subfolder listing cache. Unchanged
  sync: ~2s / 0 writes (was ~70s / 30 writes).
- P-M4k: concurrent-replace race fix — process-wide per-title writer lock
  serializes find+delete+create across backend instances (progress push
  vs manual sync); a 404 on the replace's delete is tolerated.
- v0.2.0 released: tag pushed, GitHub workflow built + signed + published
  (installer, .sig files, updater zip, latest.json). Release verified,
  updater manifest checked (version 0.2.0, signed).
- Pre-push secrets scan: all 47 pushed commits (diffs + full history
  incl. deletions) and the full tree are clean; only `.env.example`
  (placeholders) carries credential names.
- 214/214 backend tests pass.

## Current state
- main == origin/main (fully pushed). Release run 43566163396 succeeded.
- Release: https://github.com/marcpelve/home-media/releases/tag/v0.2.0
- Local app running (port 8790) on the 0.2.0 build; Drive sync clean
  (30 titles, last_error None).
- Untracked local file: `desktop/src-tauri/build-tauri.bat` (MSVC toolchain
  helper for local builds; the CI workflow does NOT use it). Decide whether
  to commit or gitignore it.

## Blockers
- None.

## Next steps
1. Test the in-app updater on a real install: install 0.1.7, check for
   update -> should find 0.2.0 via latest.json (updater path untested
   end-to-end since v0.1.0).
2. Decide the fate of `desktop/src-tauri/build-tauri.bat` (commit vs
   gitignore).
3. HANDOFF.md commit is local — push with the next explicit push.

## Key decisions
- Replace-on-update (delete + create) for Drive writes: in-place content
  updates are environmentally broken on this network; safe because sync
  pushes all titles (self-healing) and lookup is name-based (id swap
  invisible).
- Per-title writer lock is process-wide (module-level), keyed by storage
  key: the factory builds a fresh backend per call (token freshness), so
  instance state can't serialize writers.
- Selective push compares data ts = max(title row, watch rows)
  `updated_at` — NOT the doc's top-level stamp (build time).
- Rollback levels: L1 `storage_backend='local'`; L2 + delete Drive creds
  from the local DB settings; L3 `git revert` the migration commits.

## References
- STATE.md — full phase log through P-M4k.
- Key commits: 7e9a3b9 (listing cache), c0ae6a2 (selective + parallel
  sync), 32c5cac (writer lock), 6fd6c48 (version).
- Local DB: `%LOCALAPPDATA%\HomeMedia\home_media.db` (settings table holds
  Drive/TMDB/Torbox credentials — never commit or print values).
- Release workflow: `.github/workflows/release.yml` (tag `v*.*.*` trigger;
  needs repo secret TAURI_SIGNING_PRIVATE_KEY, already set).
