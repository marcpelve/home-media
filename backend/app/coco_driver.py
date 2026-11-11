"""Cocoscrapers driver: builds the data dict, calls all enabled sources,
aggregates results, dedupes by hash, and ranks by quality/seeders/size.

Synchronous (urllib-based); call from a thread via asyncio.to_thread().
"""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
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


def _parse_capabilities(name_info: str, name: str) -> list[str]:
    """Extract Kodi/umbrella-style capability tags from a release name.

    `name_info` is Cocoscrapers' cleaned, dot-separated, lowercase form
    (e.g. ".2160p.bluray.x265.10bit.sdr.dts.hd.ma.5.1"); fall back to the raw
    `name` when it is absent. Tags are returned in a fixed display order that
    mirrors the ranking priority - HDR and audio lead the row, with the codec
    and source/container tags kept near the end, so a 4K BluRay DV/HDR/Atmos
    release reads "Dolby Vision HDR 10-bit Atmos HEVC BluRay" rather than
    "BluRay ... Atmos HEVC".
    """
    raw = (name_info or name or "").lower()
    tokens = set(re.split(r"[^a-z0-9]+", raw))
    tags: list[str] = []
    # HDR
    # "Dolby Vision" appears as the fused/abbrev token in cleaned names and as
    # the two tokens "dolby" + "vision" in raw dotted names - catch both.
    if tokens & {"dovi", "dv", "dvhdr", "dolbyvision"} or ({"dolby", "vision"} <= tokens):
        tags.append("Dolby Vision")
    if tokens & {"hdr10", "hdr10plus"}:
        tags.append("HDR10")
    elif "hdr" in tokens:
        tags.append("HDR")
    if "hlg" in tokens:
        tags.append("HLG")
    # bit depth
    if "10bit" in tokens:
        tags.append("10-bit")
    # audio
    if "atmos" in tokens:
        tags.append("Atmos")
    if "truehd" in tokens:
        tags.append("TrueHD")
    if "ddp" in tokens:
        tags.append("DD+")
    elif "dd" in tokens:
        tags.append("DD")
    if "dts" in tokens:
        tags.append("DTS")
    # codec (kept near the end, just before the source/container tag)
    if tokens & {"x265", "hevc"}:
        tags.append("HEVC")
    elif tokens & {"x264", "h264"}:
        tags.append("H.264")
    elif "av1" in tokens:
        tags.append("AV1")
    # source / container (kept last so the HDR/audio tags that drive the sort
    # lead the row)
    if tokens & {"bluray", "brrip", "bdrip"}:
        tags.append("BluRay")
    elif "remux" in tokens:
        tags.append("Remux")
    elif tokens & {"web", "webdl", "webm"}:
        tags.append("Web")
    elif "hdtv" in tokens:
        tags.append("HDTV")
    return tags


def _cap_tier(tags):
    """Sort rank for a source's HDR capability: Dolby Vision (0) beats any
    other HDR flavor (1), which beats no HDR (2). Used to order DV/HDR
    releases ahead of the rest within each quality tier."""
    if "Dolby Vision" in tags:
        return 0
    if {"HDR", "HDR10", "HLG"} & set(tags):
        return 1
    return 2


def _size_gb(size_str: str) -> float:
    m = _SIZE_RE.search(str(size_str or ""))
    return float(m.group(1)) if m else 0.0


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _scrape_source(name: str, source_class, data: dict) -> list[tuple[str, dict]]:
    """Run one scraper in a worker thread; return (provider, raw) pairs.

    Each scraper is a blocking urllib call, so running them in a thread pool
    turns the sum of their latencies into the max.
    """
    try:
        instance = source_class()
        results = instance.sources(data, {})
    except Exception:
        return []
    return [(name, r) for r in (results or []) if isinstance(r, dict) and r.get("hash")]


# Minimum quality rank to surface (1080p and up). Lower tiers (720p, SD, CAM,
# SCR) are dropped - sub-1080p content is never wanted.
MIN_QUALITY_RANK = _QUALITY_RANK["1080p"]
# Minimum seeders to surface. Torrents with fewer are dropped as unreliable.
MIN_SEEDERS = 10


def scrape(data: dict) -> list[dict]:
    """Run all enabled Cocoscrapers sources and return a ranked source list.

    Sources are scraped concurrently (thread pool) and filtered to
    MIN_QUALITY_RANK (1080p+) and MIN_SEEDERS. Returns [{hash, magnet, quality,
    size, seeders, provider, name, tags}] sorted by (quality desc, then
    Dolby Vision > HDR > no-HDR, then seeders desc, size desc). `tags` are
    capability labels parsed from the release name (Dolby Vision, HDR10, HEVC,
    BluRay, ...).
    """
    source_list = cocoscrapers.sources(ret_all=False)

    best_by_hash: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(source_list))) as pool:
        futures = [pool.submit(_scrape_source, name, cls, data) for name, cls in source_list]
        for fut in futures:
            for name, r in fut.result():
                h = str(r.get("hash", "")).lower()  # normalize for dedupe
                seeders = _as_int(r.get("seeders", 0))
                prev = best_by_hash.get(h)
                if prev is None or seeders > prev.get("seeders", 0):
                    best_by_hash[h] = {
                        "hash": h,
                        "magnet": r.get("magnet") or r.get("url", ""),
                        "quality": r.get("quality", "SD"),
                        "size": r.get("size", ""),
                        "seeders": seeders,
                        "provider": name,
                        "name": r.get("name", ""),
                        "tags": _parse_capabilities(r.get("name_info", ""), r.get("name", "")),
                    }

    ranked = [
        s for s in best_by_hash.values()
        if _quality_rank(s["quality"]) >= MIN_QUALITY_RANK and s["seeders"] >= MIN_SEEDERS
    ]
    # Quality (4K before 1080p) is primary; within each tier, Dolby Vision
    # beats HDR beats no-HDR, then seeders, then size.
    ranked.sort(
        key=lambda x: (
            -_quality_rank(x["quality"]),
            _cap_tier(x["tags"]),
            -x["seeders"],
            -_size_gb(x["size"]),
        )
    )
    return ranked
