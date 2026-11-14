"""Trakt sync engine: push local watch progress to Trakt, pull Trakt
watched/progress, and merge into the local watch_progress source of truth.

Local is authoritative. Trakt is a sync/backup layer that degrades gracefully
when unauthenticated or offline (records an error, local keeps working).
Merge rule: max position_ms per item, union of completed.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

from app.storage import build_doc, make_key, parse_key
from app.trakt import TraktAuthError, TraktClient


def _now_iso() -> str:
    """ISO 8601 UTC - for Trakt `played_at` fields."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _record_sync(db_path, pending_count: int, last_error: str | None) -> None:
    conn = _conn(db_path)
    try:
        conn.execute("DELETE FROM trakt_sync")
        conn.execute(
            "INSERT INTO trakt_sync (last_sync_at, pending_count, last_error) "
            "VALUES (?, ?, ?)",
            (_now_sqlite(conn), pending_count, last_error),
        )
        conn.commit()
    finally:
        conn.close()


def _local_title_by_tmdb(conn: sqlite3.Connection, tmdb_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM titles WHERE tmdb_id = ?", (tmdb_id,)
    ).fetchone()


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


def _ep(node: dict, key: str) -> int | None:
    ep = node.get("episode") or {}
    val = ep.get(key)
    return int(val) if isinstance(val, int) else None


def _progress_ms(node: dict, entry: dict) -> tuple[int, int]:
    """Convert Trakt node.runtime (min) + progress.percent to (ms, duration_ms)."""
    runtime = node.get("runtime")
    percent = (entry.get("progress") or {}).get("percent", 0.0)
    if not isinstance(runtime, (int, float)) or not isinstance(percent, (int, float)):
        return 0, 0
    duration_ms = int(runtime * 60000)
    position_ms = int(percent / 100.0 * duration_ms)
    return position_ms, duration_ms


def _scrobble_node(title: sqlite3.Row, season: int | None, episode: int | None) -> dict | None:
    """Build a Trakt scrobble body (progress 100 = watched) from a local title.

    /scrobble/stop takes a single {progress, movie|episode} object. Movies need
    title + year + ids; episodes need season + number + the show's ids (Trakt
    resolves the episode from show-tmdb + season + number).
    """
    if not title["tmdb_id"]:
        return None
    ids: dict = {"tmdb": title["tmdb_id"]}
    if title["imdb_id"]:
        ids["imdb"] = title["imdb_id"]
    if title["type"] == "movie":
        return {
            "progress": 100,
            "movie": {
                "title": title["title"],
                "year": title["year"] or 0,
                "ids": ids,
            },
        }
    return {
        "progress": 100,
        "episode": {
            "season": season or 1,
            "number": episode or 1,
            "ids": dict(ids),
            "show": {"ids": dict(ids)},
        },
    }


async def push_progress(client: TraktClient, db_path) -> int:
    """Scrobble local watch progress (updated since the last push) to Trakt.

    DEPRECATED (P-M3): replaced by cloud sync; retained for the one-off Trakt
    import (P-M5) and must not be extended.
    Returns the number of items pushed. No-op (0) when unauthenticated.
    """
    if not client.is_authenticated():
        return 0
    conn = _conn(db_path)
    try:
        last_push = _get_setting(db_path, "trakt_last_push_at") or ""
        rows = conn.execute(
            "SELECT * FROM watch_progress WHERE updated_at > ?", (last_push,)
        ).fetchall()
        pushed = 0
        for row in rows:
            # watch_progress is keyed by tmdb_id app-wide (the frontend only
            # knows TMDB ids), so resolve the title by tmdb_id, not titles.id.
            title = conn.execute(
                "SELECT * FROM titles WHERE tmdb_id = ?", (row["title_id"],)
            ).fetchone()
            if title is None:
                continue
            item = _scrobble_node(title, row["season"], row["episode"])
            if item is None:
                continue
            await client.mark_watched(item)
            pushed += 1
        if pushed:
            _set_setting(db_path, "trakt_last_push_at", _now_sqlite(conn))
        return pushed
    finally:
        conn.close()


async def pull_progress(client: TraktClient, db_path) -> tuple[int, list[str]]:
    """Pull the user's Trakt watch history; merge into local progress.

    DEPRECATED (P-M3): replaced by cloud sync; retained for the one-off Trakt
    import (P-M5) and must not be extended.
    Uses /users/{u}/history (history:read), which works without scrobble:post.
    Returns (merged_count, partial_errors). No-op (0, []) when unauthenticated.
    """
    if not client.is_authenticated():
        return 0, []
    conn = _conn(db_path)
    merged = 0
    errors: list[str] = []
    try:
        try:
            history = await client.get_history()
        except Exception as e:
            errors.append(f"history: {type(e).__name__}")
            history = []
        for item in history:
            if not isinstance(item, dict):
                continue
            typ = item.get("type")
            if typ == "movie":
                node = item.get("movie") or {}
                tmdb_id = (node.get("ids") or {}).get("tmdb")
                if not tmdb_id:
                    continue
                title = _local_title_by_tmdb(conn, int(tmdb_id))
                if title is None:
                    continue
                # Keyed by tmdb_id app-wide (the frontend only knows TMDB ids),
                # so match/insert on tmdb_id, not titles.id.
                row = _find_progress(conn, int(tmdb_id), None, None)
                if row is None:
                    conn.execute(
                        "INSERT INTO watch_progress (title_id, position_ms, "
                        "duration_ms, completed) VALUES (?, 0, 0, 1)",
                        (int(tmdb_id),),
                    )
                    merged += 1
                elif not row["completed"]:
                    conn.execute(
                        "UPDATE watch_progress SET completed = 1, completed_at = ? "
                        "WHERE id = ?",
                        (_now_sqlite(conn), row["id"]),
                    )
                    merged += 1
            elif typ == "episode":
                node = item.get("episode") or {}
                show_tmdb = ((node.get("show") or {}).get("ids") or {}).get("tmdb")
                if not show_tmdb:
                    continue
                title = _local_title_by_tmdb(conn, int(show_tmdb))
                if title is None:
                    continue
                season = node.get("season")
                number = node.get("number")
                # Keyed by tmdb_id app-wide (see movie branch).
                row = _find_progress(conn, int(show_tmdb), season, number)
                if row is None:
                    conn.execute(
                        "INSERT INTO watch_progress (title_id, season, episode, "
                        "position_ms, duration_ms, completed) "
                        "VALUES (?, ?, ?, 0, 0, 1)",
                        (int(show_tmdb), season, number),
                    )
                    merged += 1
                elif not row["completed"]:
                    conn.execute(
                        "UPDATE watch_progress SET completed = 1, completed_at = ? "
                        "WHERE id = ?",
                        (_now_sqlite(conn), row["id"]),
                    )
                    merged += 1
        conn.commit()
    finally:
        conn.close()
    return merged, errors


async def sync_all(client: TraktClient, db_path) -> dict:
    """Push local changes to Trakt, pull Trakt activity, record status.

    DEPRECATED (P-M3): replaced by cloud sync; retained for the one-off Trakt
    import (P-M5) and must not be extended.
    Degrades gracefully: no auth / offline / partial API failure -> no crash,
    error recorded, local keeps working. Returns {pushed, pulled, error}.
    """
    if not client.is_authenticated():
        _record_sync(db_path, 0, "not authenticated")
        return {"pushed": 0, "pulled": 0, "error": "not authenticated"}
    pushed = 0
    pulled = 0
    errors: list[str] = []
    try:
        pushed = await push_progress(client, db_path)
    except TraktAuthError as e:
        errors.append(f"push auth: {e}")
    except Exception as e:
        errors.append(f"push: {type(e).__name__}: {e}")
    try:
        pulled, pull_errors = await pull_progress(client, db_path)
        errors.extend(pull_errors)
    except TraktAuthError as e:
        errors.append(f"pull auth: {e}")
    except Exception as e:
        errors.append(f"pull: {type(e).__name__}: {e}")
    error = "; ".join(errors) if errors else None
    _record_sync(db_path, pushed + pulled, error)
    return {"pushed": pushed, "pulled": pulled, "error": error}


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


def push_title(backend, db_path, key: str) -> bool:
    """Build the doc for *key* from the local DB and write it to *backend*.

    Returns False (and writes nothing) when the title row does not exist.
    """
    _t_type, tmdb_id = parse_key(key)
    conn = _conn(db_path)
    try:
        doc = build_title_doc(conn, tmdb_id)
        if doc is None:
            return False
        backend.write_title(key, doc)
        return True
    finally:
        conn.close()


def pull(backend, db_path) -> tuple[int, list[str]]:
    """Pull every stored doc and LWW-merge it into the local DB.

    One bad doc must not abort the pull: a per-title exception is recorded as
    "key: TypeName: msg" and the loop continues. Returns (rows_changed, errors).
    """
    conn = _conn(db_path)
    rows_changed = 0
    errors: list[str] = []
    try:
        for key in backend.list_titles():
            try:
                doc = backend.read_title(key)
                if doc is None:
                    continue
                rows_changed += _merge_doc(conn, doc)
            except Exception as e:
                errors.append(f"{key}: {type(e).__name__}: {e}")
        conn.commit()
    finally:
        conn.close()
    return rows_changed, errors


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
    """Pull cloud docs, push every local title, record sync status.

    Never raises: per-title failures are recorded in "error" and the loop
    continues (a bad doc or a failed push must not stop the rest). Returns
    {"pushed", "pulled", "error"}.
    """
    errors: list[str] = []
    pulled = 0
    try:
        pulled, pull_errors = pull(backend, db_path)
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

    pushed = 0
    for t in titles:
        key = make_key(t["type"], t["tmdb_id"])
        try:
            if push_title(backend, db_path, key):
                pushed += 1
        except Exception as e:
            errors.append(f"push {key}: {type(e).__name__}: {e}")

    error = "; ".join(errors) if errors else None
    # A full push leaves nothing pending, so pending_count is always 0 here.
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
    """
    def __init__(self, db_path, backend, delay_s: float = 30.0):
        self._db_path = db_path
        self._backend = backend
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
        self._timers[key] = self._loop.call_later(
            self._delay_s, self._fire, key
        )

    def _fire(self, key: str) -> None:
        self._timers.pop(key, None)
        self._pending.discard(key)
        try:
            push_title(self._backend, self._db_path, key)
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
        pushed = 0
        for key in keys:
            try:
                if push_title(self._backend, self._db_path, key):
                    pushed += 1
            except Exception:
                pass
        return pushed
