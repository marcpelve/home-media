# AGENTS.md - home-media

Rules and orientation for AI-assisted work in this repo. Read this first.

## What this is
A lightweight native Windows media app to replace Kodi. Tauri frontend (thin
native shell) + Python/FastAPI backend (the brain). The backend does: TMDB
metadata, Cocoscrapers source-finding, Torbox resolve + offline download,
mpv playback, watch tracking with a local SQLite source of truth synced
across machines via Google Drive (Trakt is a one-time history import).

Full design: PLAN.md. Detailed log: STATE.md. Latest handoff: HANDOFF.md.

## Layout
- backend/    Python FastAPI app (the brain). Entry: backend/app/main.py
- desktop/    Tauri v2 native shell. Frontend is vanilla JS/CSS/HTML in
              desktop/src/ (main.js, no framework, no bundler);
              `npm run build` copies src/ -> dist/. Rust shell (sidecar
              spawn/kill, updater) in desktop/src-tauri/src/lib.rs.
- data/       runtime (dev): SQLite DB, caches, downloads (gitignored).
              The installed app uses %LOCALAPPDATA%\HomeMedia instead.
- backend/.env  secrets - gitignored. NEVER commit, NEVER print, NEVER sync.
- .github/workflows/release.yml  tag `v*.*.*` -> build, sign, publish.

## Python
- Python 3.14 on this box (the plan said 3.11); CI builds with 3.12. Both
  work for our stack. Venv lives at backend/.venv.
- Create venv:   py -3.14 -m venv backend/.venv
- Install deps:  backend/.venv/Scripts/pip install -r backend/requirements.txt
- Run backend:   from backend/ ->  .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8790
- Run tests:     backend/.venv/Scripts/python -m pytest backend/tests -q

## Backend API (local, loopback only)
- Health:  GET http://127.0.0.1:8790/health
- Full surface: PLAN.md S9.

## Edit-scope rules
- One task = one git worktree. Never edit in the primary checkout
  (C:\ai\repos\home-media). Work in your assigned worktree directory.
- backend/ changes stay in backend/; frontend changes in desktop/.
- Do NOT touch backend/.env. Do NOT add secrets to any tracked file. The
  installed app keeps TMDB/Torbox/Drive credentials in the DB settings
  table - never print those values either.
- Cocoscrapers (vendored under backend/app/coco/) is READ-ONLY. Adapt it via
  kodi_stub.py - never edit the library's own files. One deliberate
  exception exists: the piratebay.py shadowed-import fix (commit 88b0242);
  re-apply it if the vendored copy is ever refreshed.
- The API only accepts browser requests from the Tauri webview and local
  dev origins (ALLOWED_ORIGINS / ALLOWED_ORIGIN_REGEX in backend/app/main.py;
  anything else gets 403). If the frontend's origin changes, update that
  allowlist - never go back to a wildcard.
- The orchestrator (Hermes) owns AGENTS.md, STATE.md, README.md, PLAN.md.
  A delegated coding task should not rewrite those.

## Reading selectively (this box)
- The terminal here is git-bash (POSIX), not PowerShell. Use `ls`, `grep`,
  `head`/`tail`, single-quoted strings.
- Search before opening: search_files (ripgrep-backed), not read-every-file.
- Cap output: pipe long logs/test runs through `| tail -n 50` or `Select-String`.
- Read line ranges, not whole files, when only part matters.

## Conventions
- Comments explain WHY (a tradeoff, a constraint, a worked-around bug), not
  what the code does.
- Every module/script gets a header comment: purpose + how it's used.
- Commit often, atomic, message says what + why. Never commit secrets.
- Verify before reporting done: run the tests, hit the endpoint, check the
  artifact. A self-report is not proof.
