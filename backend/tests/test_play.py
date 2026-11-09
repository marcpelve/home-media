"""Tests for app.routers.play: resume start-position passthrough to mpv."""

from fastapi.testclient import TestClient

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
