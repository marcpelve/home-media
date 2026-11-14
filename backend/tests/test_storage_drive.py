"""DriveBackend tests: a tiny in-memory Drive behind httpx.MockTransport
(no real API calls)."""

from __future__ import annotations

import json
import re
import threading

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


class InMemoryDrive:
    """Drive v3 stand-in: a dict of files plus the two type subfolders."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self.post_count = 0
        self.put_count = 0
        self.requests: list[tuple[str, str]] = []  # (method, raw path)
        # The root must contain both type subfolders.
        for fid, name in ((FOLDER_MOVIES, "movies"), (FOLDER_SHOWS, "shows")):
            self.files[fid] = {"name": name, "mimeType": FOLDER_MIME,
                               "parent": ROOT, "content": b""}

    def _next_id(self) -> str:
        # Called only from _handle, which already holds self._lock.
        self._seq += 1
        return f"file-{self._seq}"

    def _session(self, fid: str) -> httpx.Response:
        """Open a resumable session, mirroring Drive's Location header."""
        return httpx.Response(
            200, json={"id": fid, "name": self.files[fid]["name"]},
            headers={"Location": f"{UPLOAD_BASE_URL}/files"
                     f"?uploadType=resumable&upload_id={fid}"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        # The whole handler runs under the lock: every branch reads and
        # mutates the same dicts, and the race test drives it from
        # several threads at once.
        with self._lock:
            return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
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
        params = dict(request.url.params)

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
            files = [{"id": fid, "name": f["name"]} for fid, f
                     in self.files.items()
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
            # Open a create session: metadata JSON body, no media yet.
            meta = json.loads(request.content)
            fid = self._next_id()
            self.files[fid] = {"name": meta["name"],
                               "mimeType": meta["mimeType"],
                               "parent": meta["parents"][0],
                               "content": b""}
            self.post_count += 1
            return self._session(fid)

        if method == "PUT" and path == "/files" and "upload_id" in params:
            # Complete the upload: raw media body.
            fid = params["upload_id"]
            if fid not in self.files:
                return httpx.Response(404, json={"error": "not found"})
            self.files[fid]["content"] = request.content
            self.put_count += 1
            return httpx.Response(200, json={"id": fid,
                                             "name": self.files[fid]["name"]})

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
           "upload_client": upload_client, "transport": transport}
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
    # Update = replace: the second write deletes the old file and creates a
    # new one (in-place content PUTs are unroutable on this network).
    assert drv.put_count == 2, "one completed upload per write"
    stored = [f for f in drv.files.values() if f["name"] == "1.json"]
    assert len(stored) == 1, "replace must leave exactly one file"
    assert backend.read_title("movie:1") == doc2


def test_drive_read_missing(drive):
    assert drive["backend"].read_title("movie:42") is None


def test_drive_writes_use_upload_endpoint(drive):
    """Regression: file CONTENT must go to the upload endpoint
    (upload/drive/v3) via the resumable protocol. The plain drive/v3
    endpoint only does metadata (a multipart POST there answers
    'Parse Error'), and multipart bodies to the upload endpoint
    dropped the media part on this network."""
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


def _listing_count(drv: InMemoryDrive) -> int:
    """Number of metadata file-listing requests (GET /files) so far."""
    return sum(1 for m, p in drv.requests
               if m == "GET" and p == "/drive/v3/files")


def test_drive_listing_cached_between_reads(drive):
    """A second read_title for the same key is a cache hit: it must not
    issue another folder-listing request."""
    backend, drv = drive["backend"], drive["drv"]
    doc = build_doc(tmdb_id=603, type="movie", title={"title": "M"},
                    watch=[], device=None)
    backend.write_title("movie:603", doc)
    before = _listing_count(drv)
    assert backend.read_title("movie:603") == doc
    after_first = _listing_count(drv)
    assert backend.read_title("movie:603") == doc
    after_second = _listing_count(drv)
    # First read lists the subfolder once; the second is served from cache.
    assert after_first == before + 1
    assert after_second == after_first


def test_drive_write_invalidates_stale_listing(drive):
    """Replace-on-update (delete + create) changes the file id; without
    cache invalidation the next read would use the stale id and 404."""
    backend = drive["backend"]
    doc1 = build_doc(tmdb_id=7, type="movie", title={"title": "A"},
                     watch=[], device=None)
    doc2 = build_doc(tmdb_id=7, type="movie", title={"title": "B"},
                     watch=[], device=None)
    backend.write_title("movie:7", doc1)
    # Prime the cache with doc1's file id.
    assert backend.read_title("movie:7") == doc1
    backend.write_title("movie:7", doc2)
    # The read must see the NEW file (new id), not the stale cached one.
    assert backend.read_title("movie:7") == doc2


def test_drive_delete_updates_listing(drive):
    backend = drive["backend"]
    for i in (11, 22):
        backend.write_title(
            f"movie:{i}",
            build_doc(tmdb_id=i, type="movie", title={"title": "X"},
                      watch=[], device=None))
    assert set(backend.list_titles()) == {"movie:11", "movie:22"}
    backend.delete_title("movie:11")
    # The cached listing must reflect the removal (re-listed, not stale).
    assert set(backend.list_titles()) == {"movie:22"}
    assert backend.read_title("movie:11") is None


def test_drive_write_tolerates_stale_delete_404(drive):
    """A 404 on the replace's delete means the old version is already gone
    (a concurrent writer, or an out-of-band delete, removed it). The
    write must proceed and leave exactly one file with the new content."""
    backend, drv = drive["backend"], drive["drv"]
    doc1 = build_doc(tmdb_id=9, type="movie", title={"title": "A"},
                     watch=[], device=None)
    doc2 = build_doc(tmdb_id=9, type="movie", title={"title": "B"},
                     watch=[], device=None)
    backend.write_title("movie:9", doc1)
    # Prime the listing cache with the current file id...
    assert backend.read_title("movie:9") == doc1
    cached_id = next(fid for fid, f in drv.files.items()
                    if f["name"] == "9.json")
    # ...then remove the file out-of-band (a concurrent writer's delete).
    del drv.files[cached_id]
    # The next write resolves the stale id from the cache and its delete
    # 404s — which must be tolerated, not fatal.
    backend.write_title("movie:9", doc2)
    assert backend.read_title("movie:9") == doc2
    stored = [f for f in drv.files.values() if f["name"] == "9.json"]
    assert len(stored) == 1


def test_drive_concurrent_writes_same_key_one_file(drive):
    """The live bug: a debounced progress push and a manual sync each run
    on their own DriveBackend instance (fresh token), so their listing
    caches can't serialize them. Two concurrent replaces of the same
    title must end with exactly ONE file, and neither write may raise."""
    drv = drive["drv"]
    # A second backend instance on the SAME drive (its own clients).
    client2 = httpx.Client(base_url=BASE_URL, transport=drive["transport"])
    upload2 = httpx.Client(base_url=UPLOAD_BASE_URL, transport=drive["transport"])
    backend2 = DriveBackend(TOKEN, ROOT, client=client2, upload_client=upload2)
    docA = build_doc(tmdb_id=13, type="movie", title={"title": "A"},
                     watch=[], device=None)
    docB = build_doc(tmdb_id=13, type="movie", title={"title": "B"},
                     watch=[], device=None)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def w(backend, doc):
        try:
            barrier.wait()
            backend.write_title("movie:13", doc)
        except BaseException as e:  # noqa: BLE001 — report, don't swallow
            errors.append(e)

    t1 = threading.Thread(target=w, args=(drive["backend"], docA))
    t2 = threading.Thread(target=w, args=(backend2, docB))
    t1.start(); t2.start()
    t1.join(); t2.join()
    client2.close()
    upload2.close()
    assert not errors, f"concurrent writes raised: {errors}"
    stored = [f for f in drv.files.values() if f["name"] == "13.json"]
    assert len(stored) == 1, f"concurrent replaces left {len(stored)} files"
    # The survivor holds one of the two docs (whichever replace ran last).
    # (Read the drive directly: each backend's listing cache is
    # per-instance, so a cross-instance read could see a stale map.)
    content = stored[0]["content"].decode("utf-8")
    assert content in (json.dumps(docA, indent=2, sort_keys=True),
                       json.dumps(docB, indent=2, sort_keys=True))


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
