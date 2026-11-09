"""Tests for app.main.do_trakt_sync, the body of the 30-minute sync loop."""

import asyncio

from app.main import app, do_trakt_sync


def test_do_trakt_sync_calls_sync_all(tmp_path, monkeypatch):
    seen = {}

    async def fake_sync(client, db):
        seen["call"] = (client, db)
        return {"pushed": 0, "pulled": 0, "error": None}

    monkeypatch.setattr("app.main.sync_all", fake_sync)
    app.state.trakt = object()
    app.state.db_path = str(tmp_path / "d.db")
    asyncio.run(do_trakt_sync(app))
    assert "call" in seen
    assert seen["call"][0] is app.state.trakt


def test_do_trakt_sync_swallows_errors(tmp_path, monkeypatch):
    async def boom(client, db):
        raise RuntimeError("no network")

    monkeypatch.setattr("app.main.sync_all", boom)
    app.state.trakt = object()
    app.state.db_path = str(tmp_path / "d.db")
    # must not raise: the local DB stays the source of truth
    asyncio.run(do_trakt_sync(app))
