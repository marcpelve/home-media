"""DriveBackend tests: a tiny in-memory Drive behind httpx.MockTransport
(no real API calls)."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from app.storage import DriveBackend, build_doc, create_backend

ROOT = "root"
TOKEN = "t"
FOLDER_MOVIES = "folder-movies"
FOLDER_SHOWS = "folder-shows"
FOLDER_MIME = "application/vnd.google-apps.folder"
BASE_URL = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE_URL = "https://www.googleapis.com/upload/drive/v3"


# ---------------------------------------------------------------------------
# In-memory Drive v3 stand-in
# ---------------------------------------------------------------------------

def _q_value(q: str, field: str) -> str:
    """Pull a quoted value out of a Drive ``q`` expression."""
    if field == "parents":
        m = re.search(r"'([^']*)' in parents", q)
    else:
        m = re.search(rf"{field}='([^']*)'", q)
    return m.group(1) if m else ""


def _parse_multipart(request: httpx.Request) -> dict[str, bytes]:
    """Split a multipart/form-data body into {field: bytes}."""
    ctype = request.headers.get("content-type", "")
    m = re.search(r"boundary=(\"[^\"]+\"|[^;]+)", ctype)
    boundary = m.group(1).strip().strip('"').encode()
    parts: dict[str, bytes] = {}
    for chunk in request.content.split(b"--" + boundary):
        chunk = chunk.lstrip(b"\r\n")
        if not chunk or chunk.startswith(b"--"):
            continue
        header_blob, _, content = chunk.partition(b"\r\n\r\n")
        name_m = re.search(rb'name="([^"]*)"', header_blob)
        if name_m:
            parts[name_m.group(1).decode()] = content.removesuffix(b"\r\n")
    return parts


class InMemoryDrive:
    """Drive v3 stand-in: a dict of files plus the two type subfolders."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self._seq = 0
        self.post_count = 0
        self.put_count = 0
        self.requests: list[tuple[str, str]] = []  # (method, raw path)
        # The root must contain both type subfolders.
        for fid, name in ((FOLDER_MOVIES, "movies"), (FOLDER_SHOWS, "shows")):
            self.files[fid] = {"name": name, "mimeType": FOLDER_MIME,
                               "parent": ROOT, "content": b""}

    def _next_id(self) -> str:
        self._seq += 1
        return f"file-{self._seq}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        # request.url.path includes the base_url path; strip it to get the
        # Drive API route ("/files", "/files/<id>"). Uploads arrive on the
        # upload/drive/v3 host, metadata on drive/v3.
        path = request.url.path
        for prefix in ("/upload/drive/v3", "/drive/v3"):
            if path.startswith(prefix):
                path = path.removeprefix(prefix)
                break
        method = request.method

        if method == "GET" and path == "/files":
            q = request.url.params.get("q", "")
            if FOLDER_MIME in q:
                # Subfolder lookup: match name + parent among folders.
                parent, name = _q_value(q, "parents"), _q_value(q, "name")
                files = [{"id": fid, "name": f["name"]}
                         for fid, f in self.files.items()
                         if f["parent"] == parent and f["name"] == name
                         and f["mimeType"] == FOLDER_MIME]
                return httpx.Response(200, json={"files": files})
            if "name='" in q:
                # File search: match name + parent.
                parent, name = _q_value(q, "parents"), _q_value(q, "name")
                files = [{"id": fid, "name": f["name"]}
                         for fid, f in self.files.items()
                         if f["parent"] == parent and f["name"] == name]
                return httpx.Response(200, json={"files": files})
            # List: files under a parent with mimeType application/json.
            parent = _q_value(q, "parents")
            files = [{"name": f["name"]} for f in self.files.values()
                     if f["parent"] == parent
                     and f["mimeType"] == "application/json"]
            return httpx.Response(200, json={"files": files})

        if method == "GET" and path.startswith("/files/"):
            fid = path.rsplit("/", 1)[1]
            f = self.files.get(fid)
            if f is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, content=f["content"])

        if method == "POST" and path == "/files":
            parts = _parse_multipart(request)
            meta = json.loads(parts["metadata"])
            fid = self._next_id()
            self.files[fid] = {"name": meta["name"],
                               "mimeType": meta["mimeType"],
                               "parent": meta["parents"][0],
                               "content": parts["media"]}
            self.post_count += 1
            return httpx.Response(200, json={"id": fid, "name": meta["name"]})

        if method == "PUT" and path.startswith("/files/"):
            fid = path.rsplit("/", 1)[1]
            f = self.files.get(fid)
            if f is None:
                return httpx.Response(404, json={"error": "not found"})
            parts = _parse_multipart(request)
            f["content"] = parts["media"]
            self.put_count += 1
            return httpx.Response(200, json={"id": fid, "name": f["name"]})

        if method == "DELETE" and path.startswith("/files/"):
            fid = path.rsplit("/", 1)[1]
            if fid not in self.files:
                return httpx.Response(404, json={"error": "not found"})
            del self.files[fid]
            return httpx.Response(200, json={})

        return httpx.Response(404, json={"error": "unrouted"})


@pytest.fixture
def drive():
    drv = InMemoryDrive()
    transport = httpx.MockTransport(drv.handler)
    client = httpx.Client(base_url=BASE_URL, transport=transport)
    upload_client = httpx.Client(base_url=UPLOAD_BASE_URL, transport=transport)
    backend = DriveBackend(TOKEN, ROOT, client=client,
                           upload_client=upload_client)
    yield {"backend": backend, "drv": drv, "client": client,
           "upload_client": upload_client}
    client.close()
    upload_client.close()


# ---------------------------------------------------------------------------
# DriveBackend
# ---------------------------------------------------------------------------

def test_drive_roundtrip_create_read(drive):
    backend, drv = drive["backend"], drive["drv"]
    doc = build_doc(tmdb_id=603, type="movie", title={"title": "Example"},
                    watch=[], device=None)
    backend.write_title("movie:603", doc)
    assert backend.read_title("movie:603") == doc
    # The file must live under the movies subfolder.
    stored = [f for f in drv.files.values() if f["name"] == "603.json"]
    assert len(stored) == 1
    assert stored[0]["parent"] == FOLDER_MOVIES


def test_drive_list_titles(drive):
    backend = drive["backend"]
    backend.write_title(
        "movie:603",
        build_doc(tmdb_id=603, type="movie", title={"title": "M"},
                  watch=[], device=None))
    backend.write_title(
        "tv:83923",
        build_doc(tmdb_id=83923, type="tv", title={"title": "T"},
                  watch=[], device=None))
    keys = set(backend.list_titles())
    assert "movie:603" in keys
    assert "tv:83923" in keys


def test_drive_update_existing(drive):
    backend, drv = drive["backend"], drive["drv"]
    doc1 = build_doc(tmdb_id=1, type="movie", title={"title": "A"},
                     watch=[], device=None)
    doc2 = build_doc(tmdb_id=1, type="movie", title={"title": "B"},
                     watch=[], device=None)
    backend.write_title("movie:1", doc1)
    backend.write_title("movie:1", doc2)
    assert drv.post_count == 1, "second write must be an UPDATE, not a create"
    assert drv.put_count == 1
    assert backend.read_title("movie:1") == doc2


def test_drive_read_missing(drive):
    assert drive["backend"].read_title("movie:42") is None


def test_drive_writes_use_upload_endpoint(drive):
    """Regression: file CONTENT must go to upload/drive/v3. The plain
    drive/v3 endpoint cannot parse a multipart body and answers
    'Parse Error' (this broke the first real sync)."""
    backend, drv = drive["backend"], drive["drv"]
    doc = build_doc(tmdb_id=603, type="movie", title={"title": "Example"},
                    watch=[], device=None)
    backend.write_title("movie:603", doc)          # create
    backend.write_title("movie:603", doc)          # update
    writes = [p for m, p in drv.requests if m in ("POST", "PUT")]
    assert writes, "expected create/update requests"
    assert all(p.startswith("/upload/drive/v3/") for p in writes), writes
    # Metadata traffic (searches, reads, deletes) stays on drive/v3.
    reads = [p for m, p in drv.requests if m == "GET"]
    assert all(p.startswith("/drive/v3/") for p in reads), reads


def test_drive_delete(drive):
    backend = drive["backend"]
    doc = build_doc(tmdb_id=603, type="movie", title={"title": "M"},
                    watch=[], device=None)
    backend.write_title("movie:603", doc)
    backend.delete_title("movie:603")
    assert backend.read_title("movie:603") is None
    assert "movie:603" not in backend.list_titles()


def test_drive_4xx_raises():
    drv = InMemoryDrive()

    def handler(request: httpx.Request) -> httpx.Response:
        # Any file search GET fails -> read_title must surface a RuntimeError.
        path = request.url.path.removeprefix("/drive/v3")
        if (request.method == "GET" and path == "/files"
                and "name='" in request.url.params.get("q", "")):
            return httpx.Response(500, content="boom")
        return drv.handler(request)

    client = httpx.Client(base_url=BASE_URL,
                          transport=httpx.MockTransport(handler))
    upload_client = httpx.Client(base_url=UPLOAD_BASE_URL,
                                 transport=httpx.MockTransport(handler))
    backend = DriveBackend(TOKEN, ROOT, client=client,
                           upload_client=upload_client)
    with pytest.raises(RuntimeError, match="Drive API 500"):
        backend.read_title("movie:603")
    client.close()
    upload_client.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_create_backend_drive():
    b = create_backend({"storage_backend": "drive",
                        "drive_access_token": TOKEN,
                        "drive_root_folder_id": ROOT})
    assert isinstance(b, DriveBackend)
    b.client.close()
    b.upload_client.close()
