# Project Handoff: home-media

**Date:** 2026-11-13 04:45
**Branch:** main
**Latest commit:** ea38dc8 (PLAN.md: add section 16 - Trakt to cloud-storage sync migration plan)

## Done
- Root-caused and fixed Trakt sync push=0 (two bugs in `backend/app/sync.py`):
  1. `watch_progress.title_id` was treated as the local `titles.id`, but the app keys on TMDB id -> push + both pull paths now key on `tmdb_id` (commit `b31ffde`).
  2. `mark_watched` POSTed to `/users/{id}/items/history` (GET-only; no POST exists) -> now `POST /scrobble/stop` with `{progress:100, episode|movie}`, 409 treated as success (commit `ec2d661`).
- Verified against the live DB: all 47 watch_progress rows resolve and yield valid scrobble nodes. 136 tests pass.
- pull=0 confirmed correct: Trakt history (100 items) has 0 overlap with the 19-title library.
- Diagnosed the 400 on push: token scope is `public offline_access` (no `scrobble:post`); NOT an IP block. No self-serve/proven grant path; production access is premium-gated (rejected).
- Designed + documented the migration from Trakt to cloud-storage sync as **PLAN.md section 16** (commit `ea38dc8`): local DB live peer + per-title cloud file (Google Drive, OAuth personal) + storage adapter + LWW merge by `updated_at`; Trakt demoted to a one-time import. Phases P-M0..P-M7.
- (Earlier in the session) Rebuilt the frozen backend + Tauri app: fresh `desktop.exe` (Nov 13 02:01) with the `tmdb_id` fix but not the `/scrobble/stop` fix.

## Current state
- Repo `main`, clean. 10 unpushed commits (origin at `c3144e5`): ea38dc8, ec2d661, b31ffde, d907fbe, 911d0df, 2a1e0bd, 9095fcf, 0d387c0, 0cfaeaa, e14bc80.
- Live DB `%LOCALAPPDATA%\HomeMedia\home_media.db`: 19 titles, 47 watch_progress rows (all tmdb_id=105971), Trakt authed, scope `public offline_access`.
- Migration is PLANNED (PLAN.md S16), not executed. 136 tests pass. No servers running.

## Blockers
- None. Awaiting the user's go-ahead to start the migration (standing rule: no code until go-ahead).
- Trakt `scrobble:post` scope is blocked, now moot — the new architecture doesn't depend on Trakt push.

## Next steps
1. User reviews PLAN.md S16; on go-ahead start P-M0 (confirm Drive-OAuth / device-id / file-format decisions; set up the Google Cloud project + OAuth desktop client).
2. P-M1 add `device` column -> P-M2 `backend/app/storage.py` adapter -> P-M3 rewrite `sync.py` -> P-M4 hooks/config -> P-M5 one-time Trakt import -> P-M6 cutover/verify -> P-M7 rollback.
3. The fresh `desktop.exe` predates the `/scrobble/stop` fix; rebuild only if that must ship (the migration supersedes Trakt push, so low priority).
4. Push the 10 local commits only with explicit approval.

## Key decisions
- Local DB is the live offline-first peer (not a cache) and doubles as the offline outbox; the cloud is one file per title (metadata + watch state).
- Sync = bidirectional last-write-wins merge by `updated_at` (deterministic, idempotent).
- Storage adapter (`backend/app/storage.py`): `LocalFolder` + `Drive` (OAuth personal) behind one interface -> transport is swappable (Drive -> B2 -> local folder).
- OAuth against a PERSONAL Google account (not a service account) — the user's familiar pattern.
- Trakt demoted to a one-time import; the `/scrobble/stop` code is kept (deprecated) in case the scope ever opens.
- Commit to main, concise messages, no AI attribution, no push/PR without being asked. No secrets in handoffs — reference by name only.

## References
- `PLAN.md` S16 — the migration plan (this session's main deliverable).
- `backend/app/sync.py` — push/pull; `_scrobble_node` builds the `/scrobble/stop` payload.
- `backend/app/trakt.py` — `mark_watched` -> `POST /scrobble/stop`; `get_history()` for the one-time import.
- `backend/tests/test_trakt.py` — mock intercepts `/scrobble/stop`.
- `backend/app/db.py` — schema (S7 already has `updated_at`; the migration adds `device`).
- Repo: `C:\ai\repos\home-media` · GitHub: `https://github.com/marcpelve/home-media` · last release tag `v0.1.7`.
- Frozen config/data: `%LOCALAPPDATA%\HomeMedia\` (`home_media.db` holds keys incl. `trakt_*`, `torbox_api_key`; no `.env`).
- Cloned Trakt API reference (scratch): `%LOCALAPPDATA%\hermes\cache\scratch\trakt-api`.
