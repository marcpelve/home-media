"""Tests for the per-title storage adapter: key parsing, doc schema, LocalFolderBackend."""

import json
import re
from pathlib import Path

import pytest

from app.storage import (
    DOC_VERSION,
    DriveBackend,
    LocalFolderBackend,
    build_doc,
    create_backend,
    doc_is_valid,
    make_key,
    now_iso,
    parse_key,
)

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def test_make_parse_key_roundtrip():
    assert make_key("movie", 603) == "movie:603"
    assert parse_key("movie:603") == ("movie", 603)
    assert parse_key("tv:617") == ("tv", 617)


def test_parse_key_malformed():
    with pytest.raises(ValueError):
        parse_key("no-colon")
    with pytest.raises(ValueError):
        parse_key("movie:abc")
    with pytest.raises(ValueError):
        parse_key("movie:")


# ---------------------------------------------------------------------------
# Doc helpers
# ---------------------------------------------------------------------------


def test_build_doc_shape():
    title_row = {"tmdb_id": 603, "title": "The Matrix", "device": "desktop"}
    watch_rows = [
        {"season": None, "episode": None, "position_ms": 5000,
         "duration_ms": 100000, "completed": 0, "completed_at": None,
         "device": "desktop", "updated_at": "2026-11-13 04:00:00"},
    ]
    doc = build_doc(
        tmdb_id=603, type="movie", title=title_row, watch=watch_rows, device="desktop"
    )
    assert doc["version"] == DOC_VERSION
    assert doc["key"] == "movie:603"
    assert doc["tmdb_id"] == 603
    assert doc["type"] == "movie"
    # title and watch passed through unchanged
    assert doc["title"] == title_row
    assert doc["watch"] == watch_rows
    # updated_at is ISO 8601 UTC with Z suffix
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", doc["updated_at"])


def test_now_iso_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", now_iso())


def test_doc_is_valid():
    doc = build_doc(tmdb_id=1, type="movie", title={}, watch=[], device=None)
    assert doc_is_valid(doc) is True
    # wrong version
    bad = dict(doc, version=99)
    assert doc_is_valid(bad) is False
    # non-dict
    assert doc_is_valid("not a dict") is False  # type: ignore[arg-type]
    assert doc_is_valid(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LocalFolderBackend
# ---------------------------------------------------------------------------


def test_local_roundtrip(tmp_path: Path):
    be = LocalFolderBackend(tmp_path)
    doc = build_doc(tmdb_id=603, type="movie", title={"title": "The Matrix"},
                    watch=[], device="desktop")
    be.write_title("movie:603", doc)
    assert be.read_title("movie:603") == doc


def test_local_list_titles(tmp_path: Path):
    be = LocalFolderBackend(tmp_path)
    doc_m = build_doc(tmdb_id=603, type="movie", title={}, watch=[], device=None)
    doc_t = build_doc(tmdb_id=617, type="tv", title={}, watch=[], device=None)
    be.write_title("movie:603", doc_m)
    be.write_title("tv:617", doc_t)
    assert set(be.list_titles()) == {"movie:603", "tv:617"}


def test_local_read_missing(tmp_path: Path):
    be = LocalFolderBackend(tmp_path)
    assert be.read_title("movie:999") is None


def test_local_delete(tmp_path: Path):
    be = LocalFolderBackend(tmp_path)
    doc = build_doc(tmdb_id=603, type="movie", title={}, watch=[], device=None)
    be.write_title("movie:603", doc)
    be.delete_title("movie:603")
    assert be.read_title("movie:603") is None
    assert "movie:603" not in be.list_titles()
    # deleting a missing key is a no-op, not an error
    be.delete_title("movie:603")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_create_backend_local(tmp_path: Path):
    be = create_backend({"storage_backend": "local", "storage_local_root": str(tmp_path)})
    assert isinstance(be, LocalFolderBackend)
    # default root when key absent
    be2 = create_backend({})
    assert isinstance(be2, LocalFolderBackend)


def test_create_backend_drive():
    # P-M2b: "drive" now constructs a DriveBackend (token/root from settings).
    be = create_backend({"storage_backend": "drive",
                         "drive_access_token": "t",
                         "drive_root_folder_id": "root"})
    assert isinstance(be, DriveBackend)
    be.client.close()
