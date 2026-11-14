"""Per-title storage adapter: one JSON doc per title, swappable backend.

Used by the cloud-sync migration (P-M2+). The local peer writes through
LocalFolderBackend; a cloud peer (P-M2b) will use a Drive backend behind the
same StorageBackend interface.

Doc format: versioned JSON, one file per title.
Local path layout: <root>/movies/{tmdb_id}.json, <root>/shows/{tmdb_id}.json
"""

from __future__ import annotations

import abc
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DOC_VERSION = 1


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def make_key(t_type: str, tmdb_id: int) -> str:
    """Build a storage key: ``"movie:603"``."""
    return f"{t_type}:{tmdb_id}"


def parse_key(key: str) -> tuple[str, int]:
    """Split ``"movie:603"`` -> ``("movie", 603)``.

    Raises ValueError if the key has no colon or a non-integer id.
    """
    if ":" not in key:
        raise ValueError(f"malformed key (no ':'): {key!r}")
    t_type, _, id_str = key.partition(":")
    try:
        tmdb_id = int(id_str)
    except ValueError:
        raise ValueError(f"malformed key (non-integer id): {key!r}") from None
    return t_type, tmdb_id


# ---------------------------------------------------------------------------
# Doc helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """UTC timestamp in canonical ISO 8601 with 'Z' suffix.

    WHY "Z" (not space-separated, no offset): cloud docs use one canonical
    UTC format so LWW timestamp comparison (P-M3) is a plain lexicographic
    compare. The DB uses SQLite's datetime('now') (space, no Z); P-M3
    normalizes DB<->doc timestamps -- do not reconcile here.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_doc(
    *,
    tmdb_id: int,
    type: str,
    title: dict,
    watch: list[dict],
    device: str | None,
) -> dict:
    """Assemble a versioned per-title doc for storage."""
    return {
        "version": DOC_VERSION,
        "key": make_key(type, tmdb_id),
        "tmdb_id": tmdb_id,
        "type": type,
        "title": title,
        "watch": watch,
        "updated_at": now_iso(),
    }


def doc_is_valid(doc: dict) -> bool:
    """True iff *doc* is a dict with ``version == DOC_VERSION``."""
    return isinstance(doc, dict) and doc.get("version") == DOC_VERSION


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------

class StorageBackend(abc.ABC):
    """One per-title doc behind a swappable transport (local folder, Drive)."""

    @abc.abstractmethod
    def list_titles(self) -> list[str]:
        """Return all stored keys."""
        ...

    @abc.abstractmethod
    def read_title(self, key: str) -> dict | None:
        """Return the doc dict for *key*, or None if not present."""
        ...

    @abc.abstractmethod
    def write_title(self, key: str, data: dict) -> None:
        """Atomically persist *data* under *key*."""
        ...

    @abc.abstractmethod
    def delete_title(self, key: str) -> None:
        """Remove *key*; no-op if absent."""
        ...


# ---------------------------------------------------------------------------
# Local folder backend
# ---------------------------------------------------------------------------

class LocalFolderBackend(StorageBackend):
    """Per-title JSON on disk: ``root/movies/{id}.json``, ``root/shows/{id}.json``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        # Create both type subdirs up front so list_titles can glob safely.
        (self.root / "movies").mkdir(parents=True, exist_ok=True)
        (self.root / "shows").mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        t_type, tmdb_id = parse_key(key)
        subdir = "movies" if t_type == "movie" else "shows"
        return self.root / subdir / f"{tmdb_id}.json"

    def list_titles(self) -> list[str]:
        keys: list[str] = []
        for subdir, t_type in (("movies", "movie"), ("shows", "tv")):
            for p in sorted((self.root / subdir).glob("*.json")):
                keys.append(make_key(t_type, int(p.stem)))
        return keys

    def read_title(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_title(self, key: str, data: dict) -> None:
        # Atomic: write to .tmp then os.replace so a crash never leaves a
        # truncated title file.
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))

    def delete_title(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_backend(settings: dict) -> StorageBackend:
    """Pick a StorageBackend from app settings.

    ``settings["storage_backend"]`` selects the transport:
    - ``"local"`` (default) -> LocalFolderBackend
    - ``"drive"``           -> NotImplementedError (P-M2b follow-up)
    """
    backend_name = settings.get("storage_backend", "local")
    if backend_name == "local":
        root = settings.get("storage_local_root", "data/storage")
        return LocalFolderBackend(root)
    if backend_name == "drive":
        raise NotImplementedError("Drive backend is a follow-up run (P-M2b)")
    raise ValueError(f"unknown storage_backend: {backend_name!r}")
