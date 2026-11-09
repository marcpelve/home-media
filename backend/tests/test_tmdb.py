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
SEARCH_MOVIE = {"results": [{"id": 1, "title": "Movie A"}]}
SEARCH_TV = {"results": [{"id": 2, "title": "Show B"}]}
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
        "/tv/77": httpx.Response(200, json=TV_DETAILS),
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
    """GET /tmdb/title/{id} for a TV show requests external_ids (for imdb_id)."""
    tc: TestClient = tmdb_app["client"]
    calls: list[httpx.Request] = tmdb_app["calls"]

    before = len(calls)
    resp = tc.get("/tmdb/title/77")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_type"] == "tv"
    assert data["name"] == "Some Show"
    # imdb_id is available via external_ids
    assert (data.get("external_ids") or {}).get("imdb_id") == "tt123"

    # The tv fetch must have requested append_to=external_ids
    tv_calls = [c for c in calls[before:] if str(c.url.path).endswith("/tv/77")]
    assert tv_calls, "expected a tv/77 request"
    assert tv_calls[0].url.params.get("append_to") == "external_ids"


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
    """GET /tmdb/latest returns {movie: [...], tv: [...]} via /discover."""
    tc: TestClient = tmdb_app["client"]
    resp = tc.get("/tmdb/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["movie"] == [{"id": 30, "title": "New Movie"}]
    assert data["tv"] == [{"id": 31, "name": "New Show"}]
