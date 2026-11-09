"""Tests for app.routers.play: resume start-position passthrough to mpv."""

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app


class FakeMpv:
    """Records the calls the router makes; no real mpv."""

    def __init__(self):
        self.calls: list[tuple] = []
        self._pos = {"time_pos": 0.0, "duration": 60.0, "pause": False}

    def ensure_running(self):
        self.calls.append(("ensure_running",))

    def play(self, target):
        self.calls.append(("play", target))

    def set_position(self, seconds):
        self.calls.append(("set_position", seconds))

    def wait_loaded(self, timeout: float = 15.0):
        self.calls.append(("wait_loaded",))
        return True

    def read_position(self):
        return dict(self._pos)

    def is_alive(self):
        return True

    def stop(self):
        self.calls.append(("stop",))


def test_play_with_start_position_seeks():
    with TestClient(app) as client:
        fake = FakeMpv()
        app.state.mpv = fake
        r = client.post("/play", json={"url": "http://x/v.mkv", "start_seconds": 42})
        assert r.status_code == 200
        assert ("play", "http://x/v.mkv") in fake.calls
        assert ("set_position", 42) in fake.calls
        # A seek before the file loads is a no-op, so it must follow the wait.
        play_i = fake.calls.index(("play", "http://x/v.mkv"))
        load_i = fake.calls.index(("wait_loaded",))
        seek_i = fake.calls.index(("set_position", 42))
        assert play_i < load_i < seek_i


def test_play_without_start_position_does_not_seek():
    with TestClient(app) as client:
        fake = FakeMpv()
        app.state.mpv = fake
        r = client.post("/play", json={"url": "http://x/v.mkv"})
        assert r.status_code == 200
        assert ("play", "http://x/v.mkv") in fake.calls
        assert not any(c[0] == "set_position" for c in fake.calls)


def test_play_zero_start_position_does_not_seek():
    with TestClient(app) as client:
        fake = FakeMpv()
        app.state.mpv = fake
        r = client.post("/play", json={"url": "http://x/v.mkv", "start_seconds": 0})
        assert r.status_code == 200
        assert not any(c[0] == "set_position" for c in fake.calls)


def test_save_progress_completes_at_threshold(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with TestClient(app) as client:
        fake = FakeMpv()
        app.state.mpv = fake
        app.state.db_path = str(db_path)
        # 55s of 60s = 91.7% -> at/over the 90% fraction -> completed.
        fake._pos = {"time_pos": 55.0, "duration": 60.0, "pause": False}
        r = client.post("/play/progress", json={"title_id": 42})
        assert r.status_code == 200
        assert r.json()["completed"] == 1


def test_save_progress_not_complete_below_threshold(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with TestClient(app) as client:
        fake = FakeMpv()
        app.state.mpv = fake
        app.state.db_path = str(db_path)
        # 50s of 60s = 83.3% -> below the 90% fraction -> not completed.
        fake._pos = {"time_pos": 50.0, "duration": 60.0, "pause": False}
        r = client.post("/play/progress", json={"title_id": 42})
        assert r.status_code == 200
        assert r.json()["completed"] == 0


def test_save_progress_seek_back_keeps_completed(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with TestClient(app) as client:
        fake = FakeMpv()
        app.state.mpv = fake
        app.state.db_path = str(db_path)
        # Cross the threshold first -> completed.
        fake._pos = {"time_pos": 55.0, "duration": 60.0, "pause": False}
        assert client.post("/play/progress", json={"title_id": 42}).json()["completed"] == 1
        # Seek back below the threshold -> completed must stick.
        fake._pos = {"time_pos": 5.0, "duration": 60.0, "pause": False}
        r = client.post("/play/progress", json={"title_id": 42})
        assert r.status_code == 200
        assert r.json()["completed"] == 1
        assert r.json()["position_ms"] == 5000
