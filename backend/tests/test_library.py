"""Offline library: /library, /library/file, upsert_title, local_poster_path.

These exercise the DB-direct code path (no TMDB / network), so the library
works with no internet.
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app import library as lib
from app import watch
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


def test_watch_progress_specific_episode(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        # exact episode -> that row
        body = client.get(
            "/watch/progress/123", params={"season": 1, "episode": 1}
        ).json()
        assert body["season"] == 1 and body["episode"] == 1
        assert body["position_ms"] == 5000
        # a missing episode -> empty object
        assert client.get(
            "/watch/progress/123", params={"season": 1, "episode": 2}
        ).json() == {}
        # season without episode -> falls back to current-episode (S01E01)
        body = client.get("/watch/progress/123", params={"season": 1}).json()
        assert body["episode"] == 1


def test_upsert_progress_completed_is_sticky(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    # Crossing the threshold marks it complete.
    watch.upsert_progress(
        str(db_path), 456, position_ms=54000, duration_ms=60000, completed=True
    )
    # A later save below the threshold (seek-back) must not un-complete it.
    watch.upsert_progress(
        str(db_path), 456, position_ms=5000, duration_ms=60000, completed=False
    )
    row = watch.get_progress(str(db_path), 456)
    assert row is not None
    assert row["completed"] == 1
    assert row["position_ms"] == 5000  # position updated, completed preserved


def test_watch_complete_movie(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        r = client.post("/watch/complete", json={"title_id": 456})
        assert r.status_code == 200
        assert r.json()["completed"] == 1
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT completed FROM watch_progress "
            "WHERE title_id = 456 AND season IS NULL AND episode IS NULL"
        ).fetchone()
        conn.close()
        assert row is not None and row[0] == 1


def test_watch_complete_episode_preserves_position(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        r = client.post(
            "/watch/complete", json={"title_id": 123, "season": 1, "episode": 1}
        )
        assert r.status_code == 200
        body = client.get(
            "/watch/progress/123", params={"season": 1, "episode": 1}
        ).json()
        assert body["completed"] is True
        assert body["position_ms"] == 5000  # saved position preserved


def test_watch_complete_whole_season(tmp_path):
    db_path, _, _ = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        r = client.post(
            "/watch/complete",
            json={
                "title_id": 123,
                "season": 1,
                "whole_season": True,
                "episode_count": 3,
            },
        )
        assert r.status_code == 200
        assert r.json()["completed"] == 3
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT episode, completed, position_ms FROM watch_progress "
            "WHERE title_id = 123 AND season = 1 ORDER BY episode"
        ).fetchall()
        conn.close()
        assert [r[0] for r in rows] == [1, 2, 3]
        assert all(r[1] == 1 for r in rows)
        # ep 1 kept its saved position; eps 2-3 are fresh 0/0 rows
        assert rows[0][2] == 5000
        assert rows[1][2] == 0 and rows[2][2] == 0


def test_delete_library_title(tmp_path):
    from app.downloads import DownloadManager

    db_path, media, movie = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        app.state.downloads = DownloadManager(db_path, dest_dir=tmp_path / "downloads")
        r = client.delete("/library/123")
        assert r.status_code == 200
        assert r.json()["removed"] == 1
        by_id = {t["tmdb_id"] for t in client.get("/library").json()["titles"]}
        assert by_id == {456}, "the TV show is gone; the movie remains"
        assert not media.exists(), "the downloaded file is deleted"
        assert movie.exists(), "the untouched file is kept"


def test_delete_library_episode(tmp_path):
    from app.downloads import DownloadManager

    db_path, media, movie = _seed(tmp_path)
    with TestClient(app) as client:
        app.state.db_path = str(db_path)
        app.state.downloads = DownloadManager(db_path, dest_dir=tmp_path / "downloads")
        r = client.delete("/library/123/episodes/1")
        assert r.status_code == 200
        assert r.json()["removed"] == 1
        # 123 had a single episode, so removing it drops the whole title.
        by_id = {t["tmdb_id"] for t in client.get("/library").json()["titles"]}
        assert by_id == {456}
        assert not media.exists()
        assert movie.exists()


def test_watch_list_groups_by_show(tmp_path):
    """Continue Watching: one card per show; resume = first unwatched episode;
    fully-watched titles drop out."""
    db_path = tmp_path / "cw.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    # Show 1: two episodes in progress -> one card at S01E01 (the first).
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (1, 1, 1, 5000, 60000, 0)")
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (1, 1, 2, 10000, 60000, 0)")
    # Show 2: all episodes complete -> excluded.
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (2, 1, 1, 60000, 60000, 1)")
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (2, 1, 2, 60000, 60000, 1)")
    # Show 3: E1 complete, E2 in progress -> one card at S01E02.
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (3, 1, 1, 60000, 60000, 1)")
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (3, 1, 2, 7000, 60000, 0)")
    # Movie 4: in progress -> one card.
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (4, NULL, NULL, 12000, 90000, 0)")
    # Movie 5: complete -> excluded.
    conn.execute("INSERT INTO watch_progress (title_id, season, episode, position_ms, duration_ms, completed) VALUES (5, NULL, NULL, 90000, 90000, 1)")
    conn.commit()
    conn.close()

    class MockTMDB:
        async def title(self, tmdb_id):
            return {"title": f"Title {tmdb_id}", "_type": "movie" if tmdb_id >= 4 else "tv"}

    with TestClient(app) as client:
        # Set state inside the context: the app lifespan (entered here) resets
        # db_path and tmdb_client, so this must run after it.
        app.state.db_path = str(db_path)
        app.state.tmdb_client = MockTMDB()
        items = client.get("/watch").json()["items"]
    by_id = {it["title_id"]: it for it in items}
    # Only the not-finished titles appear.
    assert set(by_id) == {1, 3, 4}
    # Show 1 -> one card, resume at the first (lowest) unwatched episode S01E01.
    assert by_id[1]["season"] == 1 and by_id[1]["episode"] == 1
    # Show 3 -> resume at S01E02 (E1 is already complete).
    assert by_id[3]["season"] == 1 and by_id[3]["episode"] == 2
    # Movie 4 -> its saved position is carried through.
    assert by_id[4]["position_ms"] == 12000
