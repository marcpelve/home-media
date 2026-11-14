"""Trakt auth + one-time import routes (Device Code Flow).

POST /trakt/auth           -> start a device login, returns user_code + verification_url
GET  /trakt/auth           -> poll the pending flow (pending|authorized|expired|none)
GET  /trakt/status         -> auth state + last import info
POST /trakt/import         -> one-time import of the Trakt watch history (P-M5)
GET  /trakt/recommendations -> read-only recommendations (kept)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.sync import import_from_trakt, _get_setting
from app.trakt import TraktAuthError

router = APIRouter(prefix="/trakt", tags=["trakt"])


def _client(request: Request):
    return request.app.state.trakt


def _db_path(request: Request) -> str:
    return request.app.state.db_path


@router.post("/auth")
async def start_auth(request: Request) -> dict:
    client = _client(request)
    try:
        return await client.start_device_login()
    except TraktAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"trakt device code request failed: {e}"
        )


@router.get("/auth")
async def poll_auth(request: Request) -> dict:
    client = _client(request)
    try:
        return await client.poll_pending()
    except TraktAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/status")
async def status(request: Request) -> dict:
    client = _client(request)
    tokens = client.load_tokens()
    username = tokens["username"] if tokens else None
    # Self-heal a placeholder username by fetching the real account.
    if tokens and (not username or username == "unknown"):
        real = await client.get_username()
        if real:
            client.save_tokens(
                real, tokens["access_token"], tokens["refresh_token"],
                tokens["expires_at"],
            )
            username = real
    db_path = _db_path(request)
    last_at = _get_setting(db_path, "trakt_last_import_at")
    last_count_raw = _get_setting(db_path, "trakt_last_import_count")
    last_import = None
    if last_at is not None:
        last_import = {
            "at": last_at,
            "count": int(last_count_raw) if last_count_raw is not None else 0,
        }
    return {
        "authenticated": client.is_authenticated(),
        "username": username,
        "last_import": last_import,
    }


@router.post("/import")
async def trakt_import(request: Request) -> dict:
    client = _client(request)
    if not client.is_authenticated():
        raise HTTPException(status_code=401, detail="not authenticated")
    return await import_from_trakt(client, _db_path(request))


@router.get("/recommendations")
async def recommendations(request: Request) -> dict:
    """Personalized picks from Trakt, normalized to {id, title, type, poster_path}.

    The TMDB id (ids.tmdb) is what the rest of the app keys on; items without
    one are dropped. poster_path is resolved via the cached TMDB title lookup so
    the frontend can serve posters through the /tmdb/poster proxy.
    """
    import asyncio as _aio

    trakt = _client(request)
    tmdb = request.app.state.tmdb_client
    data = await trakt.get_recommendations()

    def _norm(items: list, key: str, type_: str) -> list:
        out = []
        for it in items:
            inner = it.get(key) or {}
            tmdb_id = (inner.get("ids") or {}).get("tmdb")
            if tmdb_id:
                out.append({"id": tmdb_id, "title": inner.get("title"), "type": type_})
        return out

    movies = _norm(data.get("movie", []), "movie", "movie")
    shows = _norm(data.get("show", []), "show", "tv")

    async def _poster(it: dict) -> None:
        try:
            details = await tmdb.title_typed(it["id"], it["type"])
            it["poster_path"] = details.get("poster_path")
        except Exception:
            it["poster_path"] = None

    await _aio.gather(*[_poster(it) for it in movies + shows])
    return {"movie": movies, "show": shows}
