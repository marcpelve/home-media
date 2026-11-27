"""CORS / origin allowlist: only the Tauri webview and local dev servers may
call the API from a browser; foreign web pages are refused outright.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app, origin_allowed

client = TestClient(app)


@pytest.mark.parametrize(
    "origin",
    [
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:8791",
        "http://127.0.0.1:8790",
    ],
)
def test_trusted_origins_allowed(origin):
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example:8790",
        "http://tauri.localhost.evil.example",
        "null",
    ],
)
def test_foreign_origins_refused(origin):
    assert not origin_allowed(origin)
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.status_code == 403
    assert "access-control-allow-origin" not in resp.headers


def test_foreign_simple_post_is_not_executed():
    # A body-less cross-origin POST needs no preflight, so CORS alone would
    # let it run; the origin check must stop it before it reaches the route.
    resp = client.post("/play/quit", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_no_origin_header_unaffected():
    # CLI tools, tests and top-level navigations send no Origin.
    resp = client.get("/health")
    assert resp.status_code == 200


def test_preflight_from_webview():
    resp = client.options(
        "/settings",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://tauri.localhost"
