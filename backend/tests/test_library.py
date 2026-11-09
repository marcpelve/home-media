"""Offline library: /library, /library/file, upsert_title, local_poster_path.

These exercise the DB-direct code path (no TMDB / network), so the library
works with no internet.
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app import library as lib
from app.db import init_db
from app.main import app


def _seed(tmp_path: Path):
    """Create a fixture DB with a TV show (2 eps) and a movie, plus local files."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    media = tmp_path / "downloads" / "Test.Show.S01E01.mkv"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake video bytes")
    movie = tmp_path / "downloads" / "Great.Movie.2019.mkv"
    movie.write_bytes(b"fake movie bytes")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title, year, poster_path) "
        "VALUES (123, 'tv', 'Test Show', 2020, '/p/test.jpg')"
    )
    conn.execute(
        "INSERT INTO titles (tmdb_id, type, title, year, poster_path) "
        "VALUES (456, 'movie', 'Great Movie', 2019, '/p/movie.jpg')"
    )
    conn.execute(
        "INSERT INTO downloads (title_id, episode, hash, file_path, status) "
        "VALUES (123, 1, 'h1', ?, 'done')",
        (str(media),),
    )
    conn.execute(
        "INSERT INTO downloads (title_id, episode, hash, file_path, status) "
        "VALUES (456, NULL, 'h2', ?, 'done')",
        (str(movie),),
    )
    conn.execute(
        "INSERT INTO watch_progress (title_id, season, episode, position_ms, "
        "duration_ms, completed) VALUES (123, 1, 1, 5000, 60000, 0)"
    )
    conn.commit()
    conn.close()
    return db_path, media, movie


def test_library_lists_local_files(tmp_path):
    db_path, media, movie = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        resp = client.get("/library")
        assert resp.status_code == 200
        titles = {t["tmdb_id"]: t for t in resp.json()["titles"]}
        assert set(titles) == {123, 456}

        tv = titles[123]
        assert tv["title"] == "Test Show"
        assert tv["type"] == "tv"
        assert len(tv["episodes"]) == 1
        assert tv["episodes"][0]["file_path"] == str(media)
        assert tv["episodes"][0]["position_ms"] == 5000
        assert tv["episodes"][0]["completed"] is False
        assert tv["poster"] == "/tmdb/poster/123?size=w342"

        mov = titles[456]
        assert mov["title"] == "Great Movie"
        assert mov["type"] == "movie"
        assert mov["episodes"][0]["file_path"] == str(movie)


def test_library_file_resolves_path(tmp_path):
    db_path, media, movie = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        r = client.get("/library/file", params={"tmdb_id": 123, "episode": 1})
        assert r.status_code == 200
        assert r.json()["file_path"] == str(media)

        r = client.get("/library/file", params={"tmdb_id": 456})
        assert r.status_code == 200
        assert r.json()["file_path"] == str(movie)

        r = client.get("/library/file", params={"tmdb_id": 999, "episode": 1})
        assert r.status_code == 404


def test_watch_progress_current_episode(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)

        # TV show with a progress row -> returns the current episode.
        r = client.get("/watch/progress/123")
        assert r.status_code == 200
        body = r.json()
        assert body["season"] == 1
        assert body["episode"] == 1
        assert body["position_ms"] == 5000
        assert body["completed"] is False

        # Movie has no episode progress -> empty object.
        assert client.get("/watch/progress/456").json() == {}

        # Unknown title -> empty object.
        assert client.get("/watch/progress/999").json() == {}


def test_upsert_title_and_local_poster(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    data = {
        "_type": "movie",
        "title": "New Movie",
        "release_date": "2018-05-01",
        "poster_path": "/p/new.jpg",
    }
    id1 = lib.upsert_title(str(db_path), 789, data)
    id2 = lib.upsert_title(str(db_path), 789, data)
    assert id1 == id2  # idempotent
    assert lib.local_poster_path(str(db_path), 789) == "/p/new.jpg"

    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        by_id = {t["tmdb_id"]: t for t in client.get("/library").json()["titles"]}
        # 789 has metadata but no local file, so it must NOT appear in the
        # library (a viewed-but-not-downloaded title is not part of it).
        assert 789 not in by_id
        # The seeded titles (which have local files) still appear.
        assert 123 in by_id and 456 in by_id
