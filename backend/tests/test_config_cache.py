"""Test the /config cache helpers and endpoints (size readout + clear)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.routers.config import _clear_dir, _dir_bytes, clear_cache, config


def _fake_request(poster_dir: str, download_dir: str):
    app = SimpleNamespace(
        state=SimpleNamespace(poster_dir=poster_dir, download_dir=download_dir)
    )
    return SimpleNamespace(app=app)


def test_dir_bytes_sums_files_recursively(tmp_path: Path):
    root = tmp_path / "posters"
    (root / "sub").mkdir(parents=True)
    (root / "a.jpg").write_bytes(b"x" * 100)
    (root / "sub" / "b.jpg").write_bytes(b"y" * 50)
    assert _dir_bytes(root) == 150


def test_dir_bytes_zero_for_missing_dir(tmp_path: Path):
    assert _dir_bytes(tmp_path / "nope") == 0


def test_clear_dir_removes_files_keeps_dir(tmp_path: Path):
    root = tmp_path / "posters"
    (root / "sub").mkdir(parents=True)
    (root / "a.jpg").write_bytes(b"x" * 100)
    (root / "sub" / "b.jpg").write_bytes(b"y" * 50)
    _clear_dir(root)
    assert _dir_bytes(root) == 0
    assert root.exists()  # the directory itself is kept


def test_get_config_reports_size(tmp_path: Path):
    poster = tmp_path / "posters"
    poster.mkdir()
    (poster / "a.jpg").write_bytes(b"x" * 100)
    req = _fake_request(str(poster), str(tmp_path / "downloads"))
    out = asyncio.run(config(req))  # type: ignore[arg-type]
    assert out["cache_bytes"] == 100
    assert out["poster_dir"] == str(poster)
    assert out["download_dir"] == str(tmp_path / "downloads")


def test_clear_cache_endpoint_wipes_posters(tmp_path: Path):
    poster = tmp_path / "posters"
    poster.mkdir()
    (poster / "a.jpg").write_bytes(b"x" * 100)
    (poster / "b.jpg").write_bytes(b"y" * 50)
    req = _fake_request(str(poster), str(tmp_path / "downloads"))
    out = asyncio.run(clear_cache(req))  # type: ignore[arg-type]
    assert out["cache_bytes"] == 0
    assert poster.exists() and not any(p.is_file() for p in poster.rglob("*"))
