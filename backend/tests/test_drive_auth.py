"""Drive OAuth + settings API tests (httpx MockTransport, no real HTTP)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.db import init_db
from app.drive_auth import (
    DRIVE_SCOPE,
    REDIRECT_URI,
    DriveAuthError,
    complete_connect,
    get_valid_access_token,
    make_cloud_backend,
    start_connect,
)
from app.routers.settings import update, view
from app.storage import DriveBackend, LocalFolderBackend


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FAKE_CLIENT_ID = "fake_client_id"
FAKE_CLIENT_SECRET = "fake_client_secret"

# Default successful token response.
TOKEN_OK = {
    "access_token": "at_fresh",
    "refresh_token": "rt_fresh",
    "expires_in": 3600,
}


def _make_token_handler(
    token_response: httpx.Response | None = None,
    calls: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Build a MockTransport that serves the token endpoint."""
    if calls is None:
        calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if token_response is not None:
            return token_response
        return httpx.Response(200, json=TOKEN_OK)

    return httpx.MockTransport(handler)


def _make_oauth_drive_handler(
    drive_files: dict[str, list[dict]] | None = None,
    drive_status: int = 200,
    calls: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A MockTransport serving BOTH the token endpoint (any POST to
    /token) and the Drive API (GET /files searches, POST /files creates).

    *drive_files* maps a folder name to the files its name-search returns;
    names absent from the dict are "not found". *drive_status* != 200 makes
    every Drive request fail with that status. Created folders get ids
    folder_1, folder_2, ... in creation order.
    """
    if calls is None:
        calls = []
    if drive_files is None:
        drive_files = {}
    state = {"created": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json=TOKEN_OK)
        if drive_status != 200:
            return httpx.Response(drive_status, text="denied")
        if request.method == "GET":
            q = request.url.params.get("q", "")
            for name, files in drive_files.items():
                if f"name='{name}'" in q:
                    return httpx.Response(200, json={"files": files})
            return httpx.Response(200, json={"files": []})
        # POST /files -> create
        state["created"] += 1
        return httpx.Response(200, json={"id": f"folder_{state['created']}"})

    return httpx.MockTransport(handler)


def _oauth_drive_client(
    transport: httpx.MockTransport,
) -> httpx.Client:
    """A client wired for both the token endpoint (absolute URL) and the
    Drive API (relative /files paths via base_url)."""
    return httpx.Client(
        transport=transport, base_url="https://www.googleapis.com/drive/v3"
    )


def _set(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def _get(db_path: str, key: str) -> str | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


@pytest.fixture
def db(tmp_path: Path) -> str:
    """A fresh SQLite DB with the schema initialized."""
    p = tmp_path / "test_drive.db"
    init_db(p)
    return str(p)


# ---------------------------------------------------------------------------
# Tests: start_connect
# ---------------------------------------------------------------------------

def test_start_connect_url_and_state(db: str):
    result = start_connect(db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
    url = result["authorization_url"]
    assert result["redirect_uri"] == REDIRECT_URI
    # The URL is built with urlencode (percent-encoded values), so decode
    # the query before comparing.
    params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert params["client_id"] == FAKE_CLIENT_ID
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["scope"] == DRIVE_SCOPE
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    # state is stored in settings and embedded in the URL.
    state = _get(db, "drive_oauth_state")
    assert state is not None and len(state) > 0
    assert params["state"] == state


# ---------------------------------------------------------------------------
# Tests: complete_connect
# ---------------------------------------------------------------------------

def test_complete_connect_stores_tokens(db: str):
    # Start the flow to get a valid state.
    result = start_connect(db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
    state = _get(db, "drive_oauth_state")

    calls: list[httpx.Request] = []
    transport = _make_oauth_drive_handler(calls=calls)
    client = _oauth_drive_client(transport)
    resp = complete_connect(
        db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "fake_code", state,
        client=client,
    )
    assert resp == {"connected": True}

    # Tokens are stored.
    assert _get(db, "drive_access_token") == "at_fresh"
    assert _get(db, "drive_refresh_token") == "rt_fresh"
    expires = _get(db, "drive_access_expires_at")
    assert expires is not None
    # Expiry should be ~now + 3600.
    assert abs(float(expires) - (time.time() + 3600)) < 5

    # State is cleared.
    assert _get(db, "drive_oauth_state") == ""

    # Root folder bootstrapped (nothing existed -> all three created,
    # HomeMedia first) and its id stored.
    assert _get(db, "drive_root_folder_id") == "folder_1"
    creates = [
        r for r in calls
        if r.method == "POST" and r.url.path.endswith("/files")
    ]
    assert len(creates) == 3


def test_complete_connect_reuses_existing_root(db: str):
    """Existing HomeMedia/ with movies/ + shows/ -> no folder creates,
    the existing root id is stored."""
    start_connect(db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
    state = _get(db, "drive_oauth_state")
    assert state is not None

    existing = {
        "HomeMedia": [{"id": "root_1", "name": "HomeMedia"}],
        "movies": [{"id": "sub_m", "name": "movies"}],
        "shows": [{"id": "sub_s", "name": "shows"}],
    }
    calls: list[httpx.Request] = []
    transport = _make_oauth_drive_handler(drive_files=existing, calls=calls)
    client = _oauth_drive_client(transport)
    resp = complete_connect(
        db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "fake_code", state,
        client=client,
    )
    assert resp == {"connected": True}
    assert _get(db, "drive_root_folder_id") == "root_1"
    creates = [
        r for r in calls
        if r.method == "POST" and r.url.path.endswith("/files")
    ]
    assert creates == []


def test_complete_connect_bootstrap_failure_rolls_back_tokens(db: str):
    """Drive API failure during bootstrap -> DriveAuthError and the stored
    tokens are cleared so a retry starts clean."""
    start_connect(db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
    state = _get(db, "drive_oauth_state")
    assert state is not None

    transport = _make_oauth_drive_handler(drive_status=403)
    client = _oauth_drive_client(transport)
    with pytest.raises(DriveAuthError, match="Drive API 403"):
        complete_connect(
            db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "fake_code", state,
            client=client,
        )
    assert not _get(db, "drive_access_token")
    assert not _get(db, "drive_refresh_token")
    assert not _get(db, "drive_root_folder_id")


def test_complete_connect_state_mismatch(db: str):
    start_connect(db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
    with pytest.raises(DriveAuthError, match="state mismatch"):
        complete_connect(
            db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "fake_code", "wrong_state"
        )


def test_complete_connect_token_endpoint_error(db: str):
    start_connect(db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
    state = _get(db, "drive_oauth_state")

    transport = _make_token_handler(
        token_response=httpx.Response(400, json={"error": "invalid_grant"})
    )
    client = httpx.Client(transport=transport)
    with pytest.raises(DriveAuthError, match="token endpoint 400"):
        complete_connect(
            db, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, "fake_code", state,
            client=client,
        )


# ---------------------------------------------------------------------------
# Tests: get_valid_access_token
# ---------------------------------------------------------------------------

def test_get_valid_access_token_cached(db: str):
    """A fresh token is returned without any HTTP call."""
    _set(db, "drive_access_token", "at_fresh")
    _set(db, "drive_access_expires_at", str(time.time() + 3600))

    calls: list[httpx.Request] = []
    transport = _make_token_handler(calls=calls)
    client = httpx.Client(transport=transport)

    token = get_valid_access_token(db, client=client)
    assert token == "at_fresh"
    assert len(calls) == 0  # no refresh call


def test_get_valid_access_token_refreshes_expired(db: str):
    """Expired token + refresh credentials -> token endpoint called with
    grant_type=refresh_token; rotated refresh token stored."""
    _set(db, "drive_access_token", "at_old")
    _set(db, "drive_access_expires_at", str(time.time() - 10))  # past
    _set(db, "drive_refresh_token", "rt_old")
    _set(db, "drive_client_id", FAKE_CLIENT_ID)
    _set(db, "drive_client_secret", FAKE_CLIENT_SECRET)

    rotated = {
        "access_token": "at_new",
        "refresh_token": "rt_rotated",
        "expires_in": 3600,
    }
    calls: list[httpx.Request] = []
    transport = _make_token_handler(
        token_response=httpx.Response(200, json=rotated), calls=calls
    )
    client = httpx.Client(transport=transport)

    token = get_valid_access_token(db, client=client)
    assert token == "at_new"
    assert len(calls) == 1

    # Verify the request carried grant_type=refresh_token.
    body = calls[0].content.decode()
    assert "grant_type=refresh_token" in body

    # New access token + rotated refresh token are stored.
    assert _get(db, "drive_access_token") == "at_new"
    assert _get(db, "drive_refresh_token") == "rt_rotated"


def test_get_valid_access_token_unconnected(db: str):
    """No tokens stored -> None, no HTTP."""
    calls: list[httpx.Request] = []
    transport = _make_token_handler(calls=calls)
    client = httpx.Client(transport=transport)

    token = get_valid_access_token(db, client=client)
    assert token is None
    assert len(calls) == 0


# ---------------------------------------------------------------------------
# Tests: make_cloud_backend
# ---------------------------------------------------------------------------

def test_make_cloud_backend_local(db: str, tmp_path: Path):
    """storage_backend=local -> LocalFolderBackend."""
    root = tmp_path / "storage"
    _set(db, "storage_backend", "local")
    _set(db, "storage_local_root", str(root))

    backend = make_cloud_backend(db)
    assert isinstance(backend, LocalFolderBackend)
    assert backend.root == root


def test_make_cloud_backend_drive_unconnected(db: str):
    """storage_backend=drive with no tokens -> None."""
    _set(db, "storage_backend", "drive")
    _set(db, "drive_root_folder_id", "folder_123")

    backend = make_cloud_backend(db)
    assert backend is None


def test_make_cloud_backend_drive_connected(db: str):
    """storage_backend=drive with a valid token -> DriveBackend."""
    _set(db, "storage_backend", "drive")
    _set(db, "drive_root_folder_id", "folder_123")
    _set(db, "drive_access_token", "at_valid")
    _set(db, "drive_access_expires_at", str(time.time() + 3600))
    _set(db, "drive_refresh_token", "rt_valid")

    backend = make_cloud_backend(db)
    assert isinstance(backend, DriveBackend)
    assert backend.access_token == "at_valid"


def test_make_cloud_backend_lazy_bootstraps_root(db: str):
    """Tokens present but no drive_root_folder_id (a connection made
    before bootstrap existed) -> the root folder is created on demand
    and the backend is returned, no re-authorize needed."""
    _set(db, "drive_access_token", "at_fresh")
    _set(db, "drive_refresh_token", "rt_fresh")
    _set(db, "drive_access_expires_at", str(time.time() + 3600))

    transport = _make_oauth_drive_handler()
    client = _oauth_drive_client(transport)
    backend = make_cloud_backend(db, client=client)
    assert isinstance(backend, DriveBackend)
    assert _get(db, "drive_root_folder_id") == "folder_1"


def test_make_cloud_backend_auto_drive_when_connected(db: str):
    """Tokens present, no explicit storage_backend -> DriveBackend."""
    _set(db, "drive_root_folder_id", "folder_123")
    _set(db, "drive_access_token", "at_valid")
    _set(db, "drive_access_expires_at", str(time.time() + 3600))
    _set(db, "drive_refresh_token", "rt_valid")

    backend = make_cloud_backend(db)
    assert isinstance(backend, DriveBackend)
    assert backend.access_token == "at_valid"


def test_make_cloud_backend_auto_local_when_unconnected(db: str, tmp_path: Path):
    """No tokens, no explicit storage_backend -> LocalFolderBackend."""
    root = tmp_path / "storage"
    _set(db, "storage_local_root", str(root))

    backend = make_cloud_backend(db)
    assert isinstance(backend, LocalFolderBackend)
    assert backend.root == root


def test_make_cloud_backend_explicit_local_rollback(db: str, tmp_path: Path):
    """Explicit local + tokens present -> LocalFolderBackend (rollback wins)."""
    root = tmp_path / "storage"
    _set(db, "storage_backend", "local")
    _set(db, "storage_local_root", str(root))
    _set(db, "drive_root_folder_id", "folder_123")
    _set(db, "drive_access_token", "at_valid")
    _set(db, "drive_access_expires_at", str(time.time() + 3600))
    _set(db, "drive_refresh_token", "rt_valid")

    backend = make_cloud_backend(db)
    assert isinstance(backend, LocalFolderBackend)
    assert backend.root == root


def test_make_cloud_backend_explicit_drive_no_tokens(db: str):
    """Explicit drive + no tokens -> None."""
    _set(db, "storage_backend", "drive")
    _set(db, "drive_root_folder_id", "folder_123")

    backend = make_cloud_backend(db)
    assert backend is None


# ---------------------------------------------------------------------------
# Tests: settings view/update
# ---------------------------------------------------------------------------

def test_settings_view_update(db: str):
    """update sets only whitelisted keys, ignores others, rejects bad
    storage_backend, and view reflects the values."""
    # Set some values.
    result = update(db, {
        "storage_backend": "drive",
        "storage_local_root": "/some/path",
        "device_id": "device-abc",
        "drive_client_id": "cid",
        "drive_client_secret": "csecret",
        "drive_root_folder_id": "root-123",
        # This key is NOT in the whitelist -- must be ignored.
        "drive_access_token": "should_not_be_set",
    })

    # drive_access_token must NOT have been set by update.
    assert _get(db, "drive_access_token") is None

    # View reflects the whitelisted values.
    v = view(db)
    assert v["storage_backend"] == "drive"
    assert v["storage_local_root"] == "/some/path"
    assert v["device_id"] == "device-abc"
    assert v["drive_client_id"] == "cid"
    assert v["drive_root_folder_id"] == "root-123"
    assert v["drive_connected"] is False  # no tokens set

    # Bad storage_backend raises ValueError.
    with pytest.raises(ValueError):
        update(db, {"storage_backend": "bogus"})


def test_settings_view_device_defaults_to_username(db: str, monkeypatch):
    """View reports the effective device id: the Windows username when no
    explicit device_id is set, else 'default'."""
    monkeypatch.setenv("USERNAME", "testuser")
    assert view(db)["device_id"] == "testuser"

    monkeypatch.delenv("USERNAME", raising=False)
    assert view(db)["device_id"] == "default"
