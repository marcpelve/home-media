# AGENTS.md - home-media

Rules and orientation for AI-assisted work in this repo. Read this first.

## What this is
A lightweight native Windows media app to replace Kodi. Tauri frontend (thin
native shell) + Python/FastAPI backend (the brain). The backend does: TMDB
metadata, Cocoscrapers source-finding, Torbox resolve + offline download,
mpv playback, Trakt tracking with a local SQLite source of truth.

Full design: PLAN.md. Live multi-session state: STATE.md.

## Layout
- backend/    Python FastAPI app (the brain). Entry: backend/app/main.py
- frontend/   Tauri + React + Tailwind (native shell) - built in P0b
- data/       runtime: SQLite DB, caches, downloads (gitignored)
- backend/.env  secrets - gitignored. NEVER commit, NEVER print, NEVER sync.

## Python
- Python 3.14 (only version on this box; the plan said 3.11 - 3.14 works for
  our stack). Venv lives at backend/.venv.
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
- backend/ changes stay in backend/; frontend/ in frontend/.
- Do NOT touch backend/.env. Do NOT add secrets to any tracked file.
- Cocoscrapers (once vendored under backend/app/coco/) is READ-ONLY. Adapt
  it via kodi_stub.py - never edit the library's own files.
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
