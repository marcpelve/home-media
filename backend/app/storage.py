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
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

DOC_VERSION = 1

# ---------------------------------------------------------------------------
# Per-title writer locks (process-wide)
# ---------------------------------------------------------------------------

# A debounced progress push and a manual sync each use their OWN
# DriveBackend instance (the factory builds a fresh one for token
# freshness), so instance-level state — e.g. the listing cache — cannot
# serialize them. Drive's index is eventually consistent, so an
# interleaved replace (find -> delete -> create) can resolve an id that a
# concurrent writer already deleted (404) and leave two same-name files
# behind. A process-wide lock per storage key serializes the whole
# replace for that title.
_WRITER_LOCKS: dict[str, threading.Lock] = {}
_WRITER_LOCKS_GUARD = threading.Lock()


def _writer_lock(key: str) -> threading.Lock:
    """Per-key writer lock shared across all backend instances."""
    with _WRITER_LOCKS_GUARD:
        return _WRITER_LOCKS.setdefault(key, threading.Lock())


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
# Drive backend (Google Drive API v3)
# ---------------------------------------------------------------------------

class DriveBackend(StorageBackend):
    """Per-title JSON on Google Drive, same layout as LocalFolderBackend:
    a root folder containing ``movies/`` and ``shows/`` subfolders, one
    JSON file per title (``<tmdb_id>.json``).

    The root folder MUST contain both subfolders; a missing subfolder
    raises RuntimeError on first use.

    The backend takes a ready access token (the OAuth connect/refresh
    flow is P-M4) and sends ``Authorization: Bearer <token>`` on every
    request.
    """

    def __init__(self, access_token: str, root_folder_id: str,
                 *, base_url: str = "https://www.googleapis.com/drive/v3",
                 upload_base_url: str = "https://www.googleapis.com/upload/drive/v3",
                 client: httpx.Client | None = None,
                 upload_client: httpx.Client | None = None):
        self.access_token = access_token
        self.root_folder_id = root_folder_id
        self.client = client if client is not None else httpx.Client(base_url=base_url)
        # File CONTENT goes to the upload endpoint (upload/drive/v3); the
        # plain drive/v3 endpoint only does metadata and cannot parse a
        # multipart body (it answers "Parse Error").
        self.upload_client = (
            upload_client if upload_client is not None
            else httpx.Client(base_url=upload_base_url)
        )
        # Folder ids are stable for the app's lifetime; caching them spares
        # a lookup request on every title operation.
        self._folders: dict[str, str] = {}
        # Per-subfolder file listing (subfolder id -> {name: file dict}),
        # cached so a sync pass lists each subfolder once instead of running
        # a Drive search per title. Guarded by _files_lock: parallel sync
        # workers may race the first listing of a subfolder.
        self._files_cache: dict[str, dict[str, dict]] = {}
        self._files_lock = threading.Lock()

    # -- transport helpers --------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _check(self, resp: httpx.Response) -> None:
        # Surface 4xx/5xx loudly instead of swallowing: P-M4 reports sync
        # status from this exception.
        if resp.status_code >= 400:
            raise RuntimeError(f"Drive API {resp.status_code}: {resp.text[:200]}")

    def _get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        resp = self.client.get(path, params=params, headers=self._headers())
        self._check(resp)
        return resp

    # -- folder + file lookups ----------------------------------------------

    def _subfolder(self, name: str) -> str:
        """Resolve (and cache) the id of the root's ``name`` subfolder."""
        cached = self._folders.get(name)
        if cached is not None:
            return cached
        q = (f"'{self.root_folder_id}' in parents and "
             f"mimeType='application/vnd.google-apps.folder' and name='{name}'")
        resp = self._get("/files", params={"q": q, "fields": "files(id,name)"})
        files = resp.json().get("files", [])
        if not files:
            raise RuntimeError(
                f"Drive root {self.root_folder_id!r} has no {name!r} subfolder")
        self._folders[name] = files[0]["id"]
        return self._folders[name]

    @staticmethod
    def _subdir_for(t_type: str) -> str:
        return "movies" if t_type == "movie" else "shows"

    def _list_subfolder(self, subfolder_id: str) -> dict[str, dict]:
        """The subfolder's files as ``{name: {"id", "name"}}``, cached.

        One listing request per subfolder per backend instance: every
        per-title operation resolves its file from this map instead of a
        per-call Drive search. Double-checked under the lock so parallel
        sync workers don't race a redundant first listing.
        """
        cached = self._files_cache.get(subfolder_id)
        if cached is not None:
            return cached
        with self._files_lock:
            cached = self._files_cache.get(subfolder_id)
            if cached is not None:
                return cached
            files: dict[str, dict] = {}
            page_token: str | None = None
            while True:
                q = (f"'{subfolder_id}' in parents and "
                     f"mimeType='application/json' and trashed=false")
                params: dict = {"q": q,
                                "fields": "files(id,name),nextPageToken"}
                if page_token:
                    params["pageToken"] = page_token
                resp = self._get("/files", params=params)
                data = resp.json()
                for f in data.get("files", []):
                    files[f["name"]] = {"id": f["id"], "name": f["name"]}
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            self._files_cache[subfolder_id] = files
            return files

    def _find_file(self, subfolder_id: str, stem: str) -> dict | None:
        # Resolve from the cached folder listing (one request per subfolder
        # per instance) instead of a per-call Drive search.
        return self._list_subfolder(subfolder_id).get(stem + ".json")

    # -- StorageBackend -------------------------------------------------------

    def list_titles(self) -> list[str]:
        keys: list[str] = []
        for subdir, t_type in (("movies", "movie"), ("shows", "tv")):
            fid = self._subfolder(subdir)
            # Reuse the cached listing: no extra requests beyond the one
            # _list_subfolder issues per subfolder.
            for f in self._list_subfolder(fid).values():
                stem = f["name"].rsplit(".", 1)[0]
                try:
                    keys.append(make_key(t_type, int(stem)))
                except ValueError:
                    continue
        return keys

    def read_title(self, key: str) -> dict | None:
        t_type, tmdb_id = parse_key(key)
        fid = self._subfolder(self._subdir_for(t_type))
        found = self._find_file(fid, str(tmdb_id))
        if found is None:
            return None
        resp = self._get(f"/files/{found['id']}", params={"alt": "media"})
        return json.loads(resp.content)

    def write_title(self, key: str, data: dict) -> None:
        t_type, tmdb_id = parse_key(key)
        fid = self._subfolder(self._subdir_for(t_type))
        name = f"{tmdb_id}.json"
        content = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        meta: dict = {"name": name, "mimeType": "application/json",
                      "parents": [fid]}
        # Serialize the whole replace (find + delete + create) per title:
        # a concurrent writer on a separate backend instance (progress
        # push vs manual sync) could otherwise delete the id we resolved,
        # 404 on ours, and leave two same-name files behind.
        with _writer_lock(key):
            found = self._find_file(fid, str(tmdb_id))
            # Invalidate the cached listing BEFORE mutating: replace-on-
            # update (delete + create) changes the file id, so a stale
            # entry would 404 on the next read; and if the create fails,
            # a re-list self-heals.
            self._files_cache.pop(fid, None)
            if found is not None:
                # Update = replace. In-place content updates (PUT
                # /files/{id}) are unroutable on this network (HTML 404),
                # and the sync pushes every local title each run, so
                # deleting the old file before creating the new one is
                # self-healing. Lookup is name-based, so the id swap is
                # invisible.
                try:
                    self._delete_file(found["id"])
                except RuntimeError as e:
                    # 404 = the old version is already gone (a concurrent
                    # writer, or an out-of-band delete, removed it) —
                    # which is the state we wanted after the delete.
                    # Anything else is a real failure.
                    if "404" not in str(e):
                        raise
            self._resumable_create(meta, content)

    def _delete_file(self, fid: str) -> None:
        resp = self.client.delete(f"/files/{fid}", headers=self._headers())
        self._check(resp)

    def _resumable_create(self, meta: dict, content: bytes) -> None:
        """Create a file via the resumable protocol: POST the metadata to
        open a session, then PUT the raw media to the session URL from the
        ``Location`` header.

        Multipart/related uploads were silently dropping the media part on
        this network (file created, size 0), so uploads use the resumable
        protocol -- the same one the official clients use for big files.
        """
        headers = self._headers()
        resp = self.upload_client.post("/files",
                                       params={"uploadType": "resumable"},
                                       headers=headers, json=meta)
        self._check(resp)
        session = resp.headers.get("Location")
        if not session:
            raise RuntimeError("Drive upload: response missing Location")
        resp = self.upload_client.put(session, headers=headers,
                                      content=content)
        self._check(resp)

    def delete_title(self, key: str) -> None:
        t_type, tmdb_id = parse_key(key)
        fid = self._subfolder(self._subdir_for(t_type))
        found = self._find_file(fid, str(tmdb_id))
        if found is None:
            return
        # Invalidate the cached listing before deleting: a concurrent read
        # must re-list and see the file gone, not the stale id.
        self._files_cache.pop(fid, None)
        resp = self.client.delete(f"/files/{found['id']}",
                                  headers=self._headers())
        self._check(resp)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_backend(settings: dict) -> StorageBackend:
    """Pick a StorageBackend from app settings.

    ``settings["storage_backend"]`` selects the transport:
    - ``"local"`` (default) -> LocalFolderBackend
    - ``"drive"``           -> DriveBackend (needs ``drive_access_token``
      and ``drive_root_folder_id``; OAuth acquisition is P-M4)
    """
    backend_name = settings.get("storage_backend", "local")
    if backend_name == "local":
        root = settings.get("storage_local_root", "data/storage")
        return LocalFolderBackend(root)
    if backend_name == "drive":
        return DriveBackend(settings["drive_access_token"],
                            settings["drive_root_folder_id"])
    raise ValueError(f"unknown storage_backend: {backend_name!r}")
