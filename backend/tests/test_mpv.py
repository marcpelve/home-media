"""Unit tests for app.mpv using an in-memory transport (no real mpv binary).

The real pipe + real mpv binary path is covered by e2e_mpv.py; here the
transport seam is faked so command routing and lifecycle can be tested
deterministically.
"""

import json
import queue

import pytest

from app.mpv import MpvClient, MpvError


class InMemoryTransport:
    """Fake transport: a write produces a reply; read_line pops it."""

    def __init__(self, responder):
        self._responder = responder
        self._queue: queue.Queue = queue.Queue()

    def connect(self):
        pass

    def write(self, data: bytes):
        reply = self._responder(data)
        if reply is not None:
            self._queue.put(reply)

    def read_line(self, timeout: float) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        pass


def make_responder(seen):
    state = {"pause": False, "time-pos": 0.0, "duration": 12.0}

    def responder(raw):
        msg = json.loads(raw)
        cmd = msg.get("command", [])
        rid = msg.get("request_id")
        name = cmd[0]
        if name == "get_property":
            val = state.get(cmd[1], "property-not-found")
            reply = {"request_id": rid, "data": val, "error": "success"}
        elif name == "set_property":
            state[cmd[1]] = cmd[2]
            if cmd[1] == "time-pos":
                seen["pos"] = cmd[2]
            reply = {"request_id": rid, "error": "success"}
        elif name in ("loadfile", "quit"):
            reply = {"request_id": rid, "error": "success"}
        else:
            reply = {"request_id": rid, "error": "success"}
        return json.dumps(reply).encode() + b"\n"

    return responder


def _client():
    seen = {}
    client = MpvClient(transport=InMemoryTransport(make_responder(seen)))
    client.start(spawn=False)
    return client, seen


def test_get_property_round_trip():
    client, _ = _client()
    try:
        assert client.get_property("duration") == 12.0
        assert client.get_property("pause") is False
    finally:
        client.stop()


def test_set_position_delivers_value():
    client, seen = _client()
    try:
        client.set_position(4.5)
    finally:
        client.stop()
    assert seen.get("pos") == 4.5


def test_pause_resume():
    client, _ = _client()
    try:
        client.pause()
        assert client.get_property("pause") is True
        client.resume()
        assert client.get_property("pause") is False
    finally:
        client.stop()


def test_read_position_shape():
    client, _ = _client()
    try:
        pos = client.read_position()
    finally:
        client.stop()
    assert set(pos) == {"time_pos", "duration", "pause"}


def test_command_timeout_raises():
    # Responder that never replies -> command must time out.
    client = MpvClient(transport=InMemoryTransport(lambda raw: None))
    client.start(spawn=False)
    try:
        with pytest.raises(MpvError):
            client.command(["get_property", "duration"], timeout=0.3)
    finally:
        client.stop()
