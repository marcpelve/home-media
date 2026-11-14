"""Cloud storage sync engine + one-time Trakt import (P-M5).

Local is the source of truth. The cloud section syncs per-title JSON docs
(storage.py) to a StorageBackend; the merge rule is LWW by updated_at.
``import_from_trakt`` is a one-time import: Trakt watch history -> local
titles + watch_progress, which the cloud sync then propagates. The ongoing
Trakt sync (push/pull) was dropped in P-M5.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from app.storage import StorageBackend, build_doc, make_key, parse_key
from app.trakt import TraktClient


def _conn(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_sqlite(conn: sqlite3.Connection) -> str:
    """datetime('now') - same format as the schema's updated_at default."""
    return conn.execute("SELECT datetime('now')").fetchone()[0]


def _get_setting(db_path, key: str) -> str | None:
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_setting(db_path, key: str, value: str) -> None:
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _find_progress(
    conn: sqlite3.Connection, title_id: int, season: int | None, episode: int | None
) -> sqlite3.Row | None:
    # NULL-safe match on (season, episode).
    s = -1 if season is None else season
    e = -1 if episode is None else episode
    return conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ? AND "
        "COALESCE(season, -1) = ? AND COALESCE(episode, -1) = ?",
        (title_id, s, e),
    ).fetchone()


# --- One-time Trakt import (P-M5) ------------------------------------------------
# Trakt watch history -> local titles + watch_progress. One-shot: the user
# imports their past once, and the cloud sync below then propagates the rows.
# LWW on progress: a strictly-newer local row wins (the user may have
# re-watched and be mid-way locally).


def _parse_history_item(item: dict) -> dict | None:
    """Extract (tmdb_id, type, title, year, season, episode, played_at) from a
    Trakt history item. Returns None when the item carries no TMDB id.

    Movies key on (tmdb_id, None, None); episodes key on the SHOW's tmdb_id +
    the episode's season/number (watch_progress is keyed by tmdb_id app-wide).
    """
    played_at = item.get("played_at") or ""
    typ = item.get("type")
    if typ == "movie":
        node = item.get("movie") or {}
        tmdb_id = (node.get("ids") or {}).get("tmdb")
        if not tmdb_id:
            return None
        return {
            "tmdb_id": int(tmdb_id),
            "type": "movie",
            "title": node.get("title"),
            "year": node.get("year"),
            "season": None,
            "episode": None,
            "played_at": played_at,
        }
    if typ == "episode":
        node = item.get("episode") or {}
        show = node.get("show") or {}
        tmdb_id = (show.get("ids") or {}).get("tmdb")
        if not tmdb_id:
            return None
        return {
            "tmdb_id": int(tmdb_id),
            "type": "tv",
            "title": show.get("title"),
            "year": show.get("year"),
            "season": node.get("season"),
            "episode": node.get("number"),
            "played_at": played_at,
        }
    return None


async def import_from_trakt(client: TraktClient, db_path) -> dict:
    """One-time Trakt import (P-M5): the user's full watch history -> local DB.

    Pulls the full history, dedupes replays (max played_at per
    (tmdb_id, season, episode)), creates minimal titles rows for unknown
    titles, and LWW-merges watch_progress (a strictly-newer local row wins).
    The cloud sync then propagates the created rows. Returns
    {imported, titles_created, skipped, error}. Per-item failures are recorded
    in "error" and never abort the import.
    """
    if not client.is_authenticated():
        return {
            "imported": 0,
            "titles_created": 0,
            "skipped": 0,
            "error": "not authenticated",
        }
    history = await client.get_all_history()
    conn = _conn(db_path)
    imported = 0
    titles_created = 0
    skipped = 0
    errors: list[str] = []
    try:
        device = _get_setting(db_path, "device_id") or "default"
        now = _now_sqlite(conn)
        # Dedupe replays: keep the max played_at per (tmdb_id, season, episode).
        # played_at is ISO-8601 "…Z", so a lexicographic compare is safe.
        deduped: dict[tuple, dict] = {}
        for item in history:
            if not isinstance(item, dict):
                continue
            parsed = _parse_history_item(item)
            if parsed is None:
                skipped += 1  # no TMDB id
                continue
            key = (parsed["tmdb_id"], parsed["season"], parsed["episode"])
            cur = deduped.get(key)
            if cur is None or parsed["played_at"] > cur["played_at"]:
                deduped[key] = parsed
        for p in deduped.values():
            try:
                # TITLE: create a minimal row only if the title is unknown.
                title = conn.execute(
                    "SELECT id FROM titles WHERE tmdb_id = ?", (p["tmdb_id"],)
                ).fetchone()
                if title is None:
                    conn.execute(
                        "INSERT INTO titles (tmdb_id, type, title, year, device, "
                        "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            p["tmdb_id"], p["type"], p["title"], p["year"],
                            device, now,
                        ),
                    )
                    titles_created += 1
                # PROGRESS: LWW — a strictly-newer local row wins.
                row = _find_progress(
                    conn, p["tmdb_id"], p["season"], p["episode"]
                )
                if row is None:
                    conn.execute(
                        "INSERT INTO watch_progress (title_id, season, episode, "
                        "completed, completed_at, device, updated_at) "
                        "VALUES (?, ?, ?, 1, ?, ?, ?)",
                        (
                            p["tmdb_id"], p["season"], p["episode"],
                            p["played_at"], device, now,
                        ),
                    )
                    imported += 1
                elif normalize_ts(p["played_at"]) > normalize_ts(row["updated_at"]):
                    conn.execute(
                        "UPDATE watch_progress SET completed = 1, completed_at = "
                        "?, device = ?, updated_at = ? WHERE id = ?",
                        (p["played_at"], device, now, row["id"]),
                    )
                    imported += 1
                else:
                    skipped += 1  # local state wins
            except Exception as e:
                errors.append(f"{p['tmdb_id']}: {type(e).__name__}")
        conn.commit()
    finally:
        conn.close()
    error = "; ".join(errors) if errors else None
    # Record the import so /trakt/status can report it.
    _set_setting(db_path, "trakt_last_import_at", now)
    _set_setting(db_path, "trakt_last_import_count", str(imported))
    return {
        "imported": imported,
        "titles_created": titles_created,
        "skipped": skipped,
        "error": error,
    }


# --- Cloud storage sync (P-M3) -------------------------------------------------
# Replaces Trakt as the sync layer: per-title JSON docs (storage.py) live in
# a StorageBackend (local folder today, Google Drive in P-M4). Merge rule is
# LWW by updated_at; both sides store UTC, so after normalize_ts a
# lexicographic compare is safe.

def normalize_ts(value: str | None) -> str:
    """Normalize a doc-form ('2026-11-13T10:20:30Z') or SQLite-form
    ('2026-11-13 10:20:30') timestamp to the SQLite form for compare.

    Both forms are UTC; mapping to one shape makes LWW a plain lexicographic
    compare. Empty/None -> "" (sorts before any real timestamp).
    """
    if not value:
        return ""
    return value.replace("T", " ").replace("Z", "")


def build_title_doc(conn: sqlite3.Connection, tmdb_id: int) -> dict | None:
    """Assemble a per-title doc from a titles row + its watch_progress rows.

    watch_progress.title_id IS the tmdb_id app-wide, so the title row is
    matched by tmdb_id and its watch rows are selected by title_id == tmdb_id.
    Returns None when the title row does not exist.
    """
    title = conn.execute(
        "SELECT * FROM titles WHERE tmdb_id = ?", (tmdb_id,)
    ).fetchone()
    if title is None:
        return None
    rows = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (tmdb_id,)
    ).fetchall()
    watch = [
        {
            "season": r["season"],
            "episode": r["episode"],
            "position_ms": r["position_ms"],
            "duration_ms": r["duration_ms"],
            "completed": r["completed"],
            "completed_at": r["completed_at"],
            "device": r["device"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    # genres round-trips as a JSON list in the doc (merge json.dumps it back),
    # so parse the DB's TEXT column here.
    raw_genres = title["genres"]
    try:
        genres = json.loads(raw_genres) if raw_genres else []
    except (json.JSONDecodeError, TypeError):
        genres = []
    if not isinstance(genres, list):
        genres = []
    title_dict = {
        "tmdb_id": title["tmdb_id"],
        "type": title["type"],
        "title": title["title"],
        "year": title["year"],
        "imdb_id": title["imdb_id"],
        "overview": title["overview"],
        "genres": genres,
        "poster_path": title["poster_path"],
        "backdrop_path": title["backdrop_path"],
        "device": title["device"],
        "updated_at": title["updated_at"],
    }
    return build_doc(
        tmdb_id=tmdb_id,
        type=title["type"],
        title=title_dict,
        watch=watch,
        device=title["device"],
    )


def build_local_doc(db_path, key: str) -> dict | None:
    """Build the doc for *key* from the local DB on its own connection.

    Returns None when the title row does not exist. Thread-safe: a per-call
    connection means parallel push workers can each build their own doc.
    """
    _t_type, tmdb_id = parse_key(key)
    conn = _conn(db_path)
    try:
        return build_title_doc(conn, tmdb_id)
    finally:
        conn.close()


def push_title(backend, db_path, key: str) -> bool:
    """Build the doc for *key* from the local DB and write it to *backend*.

    Returns False (and writes nothing) when the title row does not exist.
    """
    doc = build_local_doc(db_path, key)
    if doc is None:
        return False
    backend.write_title(key, doc)
    return True


def doc_data_ts(doc: dict) -> str:
    """The doc's data timestamp: the max (lexicographic, after normalize_ts)
    of the title row's updated_at and every watch row's updated_at.

    Deliberately NOT the doc's top-level updated_at: build_doc stamps that
    with the build time (now_iso), which is not comparable across sides. The
    data rows' timestamps are the LWW-relevant values. Returns "" when
    nothing is present (sorts before any real timestamp).
    """
    ts: list[str] = []
    t = doc.get("title") or {}
    if t.get("updated_at"):
        ts.append(normalize_ts(t["updated_at"]))
    for w in doc.get("watch") or []:
        if w.get("updated_at"):
            ts.append(normalize_ts(w["updated_at"]))
    return max(ts) if ts else ""


def pull(backend, db_path) -> tuple[int, list[str], dict[str, dict]]:
    """Pull every stored doc and LWW-merge it into the local DB.

    Docs are read in parallel (httpx.Client is safe to share across threads);
    the merge stays single-threaded on one connection. One bad doc must not
    abort the pull: a per-title exception is recorded as
    "key: TypeName: msg" and the loop continues. Returns
    (rows_changed, errors, remote_docs) where remote_docs maps each key whose
    doc read succeeded to its doc (keys that raised or returned None are
    absent).
    """
    keys = backend.list_titles()
    remote_docs: dict[str, dict] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(backend.read_title, keys)
        for key in keys:
            try:
                doc = next(results)
            except Exception as e:
                errors.append(f"{key}: {type(e).__name__}: {e}")
                continue
            if doc is not None:
                remote_docs[key] = doc

    conn = _conn(db_path)
    rows_changed = 0
    try:
        for key, doc in remote_docs.items():
            try:
                rows_changed += _merge_doc(conn, doc)
            except Exception as e:
                errors.append(f"{key}: {type(e).__name__}: {e}")
        conn.commit()
    finally:
        conn.close()
    return rows_changed, errors, remote_docs


def _merge_doc(conn: sqlite3.Connection, doc: dict) -> int:
    """LWW-merge one per-title doc into the local DB. Returns rows changed.

    The local catalog is authoritative for title existence: a doc whose
    tmdb_id has no local title row is skipped (no row created). For the title
    and each watch entry the strictly-newer updated_at wins, and the winner's
    updated_at is stored verbatim (provenance) -- never re-stamped with
    datetime('now'), which would corrupt LWW ordering on the next merge. Local
    watch rows absent from the doc are kept (they get pushed later).
    """
    tmdb_id = doc.get("tmdb_id")
    row = conn.execute(
        "SELECT * FROM titles WHERE tmdb_id = ?", (tmdb_id,)
    ).fetchone()
    if row is None:
        # Local catalog is authoritative for what exists.
        return 0

    changed = 0
    t = doc.get("title") or {}
    if normalize_ts(t.get("updated_at")) > normalize_ts(row["updated_at"]):
        conn.execute(
            "UPDATE titles SET title = ?, year = ?, type = ?, imdb_id = ?, "
            "overview = ?, genres = ?, poster_path = ?, backdrop_path = ?, "
            "device = ?, updated_at = ? WHERE id = ?",
            (
                t.get("title"),
                t.get("year"),
                t.get("type"),
                t.get("imdb_id"),
                t.get("overview"),
                json.dumps(t.get("genres") or []),
                t.get("poster_path"),
                t.get("backdrop_path"),
                t.get("device"),
                t.get("updated_at"),
                row["id"],
            ),
        )
        changed += 1

    for w in doc.get("watch") or []:
        season = w.get("season")
        episode = w.get("episode")
        local = _find_progress(conn, tmdb_id, season, episode)
        if local is None:
            conn.execute(
                "INSERT INTO watch_progress (title_id, season, episode, "
                "position_ms, duration_ms, completed, completed_at, device, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tmdb_id,
                    season,
                    episode,
                    w.get("position_ms", 0),
                    w.get("duration_ms", 0),
                    w.get("completed", 0),
                    w.get("completed_at"),
                    w.get("device"),
                    w.get("updated_at"),
                ),
            )
            changed += 1
        elif normalize_ts(w.get("updated_at")) > normalize_ts(local["updated_at"]):
            conn.execute(
                "UPDATE watch_progress SET position_ms = ?, duration_ms = ?, "
                "completed = ?, completed_at = ?, device = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    w.get("position_ms", 0),
                    w.get("duration_ms", 0),
                    w.get("completed", 0),
                    w.get("completed_at"),
                    w.get("device"),
                    w.get("updated_at"),
                    local["id"],
                ),
            )
            changed += 1
    return changed


def record_sync_status(db_path, pending_count: int, last_error: str | None) -> None:
    """Write the single sync_status row (last_sync_at = last_push_at = now)."""
    conn = _conn(db_path)
    try:
        now = _now_sqlite(conn)
        conn.execute("DELETE FROM sync_status")
        conn.execute(
            "INSERT INTO sync_status (last_sync_at, last_push_at, pending_count, "
            "last_error) VALUES (?, ?, ?, ?)",
            (now, now, pending_count, last_error),
        )
        conn.commit()
    finally:
        conn.close()


def pending_push_count(db_path) -> int:
    """Count watch_progress rows newer than the recorded last_push_at."""
    conn = _conn(db_path)
    try:
        row = conn.execute("SELECT last_push_at FROM sync_status").fetchone()
        last_push = (row[0] if row else None) or ""
        n = conn.execute(
            "SELECT COUNT(*) FROM watch_progress WHERE updated_at > ?",
            (last_push,),
        ).fetchone()[0]
        return n
    finally:
        conn.close()


def sync_all_cloud(backend, db_path) -> dict:
    """Pull cloud docs, push the local titles that are newer (or missing
    remotely), record sync status.

    Unchanged titles are not re-pushed: a title is pushed only when its
    remote doc is absent/unreadable (self-heal) or its data timestamp is
    strictly newer than the remote's (see doc_data_ts). Pushes run in
    parallel; each worker writes the doc already built for its comparison,
    so no title is built twice.

    Never raises: per-title failures are recorded in "error" and the loop
    continues (a bad doc or a failed push must not stop the rest). Returns
    {"pushed", "pulled", "error"}.
    """
    errors: list[str] = []
    pulled = 0
    remote_docs: dict[str, dict] = {}
    try:
        pulled, pull_errors, remote_docs = pull(backend, db_path)
        errors.extend(pull_errors)
    except Exception as e:
        errors.append(f"pull: {type(e).__name__}: {e}")

    conn = _conn(db_path)
    try:
        titles = conn.execute(
            "SELECT type, tmdb_id FROM titles WHERE tmdb_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    # Select what to push: build each local doc once and compare its data ts
    # against the remote's. Missing remote -> push (self-heal).
    to_push: list[tuple[str, dict]] = []
    for t in titles:
        key = make_key(t["type"], t["tmdb_id"])
        local_doc = build_local_doc(db_path, key)
        if local_doc is None:
            continue
        remote = remote_docs.get(key)
        if remote is None or doc_data_ts(local_doc) > doc_data_ts(remote):
            to_push.append((key, local_doc))

    pushed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(backend.write_title, key, doc): key
            for key, doc in to_push
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                fut.result()
                pushed += 1
            except Exception as e:
                errors.append(f"push {key}: {type(e).__name__}: {e}")

    error = "; ".join(errors) if errors else None
    # Everything that needed pushing was pushed, so nothing is pending.
    record_sync_status(db_path, 0, error)
    return {"pushed": pushed, "pulled": pulled, "error": error}


# --- Progress push (P-M3b) --------------------------------------------------
# Debounced per-title cloud push: local progress changes schedule a write of
# that title's doc after a quiet period. One write per title per burst keeps
# cloud load flat during a long viewing session.


def push_key_for(db_path, tmdb_id: int) -> str | None:
    """make_key for a local title, None when the title row is absent."""
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT type FROM titles WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        if row is None:
            return None
        return make_key(row["type"], tmdb_id)
    finally:
        conn.close()


class ProgressPusher:
    """Debounced per-title cloud push.

    Local progress changes (play position, mark-watched) schedule a push of
    that title's doc after delay_s; a further change resets the timer, so a
    long viewing session produces one write per title per quiet period.

    The event loop is captured at init (lifespan startup, where a loop is
    running) so schedule() can be called from FastAPI's sync thread pool.

    *make_backend* is a factory, not a backend: a backend holds an access
    token that expires ~1h, while the pusher lives for the app's lifetime,
    so it must get a fresh backend per push (the factory also refreshes the
    token). A factory that returns None (Drive not connected) makes fire and
    flush no-ops.
    """
    def __init__(
        self, db_path, make_backend: Callable[[], StorageBackend | None],
        delay_s: float = 30.0,
    ):
        self._db_path = db_path
        self._make_backend = make_backend
        self._delay_s = delay_s
        self._loop = asyncio.get_running_loop()
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._pending: set[str] = set()

    def schedule(self, key: str) -> None:
        old = self._timers.pop(key, None)
        if old is not None:
            old.cancel()
        self._pending.add(key)
        # Thread-safe: sync endpoints run in a pool; the timer must be
        # created on the main loop.
        self._loop.call_soon_threadsafe(self._set_timer, key)

    def _set_timer(self, key: str) -> None:
        # Two schedule() calls can queue two _set_timer callbacks before
        # either runs (no await between them); cancel the predecessor so
        # only the latest timer survives and fires once.
        old = self._timers.get(key)
        if old is not None:
            old.cancel()
        self._timers[key] = self._loop.call_later(
            self._delay_s, self._fire, key
        )

    def _fire(self, key: str) -> None:
        self._timers.pop(key, None)
        self._pending.discard(key)
        backend = self._make_backend()
        if backend is None:
            return  # not connected: skip; next full sync reconciles
        try:
            push_title(backend, self._db_path, key)
        except Exception:
            pass  # next full sync reconciles; never crash the timer

    @property
    def pending(self) -> set[str]:
        return set(self._pending)

    async def flush(self) -> int:
        """Cancel timers and push all pending keys now (used on shutdown)."""
        for t in list(self._timers.values()):
            t.cancel()
        self._timers.clear()
        keys = sorted(self._pending)
        self._pending.clear()
        backend = self._make_backend()
        if backend is None:
            return 0  # not connected: nothing to flush
        pushed = 0
        for key in keys:
            try:
                if push_title(backend, self._db_path, key):
                    pushed += 1
            except Exception:
                pass
        return pushed
