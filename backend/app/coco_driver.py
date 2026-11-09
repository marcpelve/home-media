"""Cocoscrapers driver: builds the data dict, calls all enabled sources,
aggregates results, dedupes by hash, and ranks by quality/seeders/size.

Synchronous (urllib-based); call from a thread via asyncio.to_thread().
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Make cocoscrapers importable as a top-level package before importing it.
# When packaged (PyInstaller) the vendored package is bundled as a top-level
# module in the extraction dir, so point there.
if getattr(sys, "frozen", False):
    _COCO_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    _COCO_DIR = str(Path(__file__).resolve().parent / "coco")
if _COCO_DIR not in sys.path:
    sys.path.insert(0, _COCO_DIR)

from app.coco.kodi_stub import install as _install_kodi_stub

_install_kodi_stub()

import cocoscrapers  # noqa: E402  (must follow kodi_stub install)

_QUALITY_RANK = {"4K": 5, "1080p": 4, "720p": 3, "SD": 2, "CAM": 1, "SCR": 0}

_SIZE_RE = re.compile(r"([\d.]+)\s*GB", re.I)


def build_movie_data(
    title: str,
    year: str,
    imdb: str,
    aliases: list[str] | None = None,
) -> dict:
    """Build the data dict Cocoscrapers expects for a movie."""
    return {
        "title": title,
        "year": year,
        "imdb": imdb,
        "aliases": aliases or [],
    }


def build_tv_data(
    show_title: str,
    year: str,
    imdb: str,
    season: int,
    episode: int,
    episode_title: str = "",
    aliases: list[str] | None = None,
) -> dict:
    """Build the data dict Cocoscrapers expects for a TV episode."""
    return {
        "tvshowtitle": show_title,
        "title": episode_title,
        "year": year,
        "imdb": imdb,
        "season": str(season),
        "episode": str(episode),
        "aliases": aliases or [],
    }


def _quality_rank(quality: str) -> int:
    return _QUALITY_RANK.get(quality, 0)


def _size_gb(size_str: str) -> float:
    m = _SIZE_RE.search(str(size_str or ""))
    return float(m.group(1)) if m else 0.0


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def scrape(data: dict) -> list[dict]:
    """Run all enabled Cocoscrapers sources and return a ranked source list.

    Returns [{hash, magnet, quality, size, seeders, provider}] sorted by
    (quality desc, seeders desc, size desc).
    """
    source_list = cocoscrapers.sources(ret_all=False)

    best_by_hash: dict[str, dict] = {}

    for name, source_class in source_list:
        try:
            instance = source_class()
            results = instance.sources(data, {})
        except Exception:
            continue

        for r in (results or []):
            if not isinstance(r, dict):
                continue
            h = r.get("hash", "")
            if not h:
                continue
            h = str(h).lower()  # hashes are case-insensitive; normalize for dedupe
            seeders = _as_int(r.get("seeders", 0))
            prev = best_by_hash.get(h)
            if prev is None or seeders > prev.get("seeders", 0):
                best_by_hash[h] = {
                    "hash": h,
                    "magnet": r.get("magnet", ""),
                    "quality": r.get("quality", "SD"),
                    "size": r.get("size", ""),
                    "seeders": seeders,
                    "provider": name,
                }

    ranked = list(best_by_hash.values())
    ranked.sort(
        key=lambda x: (
            _quality_rank(x["quality"]),
            x["seeders"],
            _size_gb(x["size"]),
        ),
        reverse=True,
    )
    return ranked
