"""TMDB router tests using httpx.MockTransport (no real API calls)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import init_db
from app.tmdb import TmdbClient
from app.routers.tmdb import router as tmdb_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_TOKEN = "test_bearer_token"

# Standard mock payloads
# popularity is set >= MIN_POPULARITY (1.0) so /tmdb/search's popularity filter
# keeps them (real TMDB search results always carry a popularity field).
SEARCH_MOVIE = {"results": [{"id": 1, "title": "Movie A", "popularity": 5.0}]}
SEARCH_TV = {"results": [{"id": 2, "title": "Show B", "popularity": 5.0}]}
TRENDING = {"results": [{"id": 10, "title": "Trending"}]}
GENRE_MOVIE = {"genres": [{"id": 1, "name": "Drama"}]}
GENRE_TV = {"genres": [{"id": 2, "name": "Comedy"}]}
TITLE_DETAILS = {"id": 55, "title": "Some Movie", "poster_path": "/abc.jpg"}
TV_DETAILS = {
    "id": 77,
    "name": "Some Show",
    "first_air_date": "2016-01-11",
    "external_ids": {"imdb_id": "tt123"},
}
REVIEWS = {"results": [{"id": 99, "content": "Great!"}]}


def _make_handler(calls: list, payloads: dict[str, httpx.Response]):
    """Build a MockTransport handler that records calls and serves canned responses."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Match by path suffix
        path = str(request.url.path)
        for key, resp in payloads.items():
            if path.endswith(key):
                return resp
        return httpx.Response(404, json={"status_message": "Not found"})
    return handler


@pytest.fixture
def tmdb_app(tmp_path: Path):
    """Create a FastAPI app with a TmdbClient backed by MockTransport."""
    db_path = tmp_path / "test_tmdb.db"
    init_db(db_path)

    calls: list[httpx.Request] = []
    payloads: dict[str, httpx.Response] = {
        "/search/movie": httpx.Response(200, json=SEARCH_MOVIE),
        "/search/tv": httpx.Response(200, json=SEARCH_TV),
        "/trending/all/week": httpx.Response(200, json=TRENDING),
        "/genre/list/movie": httpx.Response(200, json=GENRE_MOVIE),
        "/genre/list/tv": httpx.Response(200, json=GENRE_TV),
        "/movie/55": httpx.Response(200, json=TITLE_DETAILS),
        "/movie/55/external_ids": httpx.Response(200, json={"imdb_id": "tt55"}),
        "/tv/77": httpx.Response(200, json=TV_DETAILS),
        "/tv/77/external_ids": httpx.Response(200, json={"imdb_id": "tt123"}),
        "/movie/55/reviews": httpx.Response(200, json=REVIEWS),
        "/movie/popular": httpx.Response(
            200, json={"results": [{"id": 20, "title": "Pop Movie"}]}
        ),
        "/tv/popular": httpx.Response(
            200, json={"results": [{"id": 21, "name": "Pop Show"}]}
        ),
        "/discover/movie": httpx.Response(
            200, json={"results": [{"id": 30, "title": "New Movie"}]}
        ),
        "/discover/tv": httpx.Response(
            200, json={"results": [{"id": 31, "name": "New Show"}]}
        ),
        # id 99 exists as BOTH a movie (no poster) and a tv show (has a
        # poster) - TMDB shares numeric ids across media types.
        "/movie/99": httpx.Response(
            200, json={"id": 99, "title": "Movie 99", "poster_path": None}
        ),
        "/movie/99/external_ids": httpx.Response(200, json={"imdb_id": "tt99m"}),
        "/tv/99": httpx.Response(
            200, json={"id": 99, "name": "Show 99", "poster_path": "/tvposter.jpg"}
        ),
        "/tv/99/external_ids": httpx.Response(200, json={"imdb_id": "tt99t"}),
        "/t/p/w342/tvposter.jpg": httpx.Response(200, content=b"fake-bytes"),
    }

    transport = httpx.MockTransport(_make_handler(calls, payloads))
    http_client = httpx.AsyncClient(
        base_url="https://api.themoviedb.org/3",
        headers={"Authorization": f"Bearer {MOCK_TOKEN}"},
        params={"language": "en-US"},
        transport=transport,
    )

    client = TmdbClient(
        token=MOCK_TOKEN,
        db_path=db_path,
        poster_dir=tmp_path / "posters",
        client=http_client,
    )

    app = FastAPI()
    app.state.tmdb_client = client
    app.include_router(tmdb_router)

    with TestClient(app) as tc:
        yield {"client": tc, "calls": calls, "http": http_client}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_bearer_header_sent(tmdb_app):
    """The client sends the Bearer token on API requests."""
    tc: TestClient = tmdb_app["client"]
    calls: list[httpx.Request] = tmdb_app["calls"]

    tc.get("/tmdb/home")

    assert len(calls) >= 1
    auth_header = calls[0].headers.get("authorization")
    assert auth_header == f"Bearer {MOCK_TOKEN}"


def test_cache_hit_no_second_call(tmdb_app):
    """A second request for the same path is served from cache (no new HTTP call)."""
    tc: TestClient = tmdb_app["client"]
    calls: list[httpx.Request] = tmdb_app["calls"]

    tc.get("/tmdb/home")
    first_call_count = len(calls)

    tc.get("/tmdb/home")
    assert len(calls) == first_call_count, "Cache hit should not trigger a new HTTP call"


def test_cache_expiry_refetches(tmp_path: Path):
    """After TTL expires, the client refetches from the API."""
    import asyncio
    import sqlite3

    db_path = tmp_path / "expiry.db"
    init_db(db_path)

    calls: list[httpx.Request] = []
    payloads = {
        "/trending/all/week": httpx.Response(200, json=TRENDING),
    }
    transport = httpx.MockTransport(_make_handler(calls, payloads))
    http_client = httpx.AsyncClient(
        base_url="https://api.themoviedb.org/3",
        headers={"Authorization": f"Bearer {MOCK_TOKEN}"},
        transport=transport,
    )

    client = TmdbClient(
        token=MOCK_TOKEN,
        db_path=db_path,
        client=http_client,
    )

    async def run():
        # First fetch - populates cache
        data1 = await client.home()
        assert data1 == TRENDING
        assert len(calls) == 1

        # Manually expire the cache entry
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE tmdb_cache SET created_at = created_at - 200000")
        conn.commit()
        conn.close()

        # Second fetch - should refetch
        data2 = await client.home()
        assert data2 == TRENDING
        assert len(calls) == 2
        await http_client.aclose()

    asyncio.run(run())


def test_search_endpoint_shape(tmdb_app):
    """GET /tmdb/search returns {movie: [...], tv: [...]}."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/search", params={"q": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "movie" in data
    assert "tv" in data
    assert data["movie"] == [SEARCH_MOVIE["results"][0]]
    assert data["tv"] == [SEARCH_TV["results"][0]]


def test_home_endpoint_shape(tmdb_app):
    """GET /tmdb/home returns the trending payload."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/home")
    assert resp.status_code == 200
    data = resp.json()
    assert data == TRENDING


def test_categories_endpoint_shape(tmdb_app):
    """GET /tmdb/categories returns {movie: [...], tv: [...]}."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"movie": GENRE_MOVIE["genres"], "tv": GENRE_TV["genres"]}


def test_title_endpoint_shape(tmdb_app):
    """GET /tmdb/title/{id} returns details with _type field."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/title/55")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 55
    assert data["title"] == "Some Movie"
    assert data["_type"] == "movie"


def test_title_tv_endpoint_fetches_external_ids(tmdb_app):
    """GET /tmdb/title/{id} for a TV show pulls imdb_id from the dedicated
    external_ids endpoint (the detail response omits it and append_to is
    not reliably populated for tv)."""
    tc: TestClient = tmdb_app["client"]
    calls: list[httpx.Request] = tmdb_app["calls"]

    before = len(calls)
    resp = tc.get("/tmdb/title/77")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_type"] == "tv"
    assert data["name"] == "Some Show"
    # imdb_id is filled from the dedicated external_ids endpoint
    assert data.get("imdb_id") == "tt123"

    # A dedicated /tv/77/external_ids request was made (not append_to)
    ext_calls = [c for c in calls[before:] if str(c.url.path).endswith("/tv/77/external_ids")]
    assert ext_calls, "expected a tv/77/external_ids request"


def test_reviews_endpoint_shape(tmdb_app):
    """GET /tmdb/reviews/{id} returns {results: [...], type: 'movie'}."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/reviews/55")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == [REVIEWS["results"][0]]
    assert data["type"] == "movie"


def test_popular_endpoint_shape(tmdb_app):
    """GET /tmdb/popular returns {movie: [...], tv: [...]}."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/popular")
    assert resp.status_code == 200
    data = resp.json()
    assert data["movie"] == [{"id": 20, "title": "Pop Movie"}]
    assert data["tv"] == [{"id": 21, "name": "Pop Show"}]


def test_latest_endpoint_shape(tmdb_app):
    """GET /tmdb/latest returns {movie: [...], tv: [...]} via /discover,
    filtered to a recent window (no upcoming/future titles)."""
    tc: TestClient = tmdb_app["client"]
    calls: list[httpx.Request] = tmdb_app["calls"]
    resp = tc.get("/tmdb/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["movie"] == [{"id": 30, "title": "New Movie"}]
    assert data["tv"] == [{"id": 31, "name": "New Show"}]
    # The discover calls must use a recent window (date lte today) so upcoming
    # titles with future dates are excluded, and a popularity sort (the
    # primary_release_date sort ignores the date filters).
    movie_calls = [c for c in calls if str(c.url.path).endswith("/discover/movie")]
    assert movie_calls, "expected a discover/movie request"
    mp = movie_calls[0].url.params
    assert mp.get("release_date.lte"), "expected release_date.lte on discover/movie"
    assert mp.get("release_date.gte"), "expected release_date.gte on discover/movie"
    tv_calls = [c for c in calls if str(c.url.path).endswith("/discover/tv")]
    assert tv_calls, "expected a discover/tv request"
    tp = tv_calls[0].url.params
    assert tp.get("first_air_date.lte"), "expected first_air_date.lte on discover/tv"
    assert tp.get("first_air_date.gte"), "expected first_air_date.gte on discover/tv"


def test_poster_endpoint_type_aware(tmdb_app):
    """GET /tmdb/poster/{id}?type=tv resolves the tv show's poster, not the
    colliding movie (TMDB shares numeric ids across media types)."""
    tc: TestClient = tmdb_app["client"]
    # With type=tv, the tv show (id 99) has a poster -> 200 with the bytes.
    resp = tc.get("/tmdb/poster/99", params={"type": "tv"})
    assert resp.status_code == 200
    assert resp.content == b"fake-bytes"
    # With type=movie, the movie (id 99) has no poster -> 404.
    resp2 = tc.get("/tmdb/poster/99", params={"type": "movie"})
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# In-app auth (API token in the settings table)
# ---------------------------------------------------------------------------

VALID_TMDB_TOKEN = "valid_tmdb_token"


def _tmdb_auth_handler(calls: list, valid_token: str):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url.path).endswith("/configuration"):
            if request.headers.get("authorization") == f"Bearer {valid_token}":
                return httpx.Response(200, json={"image": {"base_path": "/x"}})
            return httpx.Response(401, json={"status_code": 401})
        return httpx.Response(404, json={})
    return handler


@pytest.fixture
def tmdb_auth_app(tmp_path: Path):
    import asyncio

    db_path = tmp_path / "auth.db"
    init_db(db_path)
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(_tmdb_auth_handler(calls, VALID_TMDB_TOKEN))
    http_client = httpx.AsyncClient(
        base_url="https://api.themoviedb.org/3", transport=transport
    )
    # Start with no .env token so the settings key is the only source.
    client = TmdbClient(token="", db_path=db_path, client=http_client)
    app = FastAPI()
    app.state.tmdb_client = client
    app.include_router(tmdb_router)
    with TestClient(app) as tc:
        yield {"client": tc, "tmdb": client, "calls": calls, "http": http_client}
    asyncio.run(http_client.aclose())


def test_tmdb_status_not_configured(tmdb_auth_app):
    tc = tmdb_auth_app["client"]
    r = tc.get("/tmdb/status")
    assert r.status_code == 200
    assert r.json() == {"configured": False}


def test_tmdb_data_endpoints_503_when_unconfigured(tmdb_auth_app):
    """Data endpoints return 503 (not a raw 500) when no TMDB token is set."""
    tc = tmdb_auth_app["client"]
    for path in ["/tmdb/home", "/tmdb/popular", "/tmdb/latest", "/tmdb/categories"]:
        r = tc.get(path)
        assert r.status_code == 503, f"{path}: expected 503, got {r.status_code}"
        assert r.json()["detail"] == "TMDB not configured"


def test_tmdb_auth_valid_stores_key(tmdb_auth_app):
    tc = tmdb_auth_app["client"]
    tmdb = tmdb_auth_app["tmdb"]
    r = tc.post("/tmdb/auth", json={"key": VALID_TMDB_TOKEN})
    assert r.status_code == 200
    assert r.json() == {"configured": True}
    assert tmdb.get_key() == VALID_TMDB_TOKEN
    assert tc.get("/tmdb/status").json() == {"configured": True}


def test_tmdb_auth_invalid_rejected(tmdb_auth_app):
    tc = tmdb_auth_app["client"]
    tmdb = tmdb_auth_app["tmdb"]
    r = tc.post("/tmdb/auth", json={"key": "wrong_key"})
    assert r.status_code == 401
    # Nothing stored on failure.
    assert tmdb.get_key() is None


def test_tmdb_auth_empty_rejected(tmdb_auth_app):
    tc = tmdb_auth_app["client"]
    r = tc.post("/tmdb/auth", json={"key": "   "})
    assert r.status_code == 400


def test_tmdb_auth_clear(tmdb_auth_app):
    tc = tmdb_auth_app["client"]
    tmdb = tmdb_auth_app["tmdb"]
    tc.post("/tmdb/auth", json={"key": VALID_TMDB_TOKEN})
    r = tc.delete("/tmdb/auth")
    assert r.status_code == 200
    assert r.json() == {"configured": False}
    assert tmdb.get_key() is None


def test_tmdb_settings_key_overrides_env_token(tmp_path: Path):
    """A key saved in-app wins over the .env-supplied token."""
    import asyncio

    db_path = tmp_path / "prec.db"
    init_db(db_path)
    calls: list[httpx.Request] = []
    payloads = {"/trending/all/week": httpx.Response(200, json=TRENDING)}
    transport = httpx.MockTransport(_make_handler(calls, payloads))
    http_client = httpx.AsyncClient(
        base_url="https://api.themoviedb.org/3", transport=transport
    )
    client = TmdbClient(token="env_token", db_path=db_path, client=http_client)
    client.set_key("settings_token")
    assert client._effective_key() == "settings_token"
    assert client.is_configured() is True

    async def run():
        return await client.home()

    asyncio.run(run())
    # The request carried the settings key, not the env token.
    assert calls[0].headers.get("authorization") == "Bearer settings_token"
    asyncio.run(http_client.aclose())
