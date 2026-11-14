"""Trakt client + one-time import tests using httpx.MockTransport (no real
API calls).

Covers: device-code flow, single-use token rotation, Bearer data calls, 429
retry, the one-time Trakt import (P-M5), and graceful degradation when
unauthenticated.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import sqlite3
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import init_db
from app.trakt import TraktAuthError, TraktClient
from app.sync import import_from_trakt
from app.routers.trakt import router as trakt_router

MOCK_ID = "test_client_id"
USER = "raymond"
FRESH = time.time() + 100000  # far-future expiry: no refresh needed


class _MockTmdb:
    """Minimal stand-in for TmdbClient: title_typed returns a poster_path."""

    def __init__(self, posters: dict | None = None):
        self._posters = posters or {}

    async def title_typed(self, tmdb_id: int, media_type: str) -> dict:
        return {"poster_path": self._posters.get(tmdb_id)}


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "trakt.db"
    init_db(db_path)
    return str(db_path)


def _make_handler(
    state: dict,
    progress_payload: list,
    movies_payload: list,
    history_payload: list | None = None,
):
    if history_payload is None:
        history_payload = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "auth.trakt.tv":
            if path == "/oauth/device/code":
                return httpx.Response(
                    200,
                    json={
                        "device_code": "dev123",
                        "user_code": "WXYZ-1234",
                        "verification_url": "https://trakt.tv/activate",
                        "expires_in": 900,
                    },
                )
            if path == "/oauth/device/token":
                state["polls"] = state.get("polls", 0) + 1
                if state["polls"] < state.get("authorize_at", 2):
                    return httpx.Response(
                        400, json={"error": "authorization_pending"}
                    )
                return httpx.Response(
                    200,
                    json={
                        "access_token": "at1",
                        "token_type": "Bearer",
                        "expires_in": 604800,
                        "refresh_token": "rt1",
                        "trakt": {"username": USER},
                    },
                )
            if path == "/oauth/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "at2",
                        "token_type": "Bearer",
                        "expires_in": 604800,
                        "refresh_token": "rt2",
                    },
                )
            return httpx.Response(404, json={})
        if host == "api.trakt.tv":
            if path.endswith("/scrobble/stop"):
                state["scrobbles"].append(request)
                return httpx.Response(
                    201, json={"id": 1, "type": "movie", "action": "scrobble"}
                )
            if path.endswith("/progress/watched"):
                return httpx.Response(200, json=progress_payload)
            if path.endswith("/movies/watched"):
                return httpx.Response(200, json=movies_payload)
            if path.endswith("/history"):
                return httpx.Response(200, json=history_payload)
        return httpx.Response(404, json={})

    return handler


@pytest.fixture
def trakt_env(tmp_path: Path):
    db_path = _db(tmp_path)
    state = {"polls": 0, "scrobbles": []}
    transport = httpx.MockTransport(_make_handler(state, [], [], []))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    yield {"client": client, "db": db_path, "state": state, "http": http_client}
    asyncio.run(http_client.aclose())


# ---------------------------------------------------------------------------
# Device code flow
# ---------------------------------------------------------------------------

def test_start_device_login(trakt_env):
    client = trakt_env["client"]

    async def run():
        async with client:
            return await client.start_device_login()

    result = asyncio.run(run())
    assert result["user_code"] == "WXYZ-1234"
    assert result["verification_url"] == "https://trakt.tv/activate"


def test_device_login_flow(trakt_env):
    client = trakt_env["client"]

    async def run():
        async with client:
            await client.start_device_login()
            r1 = await client.poll_pending()
            r2 = await client.poll_pending()
            return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1["status"] == "pending"
    assert r2["status"] == "authorized"
    assert r2["username"] == USER
    # Tokens saved to the local DB.
    assert client.is_authenticated()
    tokens = client.load_tokens()
    assert tokens["access_token"] == "at1"
    assert tokens["refresh_token"] == "rt1"
    assert tokens["username"] == USER


def test_poll_none_when_no_pending(trakt_env):
    client = trakt_env["client"]

    async def run():
        async with client:
            return await client.poll_pending()

    assert asyncio.run(run())["status"] == "none"


def test_poll_pending_tolerates_empty_400_body(tmp_path: Path):
    """A 400 with a non-JSON/empty body must not crash the poll loop."""
    db_path = _db(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "auth.trakt.tv":
            if path == "/oauth/device/code":
                return httpx.Response(
                    200,
                    json={
                        "device_code": "dev123",
                        "user_code": "WXYZ-1234",
                        "verification_url": "https://trakt.tv/activate",
                        "expires_in": 900,
                    },
                )
            if path == "/oauth/device/token":
                return httpx.Response(400, content=b"")
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)

    async def run():
        async with client:
            await client.start_device_login()
            return await client.poll_pending()

    assert asyncio.run(run())["status"] == "pending"
    asyncio.run(http_client.aclose())


def test_device_flow_sends_pkce_pair(tmp_path: Path):
    """The code request must carry a code_challenge and the token request the
    matching code_verifier (S256), per Trakt's PKCE requirement for the flow."""
    db_path = _db(tmp_path)
    captured: dict = {}

    def _form(request: httpx.Request) -> dict:
        # The device-code and token requests are form-encoded (per Trakt's API),
        # not JSON, so parse the query-string body into a flat dict.
        return {k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.trakt.tv":
            if request.url.path == "/oauth/device/code":
                captured["code"] = _form(request)
                return httpx.Response(
                    200,
                    json={
                        "device_code": "dev123",
                        "user_code": "WXYZ-1234",
                        "verification_url": "https://trakt.tv/activate",
                        "expires_in": 900,
                    },
                )
            if request.url.path == "/oauth/device/token":
                captured["token"] = _form(request)
                return httpx.Response(
                    400, json={"error": "authorization_pending"}
                )
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)

    async def run():
        async with client:
            await client.start_device_login()
            return await client.poll_pending()

    asyncio.run(run())
    code_body = captured["code"]
    token_body = captured["token"]
    assert code_body.get("code_challenge_method") == "S256"
    challenge = code_body.get("code_challenge")
    verifier = token_body.get("code_verifier")
    assert verifier
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected
    asyncio.run(http_client.aclose())


def test_poll_pending_fetches_username_when_absent(tmp_path: Path):
    """The real token response carries no username; the client fetches it from
    /users/me and stores it."""
    db_path = _db(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "auth.trakt.tv":
            if path == "/oauth/device/code":
                return httpx.Response(
                    200,
                    json={
                        "device_code": "dev123",
                        "user_code": "WXYZ-1234",
                        "verification_url": "https://trakt.tv/activate",
                        "expires_in": 900,
                    },
                )
            if path == "/oauth/device/token":
                # Real Trakt: no username in the token response.
                return httpx.Response(
                    200,
                    json={
                        "access_token": "at1",
                        "token_type": "Bearer",
                        "expires_in": 604800,
                        "refresh_token": "rt1",
                    },
                )
            return httpx.Response(404, json={})
        if host == "api.trakt.tv":
            if path == "/users/me":
                return httpx.Response(200, json={"username": "Exorbis"})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)

    async def run():
        async with client:
            await client.start_device_login()
            return await client.poll_pending()

    result = asyncio.run(run())
    assert result["status"] == "authorized"
    assert result["username"] == "Exorbis"
    tokens = client.load_tokens()
    assert tokens is not None
    assert tokens["username"] == "Exorbis"
    asyncio.run(http_client.aclose())


# ---------------------------------------------------------------------------
# Token rotation (single-use refresh)
# ---------------------------------------------------------------------------

def test_get_access_token_fresh(trakt_env):
    client = trakt_env["client"]
    client.save_tokens(USER, "at_fresh", "rt_fresh", FRESH)

    async def run():
        async with client:
            return await client.get_access_token()

    assert asyncio.run(run()) == "at_fresh"


def test_get_access_token_refreshes_expired(trakt_env):
    client = trakt_env["client"]
    client.save_tokens(USER, "at_old", "rt_old", time.time() - 10)

    async def run():
        async with client:
            return await client.get_access_token()

    token = asyncio.run(run())
    assert token == "at2"
    # Single-use: the stored pair rotated to the new refresh token.
    tokens = client.load_tokens()
    assert tokens["access_token"] == "at2"
    assert tokens["refresh_token"] == "rt2"


def test_get_access_token_not_authenticated(trakt_env):
    client = trakt_env["client"]

    async def run():
        async with client:
            return await client.get_access_token()

    with pytest.raises(TraktAuthError):
        asyncio.run(run())


# ---------------------------------------------------------------------------
# Data calls
# ---------------------------------------------------------------------------

def test_429_retry(tmp_path: Path):
    db_path = _db(tmp_path)
    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.trakt.tv":
            state["count"] += 1
            if state["count"] == 1:
                return httpx.Response(
                    429, headers={"Retry-After": "0"}, json={}
                )
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await client.get_progress()

    asyncio.run(run())
    assert state["count"] == 2  # one 429, then a successful retry


# ---------------------------------------------------------------------------
# One-time Trakt import (P-M5)
# ---------------------------------------------------------------------------

def _client_with_history(tmp_path: Path, history_payload: list):
    """An authenticated TraktClient whose /history returns *history_payload*.

    Returns (client, db_path, http_client). The payload has fewer than 100
    items so get_all_history() returns it after page 1.
    """
    db_path = _db(tmp_path)
    state = {"scrobbles": []}
    transport = httpx.MockTransport(_make_handler(state, [], [], history_payload))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)
    return client, db_path, http_client


def test_get_all_history_paginates(tmp_path: Path):
    """get_all_history loops pages until a short batch: 100+100+37 -> 237."""
    db_path = _db(tmp_path)
    pages = {
        "1": [{"i": n} for n in range(100)],
        "2": [{"i": n} for n in range(100, 200)],
        "3": [{"i": n} for n in range(200, 237)],
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history"):
            page = request.url.params.get("page", "1")
            requested.append(page)
            return httpx.Response(200, json=pages.get(page, []))
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await client.get_all_history()

    result = asyncio.run(run())
    assert len(result) == 237
    assert requested == ["1", "2", "3"]  # pages requested in order
    asyncio.run(http_client.aclose())


def test_import_movie_creates_title_and_row(tmp_path: Path):
    """A movie history item with no local title creates a minimal titles row +
    a completed watch_progress row (completed_at = played_at verbatim)."""
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result == {
        "imported": 1, "titles_created": 1, "skipped": 0, "error": None
    }
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT * FROM titles WHERE tmdb_id = ?", (155,)).fetchone()
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (155,)
    ).fetchone()
    conn.close()
    assert t is not None
    assert t["type"] == "movie"
    assert t["title"] == "Star Wars"
    assert t["year"] == 1999
    assert t["device"] == "default"  # device stamped (device_id unset)
    assert row is not None
    assert row["completed"] == 1
    assert row["completed_at"] == "2025-06-01T12:00:00Z"  # verbatim
    assert row["season"] is None
    assert row["episode"] is None
    assert row["device"] == "default"
    asyncio.run(http_client.aclose())


def test_import_episode(tmp_path: Path):
    """An episode history item keys the row on the SHOW's tmdb + season/number."""
    history = [
        {
            "type": "episode",
            "played_at": "2025-07-02T09:30:00Z",
            "episode": {
                "ids": {"tmdb": 99999},
                "show": {"ids": {"tmdb": 66732}, "title": "Show", "year": 2020},
                "season": 2,
                "number": 5,
            },
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result["imported"] == 1
    assert result["titles_created"] == 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT * FROM titles WHERE tmdb_id = ?", (66732,)).fetchone()
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (66732,)
    ).fetchone()
    conn.close()
    assert t is not None
    assert t["type"] == "tv"
    assert row is not None
    assert row["season"] == 2
    assert row["episode"] == 5
    assert row["completed"] == 1
    assert row["completed_at"] == "2025-07-02T09:30:00Z"
    asyncio.run(http_client.aclose())


def test_import_dedupes_replays(tmp_path: Path):
    """The same episode watched twice yields exactly one row, with the LATER
    played_at as completed_at."""
    history = [
        {
            "type": "episode",
            "played_at": "2025-07-02T09:30:00Z",
            "episode": {
                "ids": {"tmdb": 99999},
                "show": {"ids": {"tmdb": 66732}, "title": "Sh", "year": 2011},
                "season": 1,
                "number": 2,
            },
        },
        {
            "type": "episode",
            "played_at": "2025-08-01T10:00:00Z",  # later replay
            "episode": {
                "ids": {"tmdb": 99999},
                "show": {"ids": {"tmdb": 66732}, "title": "Sh", "year": 2011},
                "season": 1,
                "number": 2,
            },
        },
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result["imported"] == 1  # deduped to one
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (66732,)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["completed_at"] == "2025-08-01T10:00:00Z"  # the later one
    asyncio.run(http_client.aclose())


def test_import_lww_local_newer_wins(tmp_path: Path):
    """A local in-progress row newer than played_at is left untouched."""
    db_path = _db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (155, 'movie', 'T')"
    )
    conn.execute(
        "INSERT INTO watch_progress (title_id, position_ms, duration_ms, "
        "completed, updated_at) VALUES (?, 5000, 60000, 0, ?)",
        (155, "2026-01-01 00:00:00"),  # newer than played_at
    )
    conn.commit()
    conn.close()
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result["imported"] == 0
    assert result["skipped"] == 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (155,)
    ).fetchone()
    conn.close()
    assert row["completed"] == 0  # untouched
    assert row["updated_at"] == "2026-01-01 00:00:00"  # untouched
    asyncio.run(http_client.aclose())


def test_import_lww_trakt_newer_wins(tmp_path: Path):
    """A local row older than played_at is marked completed with played_at."""
    db_path = _db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (155, 'movie', 'T')"
    )
    conn.execute(
        "INSERT INTO watch_progress (title_id, position_ms, duration_ms, "
        "completed, updated_at) VALUES (?, 5000, 60000, 0, ?)",
        (155, "2024-01-01 00:00:00"),  # older than played_at
    )
    conn.commit()
    conn.close()
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result["imported"] == 1
    assert result["skipped"] == 0
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (155,)
    ).fetchone()
    conn.close()
    assert row["completed"] == 1
    assert row["completed_at"] == "2025-06-01T12:00:00Z"
    asyncio.run(http_client.aclose())


def test_import_existing_title_not_recreated(tmp_path: Path):
    """A title already in the DB is not re-created (titles_created == 0)."""
    db_path = _db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (155, 'movie', 'Existing')"
    )
    conn.commit()
    conn.close()
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    result = asyncio.run(run())
    assert result["imported"] == 1
    assert result["titles_created"] == 0  # title already existed
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    t = conn.execute("SELECT * FROM titles WHERE tmdb_id = ?", (155,)).fetchone()
    conn.close()
    assert t["title"] == "Existing"  # not overwritten
    asyncio.run(http_client.aclose())


def test_import_unauthenticated(tmp_path: Path):
    """No tokens -> a clean 'not authenticated' result and no DB writes."""
    db_path = _db(tmp_path)
    state = {"scrobbles": []}
    transport = httpx.MockTransport(_make_handler(state, [], [], []))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)

    async def run():
        async with client:
            return await import_from_trakt(client, db_path)

    result = asyncio.run(run())
    assert result == {
        "imported": 0, "titles_created": 0, "skipped": 0,
        "error": "not authenticated",
    }
    conn = sqlite3.connect(db_path)
    n_titles = conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
    n_prog = conn.execute("SELECT COUNT(*) FROM watch_progress").fetchone()[0]
    conn.close()
    assert n_titles == 0
    assert n_prog == 0
    asyncio.run(http_client.aclose())


def test_import_idempotent(tmp_path: Path):
    """Running the import twice with the same history imports 0 the second
    time (the first run stamped updated_at=now > played_at)."""
    db_path = _db(tmp_path)
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            r1 = await import_from_trakt(client, db)
            r2 = await import_from_trakt(client, db)
            return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1["imported"] == 1
    assert r2["imported"] == 0  # idempotent
    asyncio.run(http_client.aclose())


def test_import_route(tmp_path: Path):
    """POST /trakt/import: 401 unauthenticated; 200 with counts when authed."""
    db_path = _db(tmp_path)
    # Unauthenticated -> 401.
    state = {"scrobbles": []}
    transport = httpx.MockTransport(_make_handler(state, [], [], []))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    app = FastAPI()
    app.include_router(trakt_router)
    app.state.trakt = client
    app.state.db_path = db_path
    with TestClient(app) as c:
        r = c.post("/trakt/import")
        assert r.status_code == 401
    asyncio.run(http_client.aclose())

    # Authenticated -> 200 with counts.
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    state2 = {"scrobbles": []}
    transport2 = httpx.MockTransport(_make_handler(state2, [], [], history))
    http_client2 = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport2
    )
    client2 = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client2)
    client2.save_tokens(USER, "at1", "rt1", FRESH)
    app2 = FastAPI()
    app2.include_router(trakt_router)
    app2.state.trakt = client2
    app2.state.db_path = db_path
    with TestClient(app2) as c:
        r = c.post("/trakt/import")
        assert r.status_code == 200
        data = r.json()
        assert data["imported"] == 1
        assert data["titles_created"] == 1
        assert data["error"] is None
    asyncio.run(http_client2.aclose())


def test_import_records_status(tmp_path: Path):
    """After an import, settings carry the import status and GET /trakt/status
    reports last_import."""
    db_path = _db(tmp_path)
    history = [
        {
            "type": "movie",
            "played_at": "2025-06-01T12:00:00Z",
            "movie": {"ids": {"tmdb": 155}, "title": "Star Wars", "year": 1999},
        }
    ]
    client, db, http_client = _client_with_history(tmp_path, history)

    async def run():
        async with client:
            return await import_from_trakt(client, db)

    asyncio.run(run())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    at = conn.execute(
        "SELECT value FROM settings WHERE key = 'trakt_last_import_at'"
    ).fetchone()
    cnt = conn.execute(
        "SELECT value FROM settings WHERE key = 'trakt_last_import_count'"
    ).fetchone()
    conn.close()
    assert at is not None and at["value"] is not None
    assert cnt is not None and cnt["value"] == "1"
    app = FastAPI()
    app.include_router(trakt_router)
    app.state.trakt = client
    app.state.db_path = db
    with TestClient(app) as c:
        r = c.get("/trakt/status")
        assert r.status_code == 200
        data = r.json()
        assert data["last_import"] is not None
        assert data["last_import"]["count"] == 1
        assert data["last_import"]["at"] is not None
    asyncio.run(http_client.aclose())


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def test_router_status_not_authenticated(tmp_path: Path):
    db_path = _db(tmp_path)
    transport = httpx.MockTransport(_make_handler({"scrobles": []}, [], []))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    app = FastAPI()
    app.include_router(trakt_router)
    app.state.trakt = client
    app.state.db_path = db_path
    with TestClient(app) as c:
        r = c.get("/trakt/status")
        assert r.status_code == 200
        data = r.json()
        assert data["authenticated"] is False
        assert data["username"] is None
        assert data["last_import"] is None
    asyncio.run(http_client.aclose())


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def test_recommendations_endpoint_shape(tmp_path: Path):
    """GET /trakt/recommendations normalizes Trakt recs to {id, title, type}."""
    db_path = _db(tmp_path)
    # Real Trakt wraps each rec in a "movie"/"show" key.
    rec_movie = [{"score": 0.9, "movie": {"ids": {"tmdb": 155}, "title": "Rec Movie"}}]
    rec_show = [{"score": 0.9, "show": {"ids": {"tmdb": 645}, "title": "Rec Show"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/recommendations/movie"):
            return httpx.Response(200, json=rec_movie)
        if path.endswith("/recommendations/show"):
            return httpx.Response(200, json=rec_show)
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)
    app = FastAPI()
    app.include_router(trakt_router)
    app.state.trakt = client
    app.state.db_path = db_path
    app.state.tmdb_client = _MockTmdb({155: "/p155.jpg", 645: "/p645.jpg"})
    with TestClient(app) as c:
        r = c.get("/trakt/recommendations")
        assert r.status_code == 200
        data = r.json()
        assert data["movie"] == [{"id": 155, "title": "Rec Movie", "type": "movie", "poster_path": "/p155.jpg"}]
        assert data["show"] == [{"id": 645, "title": "Rec Show", "type": "tv", "poster_path": "/p645.jpg"}]
    asyncio.run(http_client.aclose())


def test_recommendations_unauthenticated_empty(tmp_path: Path):
    """Unauthenticated: the endpoint degrades to empty lists, not an error."""
    db_path = _db(tmp_path)
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    app = FastAPI()
    app.include_router(trakt_router)
    app.state.trakt = client
    app.state.db_path = db_path
    app.state.tmdb_client = _MockTmdb()
    with TestClient(app) as c:
        r = c.get("/trakt/recommendations")
        assert r.status_code == 200
        assert r.json() == {"movie": [], "show": []}
    asyncio.run(http_client.aclose())
