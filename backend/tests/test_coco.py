"""Tests for kodi_stub, coco_driver, and the /sources router.

No real network calls: cocoscrapers.sources() is mocked at the driver level,
and the TMDB client is mocked at the router level.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# kodi_stub tests
# ---------------------------------------------------------------------------


class TestKodiStub:
    def test_install_creates_modules(self):
        from app.coco.kodi_stub import install
        install()
        assert "xbmc" in sys.modules
        assert "xbmcgui" in sys.modules
        assert "xbmcaddon" in sys.modules
        assert "xbmcvfs" in sys.modules

    def test_install_idempotent(self):
        from app.coco.kodi_stub import install
        install()
        xbmc_first = sys.modules["xbmc"]
        install()
        assert sys.modules["xbmc"] is xbmc_first

    def test_xbmc_sleep(self):
        import time
        from app.coco.kodi_stub import install
        install()
        xbmc = sys.modules["xbmc"]
        start = time.monotonic()
        xbmc.sleep(10)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.005

    def test_xbmc_log_no_error(self):
        from app.coco.kodi_stub import install
        install()
        xbmc = sys.modules["xbmc"]
        xbmc.log("test message", 1)  # should not raise

    def test_xbmc_getCondVisibility(self):
        from app.coco.kodi_stub import install
        install()
        xbmc = sys.modules["xbmc"]
        assert xbmc.getCondVisibility("System.Online") is False

    def test_xbmc_getInfoLabel(self):
        from app.coco.kodi_stub import install
        install()
        xbmc = sys.modules["xbmc"]
        assert xbmc.getInfoLabel("System.BuildVersion") == ""

    def test_xbmc_monitor(self):
        from app.coco.kodi_stub import install
        install()
        xbmc = sys.modules["xbmc"]
        m = xbmc.Monitor()
        assert m.abortRequested() is False

    def test_xbmcgui_window_properties(self):
        from app.coco.kodi_stub import install
        install()
        xbmcgui = sys.modules["xbmcgui"]
        w = xbmcgui.Window(10000)
        w.setProperty("test_key", "test_value")
        assert w.getProperty("test_key") == "test_value"
        w.clearProperty("test_key")
        assert w.getProperty("test_key") == ""

    def test_xbmcgui_window_has_settings(self):
        from app.coco.kodi_stub import install
        install()
        xbmcgui = sys.modules["xbmcgui"]
        w = xbmcgui.Window(10000)
        settings_json = w.getProperty("cocoscrapers_settings")
        settings = json.loads(settings_json)
        assert settings["debug.enabled"] == "false"
        assert settings["filter.undesirables"] == "true"
        assert settings["provider.torrentio"] == "true"

    def test_xbmcgui_window_singleton(self):
        from app.coco.kodi_stub import install
        install()
        xbmcgui = sys.modules["xbmcgui"]
        w1 = xbmcgui.Window(10000)
        w2 = xbmcgui.Window(10000)
        assert w1 is w2

    def test_xbmcgui_dialog_noop(self):
        from app.coco.kodi_stub import install
        install()
        xbmcgui = sys.modules["xbmcgui"]
        d = xbmcgui.Dialog()
        d.notification("title", "msg")  # should not raise
        assert d.yesno("heading", "line") is True
        assert d.select("heading", ["a", "b"]) == 0

    def test_xbmcgui_notification_constants(self):
        from app.coco.kodi_stub import install
        install()
        xbmcgui = sys.modules["xbmcgui"]
        assert xbmcgui.NOTIFICATION_INFO == 0
        assert xbmcgui.NOTIFICATION_WARNING == 1
        assert xbmcgui.NOTIFICATION_ERROR == 2

    def test_xbmcaddon_addon_info(self):
        from app.coco.kodi_stub import install
        install()
        xbmcaddon = sys.modules["xbmcaddon"]
        addon = xbmcaddon.Addon("script.module.cocoscrapers")
        assert addon.getAddonInfo("id") == "script.module.cocoscrapers"
        assert addon.getAddonInfo("name") == "CocoScrapers"
        assert addon.getAddonInfo("version") == "1.0.32"
        assert addon.getAddonInfo("path").endswith("cocoscrapers")
        assert addon.getAddonInfo("profile")

    def test_xbmcaddon_get_setting(self):
        from app.coco.kodi_stub import install
        install()
        xbmcaddon = sys.modules["xbmcaddon"]
        addon = xbmcaddon.Addon()
        assert addon.getSetting("provider.torrentio") == "true"
        assert addon.getSetting("debug.enabled") == "false"

    def test_xbmcvfs_translate_path(self):
        from app.coco.kodi_stub import install
        install()
        xbmcvfs = sys.modules["xbmcvfs"]
        # special://logpath/ resolves to a local dir
        result = xbmcvfs.translatePath("special://logpath/")
        assert result.endswith("logs")
        # special://profile/ resolves under data/coco
        result2 = xbmcvfs.translatePath("special://profile/subdir")
        assert "coco" in result2
        # plain paths pass through
        assert xbmcvfs.translatePath("/tmp/test") == "/tmp/test"

    def test_xbmcvfs_exists(self):
        from app.coco.kodi_stub import install
        install()
        import os
        xbmcvfs = sys.modules["xbmcvfs"]
        assert xbmcvfs.exists("/nonexistent/path/xyz") is False
        assert xbmcvfs.exists(__file__) is True

    def test_xbmcvfs_mkdirs(self, tmp_path):
        from app.coco.kodi_stub import install
        install()
        import os
        xbmcvfs = sys.modules["xbmcvfs"]
        target = str(tmp_path / "a" / "b" / "c")
        xbmcvfs.mkdirs(target)
        assert os.path.isdir(target)

    def test_xbmc_adds_coco_to_sys_path(self):
        from app.coco.kodi_stub import install
        install()
        coco_dir = str(Path(__file__).resolve().parent.parent / "app" / "coco")
        assert coco_dir in sys.path


# ---------------------------------------------------------------------------
# coco_driver tests
# ---------------------------------------------------------------------------


class TestCocoDriverBuildData:
    def test_build_movie_data(self):
        from app.coco_driver import build_movie_data
        data = build_movie_data("The Matrix", "1999", "tt0133093", ["Matrix"])
        assert data == {
            "title": "The Matrix",
            "year": "1999",
            "imdb": "tt0133093",
            "aliases": ["Matrix"],
        }

    def test_build_movie_data_no_aliases(self):
        from app.coco_driver import build_movie_data
        data = build_movie_data("Inception", "2010", "tt1375666")
        assert data["aliases"] == []

    def test_build_tv_data(self):
        from app.coco_driver import build_tv_data
        data = build_tv_data(
            show_title="Breaking Bad",
            year="2008",
            imdb="tt0903747",
            season=1,
            episode=1,
            episode_title="The Pilot",
            aliases=["BB"],
        )
        assert data == {
            "tvshowtitle": "Breaking Bad",
            "title": "The Pilot",
            "year": "2008",
            "imdb": "tt0903747",
            "season": "1",
            "episode": "1",
            "aliases": ["BB"],
        }

    def test_build_tv_data_no_episode_title(self):
        from app.coco_driver import build_tv_data
        data = build_tv_data("Show", "2020", "tt123", 2, 5)
        assert data["title"] == ""
        assert data["season"] == "2"
        assert data["episode"] == "5"


class TestCocoDriverScrape:
    """Test scrape() with a mocked cocoscrapers.sources()."""

    def _make_fake_sources(self):
        """Create fake source classes that return canned results."""
        class FakeSource1:
            def sources(self, data, hostDict):
                return [
                    {"hash": "abc123", "magnet": "magnet:?xt=urn:btih:abc123",
                     "quality": "1080p", "size": "1.50 GB", "seeders": 50,
                     "name": "Movie.1080p", "language": "en"},
                    {"hash": "def456", "magnet": "magnet:?xt=urn:btih:def456",
                     "quality": "720p", "size": "0.80 GB", "seeders": 30,
                     "name": "Movie.720p", "language": "en"},
                ]

        class FakeSource2:
            def sources(self, data, hostDict):
                return [
                    # Duplicate hash with more seeders - should win
                    {"hash": "abc123", "magnet": "magnet:?xt=urn:btih:abc123",
                     "quality": "1080p", "size": "1.50 GB", "seeders": 80,
                     "name": "Movie.1080p.v2", "language": "en"},
                    {"hash": "ghi789", "magnet": "magnet:?xt=urn:btih:ghi789",
                     "quality": "4K", "size": "4.20 GB", "seeders": 10,
                     "name": "Movie.4K", "language": "en"},
                ]

        return [("source1", FakeSource1), ("source2", FakeSource2)]

    def test_scrape_returns_ranked_results(self):
        from app.coco_driver import scrape, build_movie_data
        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=self._make_fake_sources()):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)

        assert len(results) == 2
        # 4K should be first
        assert results[0]["hash"] == "ghi789"
        assert results[0]["quality"] == "4K"
        # 1080p with 80 seeders (deduped winner) should be second
        assert results[1]["hash"] == "abc123"
        assert results[1]["seeders"] == 80
        # (def456, 720p, is filtered out: only 1080p+ with 10+ seeders remain)

    def test_scrape_dedupes_by_hash(self):
        from app.coco_driver import scrape, build_movie_data
        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=self._make_fake_sources()):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        hashes = [r["hash"] for r in results]
        assert len(hashes) == len(set(hashes))

    def test_scrape_keeps_highest_seeders_on_dup(self):
        from app.coco_driver import scrape, build_movie_data
        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=self._make_fake_sources()):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        abc = next(r for r in results if r["hash"] == "abc123")
        assert abc["seeders"] == 80

    def test_scrape_includes_provider_name(self):
        from app.coco_driver import scrape, build_movie_data
        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=self._make_fake_sources()):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        for r in results:
            assert "provider" in r
            assert r["provider"] in ("source1", "source2")

    def test_scrape_empty_sources(self):
        from app.coco_driver import scrape, build_movie_data
        with patch("app.coco_driver.cocoscrapers.sources", return_value=[]):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        assert results == []

    def test_scrape_source_exception_skipped(self):
        from app.coco_driver import scrape, build_movie_data

        class GoodSource:
            def sources(self, data, hostDict):
                return [{"hash": "h1", "magnet": "m", "quality": "1080p",
                         "size": "1 GB", "seeders": 50}]

        class BadSource:
            def sources(self, data, hostDict):
                raise RuntimeError("network error")

        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=[("good", GoodSource), ("bad", BadSource)]):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        assert len(results) == 1
        assert results[0]["hash"] == "h1"

    def test_scrape_quality_ranking_order(self):
        from app.coco_driver import scrape, build_movie_data

        class MultiSource:
            def sources(self, data, hostDict):
                return [
                    {"hash": "h_1080", "magnet": "m", "quality": "1080p",
                     "size": "1.5 GB", "seeders": 50},
                    {"hash": "h_4k", "magnet": "m", "quality": "4K",
                     "size": "5 GB", "seeders": 50},
                ]

        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=[("multi", MultiSource)]):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        # 4K ranks above 1080p (both pass the 1080p+ / 10+ seeder filters)
        assert [r["quality"] for r in results] == ["4K", "1080p"]

    def test_scrape_filters_sub_1080p(self):
        from app.coco_driver import scrape, build_movie_data

        class LowSource:
            def sources(self, data, hostDict):
                return [
                    {"hash": "h_720", "magnet": "m", "quality": "720p",
                     "size": "0.8 GB", "seeders": 100},
                    {"hash": "h_sd", "magnet": "m", "quality": "SD",
                     "size": "0.5 GB", "seeders": 100},
                    {"hash": "h_cam", "magnet": "m", "quality": "CAM",
                     "size": "0.3 GB", "seeders": 100},
                    {"hash": "h_1080", "magnet": "m", "quality": "1080p",
                     "size": "1.5 GB", "seeders": 50},
                ]

        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=[("low", LowSource)]):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        # 720p, SD, CAM are dropped; only 1080p+ remains.
        assert [r["quality"] for r in results] == ["1080p"]

    def test_scrape_filters_low_seeders(self):
        from app.coco_driver import scrape, build_movie_data

        class LowSeederSource:
            def sources(self, data, hostDict):
                return [
                    {"hash": "h_few", "magnet": "m", "quality": "1080p",
                     "size": "1.5 GB", "seeders": 5},
                    {"hash": "h_many", "magnet": "m", "quality": "1080p",
                     "size": "1.5 GB", "seeders": 25},
                ]

        with patch("app.coco_driver.cocoscrapers.sources",
                   return_value=[("seeders", LowSeederSource)]):
            data = build_movie_data("Movie", "2020", "tt123")
            results = scrape(data)
        # The 5-seeder source is dropped; only the 25-seeder one remains.
        assert [r["hash"] for r in results] == ["h_many"]


# ---------------------------------------------------------------------------
# /sources router tests
# ---------------------------------------------------------------------------


MOCK_TITLE_DETAILS = {
    "id": 550,
    "title": "Test Movie",
    "media_type": "movie",
    "release_date": "2020-05-15",
    "external_ids": {"imdb_id": "tt12345678"},
    "aliases": [{"title": "Alt Name", "country": "US"}],
}

MOCK_TV_DETAILS = {
    "id": 660,
    "name": "Test Show",
    "media_type": "tv",
    "first_air_date": "2019-03-01",
    "external_ids": {"imdb_id": "tt87654321"},
    "aliases": [],
}

FAKE_SCRAPE_RESULT = [
    {"hash": "abc", "magnet": "magnet:?xt=urn:btih:abc",
     "quality": "1080p", "size": "1.5 GB", "seeders": 42, "provider": "torrentio"},
]


@pytest.fixture
def sources_app(tmp_path):
    """FastAPI app with mocked TMDB client and mocked coco_driver.scrape."""
    from app.routers.sources import router as sources_router

    app = FastAPI()
    app.include_router(sources_router)

    mock_client = MagicMock()
    mock_client.title = MagicMock()

    async def _title(tmdb_id):
        if tmdb_id == 550:
            return MOCK_TITLE_DETAILS
        if tmdb_id == 660:
            return MOCK_TV_DETAILS
        raise ValueError("not found")

    mock_client.title = _title
    app.state.tmdb_client = mock_client

    return app


class TestSourcesRouter:
    def test_movie_sources(self, sources_app):
        with patch("app.routers.sources.scrape",
                   return_value=FAKE_SCRAPE_RESULT):
            client = TestClient(sources_app)
            resp = client.post("/sources/550", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["sources"][0]["hash"] == "abc"
        assert data["sources"][0]["quality"] == "1080p"

    def test_tv_sources(self, sources_app):
        with patch("app.routers.sources.scrape",
                   return_value=FAKE_SCRAPE_RESULT):
            client = TestClient(sources_app)
            resp = client.post("/sources/660",
                               json={"season": 1, "episode": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_title_not_found(self, sources_app):
        with patch("app.routers.sources.scrape",
                   return_value=[]):
            client = TestClient(sources_app)
            resp = client.post("/sources/9999", json={})
        assert resp.status_code == 404

    def test_no_imdb_id(self, sources_app):
        from app.routers.sources import router
        app = sources_app
        # Override with a title that has no imdb_id
        details_no_imdb = {"id": 777, "title": "No IMDb",
                           "media_type": "movie", "release_date": "2020-01-01",
                           "external_ids": {}, "aliases": []}

        async def _title_no_imdb(tmdb_id):
            return details_no_imdb

        app.state.tmdb_client.title = _title_no_imdb
        client = TestClient(app)
        resp = client.post("/sources/777", json={})
        assert resp.status_code == 400
        assert "IMDb" in resp.json()["detail"]

    def test_scrape_receives_correct_movie_data(self, sources_app):
        """Verify the data dict passed to scrape() has the right fields."""
        captured = {}

        def _capture_scrape(data):
            captured["data"] = data
            return FAKE_SCRAPE_RESULT

        with patch("app.routers.sources.scrape", side_effect=_capture_scrape):
            client = TestClient(sources_app)
            client.post("/sources/550", json={})

        assert captured["data"]["title"] == "Test Movie"
        assert captured["data"]["year"] == "2020"
        assert captured["data"]["imdb"] == "tt12345678"
        assert "Alt Name" in captured["data"]["aliases"]

    def test_scrape_receives_correct_tv_data(self, sources_app):
        captured = {}

        def _capture_scrape(data):
            captured["data"] = data
            return FAKE_SCRAPE_RESULT

        with patch("app.routers.sources.scrape", side_effect=_capture_scrape):
            client = TestClient(sources_app)
            client.post("/sources/660", json={"season": 3, "episode": 7})

        assert captured["data"]["tvshowtitle"] == "Test Show"
        assert captured["data"]["season"] == "3"
        assert captured["data"]["episode"] == "7"
        assert captured["data"]["imdb"] == "tt87654321"
