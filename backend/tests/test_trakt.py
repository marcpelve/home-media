"""Trakt client + sync tests using httpx.MockTransport (no real API calls).

Covers: device-code flow, single-use token rotation, Bearer data calls, 429
retry, push/pull merge, and graceful degradation when unauthenticated.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import init_db
from app.trakt import TraktAuthError, TraktClient
from app.sync import push_progress, pull_progress, sync_all
from app.routers.trakt import router as trakt_router

MOCK_ID = "test_client_id"
USER = "raymond"
FRESH = time.time() + 100000  # far-future expiry: no refresh needed


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "trakt.db"
    init_db(db_path)
    return str(db_path)


def _make_handler(state: dict, progress_payload: list, movies_payload: list):
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
            if path.endswith("/items/history"):
                state["scrobbles"].append(request)
                return httpx.Response(200, json=[{"id": 1, "type": "movie"}])
            if path.endswith("/progress/watched"):
                return httpx.Response(200, json=progress_payload)
            if path.endswith("/movies/watched"):
                return httpx.Response(200, json=movies_payload)
        return httpx.Response(404, json={})

    return handler


@pytest.fixture
def trakt_env(tmp_path: Path):
    db_path = _db(tmp_path)
    state = {"polls": 0, "scrobbles": []}
    transport = httpx.MockTransport(_make_handler(state, [], []))
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

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.trakt.tv":
            if request.url.path == "/oauth/device/code":
                captured["code"] = json.loads(request.content)
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
                captured["token"] = json.loads(request.content)
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

def test_mark_watched_sends_bearer(trakt_env):
    client = trakt_env["client"]
    state = trakt_env["state"]
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await client.mark_watched(
                {
                    "played_at": "2026-01-01T00:00:00Z",
                    "type": "movie",
                    "movie": {"ids": {"tmdb": 155}},
                }
            )

    result = asyncio.run(run())
    assert isinstance(result, list)
    scrob = state["scrobbles"][0]
    assert scrob.headers.get("authorization") == "Bearer at1"
    assert scrob.url.path == f"/users/{USER}/items/history"


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
# Sync engine
# ---------------------------------------------------------------------------

def test_push_progress_scrobbles_local_rows(trakt_env):
    client = trakt_env["client"]
    db = trakt_env["db"]
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (155, 'movie', 'T')"
    )
    title_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO watch_progress (title_id, position_ms, duration_ms) "
        "VALUES (?, 1000, 60000)",
        (title_id,),
    )
    conn.commit()
    conn.close()
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await push_progress(client, db)

    assert asyncio.run(run()) == 1
    scrob = trakt_env["state"]["scrobbles"][0]
    body = scrob.content.decode()
    assert "155" in body  # tmdb id in the scrobble


def test_push_progress_noop_unauthenticated(trakt_env):
    client = trakt_env["client"]
    db = trakt_env["db"]

    async def run():
        async with client:
            return await push_progress(client, db)

    assert asyncio.run(run()) == 0


def test_pull_progress_merges_in_progress(tmp_path: Path):
    db_path = _db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (155, 'movie', 'T')"
    )
    title_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    state = {"scrobbles": []}
    progress_payload = [
        {
            "node": {"type": "movie", "ids": {"tmdb": 155}, "runtime": 120},
            "progress": {"percent": 50.0},
            "completed": False,
        }
    ]
    transport = httpx.MockTransport(
        _make_handler(state, progress_payload, [])
    )
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await pull_progress(client, db_path)

    assert asyncio.run(run()) == 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (title_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    # 50% of 120 min (7200000 ms) = 3600000 ms.
    assert row["position_ms"] == 3600000
    assert row["duration_ms"] == 7200000


def test_pull_progress_marks_watched_movie_completed(tmp_path: Path):
    db_path = _db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title) VALUES (155, 'movie', 'T')"
    )
    title_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    state = {"scrobbles": []}
    movies_payload = [
        {"type": "movie", "ids": {"tmdb": 155}, "completed": True}
    ]
    transport = httpx.MockTransport(_make_handler(state, [], movies_payload))
    http_client = httpx.AsyncClient(
        base_url="https://api.trakt.tv", transport=transport
    )
    client = TraktClient(client_id=MOCK_ID, db_path=db_path, client=http_client)
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await pull_progress(client, db_path)

    assert asyncio.run(run()) == 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM watch_progress WHERE title_id = ?", (title_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["completed"] == 1


def test_sync_all_not_authenticated(trakt_env):
    client = trakt_env["client"]
    db = trakt_env["db"]

    async def run():
        async with client:
            return await sync_all(client, db)

    result = asyncio.run(run())
    assert result == {"pushed": 0, "pulled": 0, "error": "not authenticated"}


def test_sync_all_records_status(trakt_env):
    client = trakt_env["client"]
    db = trakt_env["db"]
    client.save_tokens(USER, "at1", "rt1", FRESH)

    async def run():
        async with client:
            return await sync_all(client, db)

    result = asyncio.run(run())
    assert result["error"] is None
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM trakt_sync").fetchone()
    conn.close()
    assert row is not None
    assert row["last_sync_at"] is not None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def test_router_status_not_authenticated(tmp_path: Path):
    db_path = _db(tmp_path)
    transport = httpx.MockTransport(_make_handler({"scrobbles": []}, [], []))
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
        assert data["last_sync"] is None
    asyncio.run(http_client.aclose())
