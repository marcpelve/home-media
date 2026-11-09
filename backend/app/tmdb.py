"""TMDB v3 API client with SQLite-backed cache and in-flight de-duplication.

Used by the /tmdb/* router. All calls are cached at day-level TTLs to be
gentle on TMDB's rate limits. The Bearer token comes from Config and is
never logged.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from pathlib import Path

import httpx
import sqlite3

# TTL choices (seconds):
#   home/trending  1 day  – changes daily at most
#   genres         30 days – effectively static
#   title details  7 days – rare edits (new seasons, cast changes)
#   reviews        1 day  – new reviews appear daily
#   posters        6 months – image paths are immutable per TMDB
TTL_HOME = 86_400
TTL_GENRES = 30 * 86_400
TTL_DETAILS = 7 * 86_400
TTL_REVIEWS = 86_400
TTL_POSTER = 180 * 86_400

POSTER_SIZES = ["w92", "w154", "w185", "w342", "w500", "w780", "original"]


class TmdbClient:
    """Async TMDB client with cache + per-path lock de-duplication."""

    def __init__(
        self,
        token: str,
        db_path: str | Path,
        poster_dir: str | Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._db_path = str(db_path)
        self._poster_dir = Path(poster_dir) if poster_dir else None
        self._client = client
        self._owns_client = client is None
        self._locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> "TmdbClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url="https://api.themoviedb.org/3",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"language": "en-US"},
                timeout=15.0,
            )
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- cache helpers -------------------------------------------------------

    def _cache_get(self, path: str, ttl: int) -> dict | None:
        """Return cached payload if fresh, else None."""
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT payload, created_at, ttl_seconds FROM tmdb_cache WHERE path = ?",
                (path,),
            ).fetchone()
            if row is None:
                return None
            payload, created_at, stored_ttl = row
            if time.time() - created_at > stored_ttl:
                return None
            return json.loads(payload)
        finally:
            conn.close()

    def _cache_set(self, path: str, payload: dict, ttl: int) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO tmdb_cache (path, payload, created_at, ttl_seconds) "
                "VALUES (?, ?, ?, ?)",
                (path, json.dumps(payload), time.time(), ttl),
            )
            conn.commit()
        finally:
            conn.close()

    # -- core fetch ----------------------------------------------------------

    async def _fetch(self, path: str, ttl: int, params: dict | None = None) -> dict:
        """Fetch a TMDB path with cache + de-duplication."""
        # Cache key must include params: same path with different query
        # strings (e.g. search) must not collide, else the first result wins.
        cache_key = path if not params else f"{path}?{urllib.parse.urlencode(sorted(params.items()))}"
        # Check cache first
        cached = self._cache_get(cache_key, ttl)
        if cached is not None:
            return cached

        # De-duplicate in-flight requests per cache key
        if cache_key not in self._locks:
            self._locks[cache_key] = asyncio.Lock()
        lock = self._locks[cache_key]

        async with lock:
            # Re-check cache after acquiring lock (another coroutine may have
            # populated it while we waited).
            cached = self._cache_get(cache_key, ttl)
            if cached is not None:
                return cached

            client = self._client
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            data = resp.json()
            self._cache_set(cache_key, data, ttl)
            return data

    # -- public API methods --------------------------------------------------

    async def search(self, query: str) -> dict:
        """Search movies + TV in one shot."""
        import asyncio as _aio

        movie_task = self._fetch("search/movie", TTL_HOME, {"query": query})
        tv_task = self._fetch("search/tv", TTL_HOME, {"query": query})
        movie, tv = await _aio.gather(movie_task, tv_task)
        return {"movie": movie.get("results", []), "tv": tv.get("results", [])}

    async def home(self) -> dict:
        """Trending all/week."""
        data = await self._fetch("trending/all/week", TTL_HOME)
        return data

    async def genres(self) -> dict:
        """Genre lists for movie + tv."""
        import asyncio as _aio

        movie, tv = await _aio.gather(
            self._fetch("genre/list/movie", TTL_GENRES),
            self._fetch("genre/list/tv", TTL_GENRES),
        )
        return {"movie": movie.get("genres", []), "tv": tv.get("genres", [])}

    async def title(self, tmdb_id: int) -> dict:
        """Title details; tries movie first, falls back to tv."""
        try:
            data = await self._fetch(f"movie/{tmdb_id}", TTL_DETAILS)
            data["_type"] = "movie"
            return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Base tv responses omit imdb_id; pull it from external_ids.
                data = await self._fetch(f"tv/{tmdb_id}", TTL_DETAILS, {"append_to": "external_ids"})
                data["_type"] = "tv"
                return data
            raise

    async def reviews(self, tmdb_id: int) -> dict:
        """Reviews; tries movie first, falls back to tv."""
        import asyncio as _aio

        async def _try_reviews(prefix: str) -> list:
            try:
                data = await self._fetch(f"{prefix}/{tmdb_id}/reviews", TTL_REVIEWS)
                return data.get("results", [])
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return []
                raise

        movie_reviews = await _try_reviews("movie")
        if movie_reviews:
            return {"results": movie_reviews, "type": "movie"}
        tv_reviews = await _try_reviews("tv")
        return {"results": tv_reviews, "type": "tv"}

    # -- poster helpers -------------------------------------------------------

    def poster_url(self, path: str | None, size: str = "w342") -> str | None:
        """Build a full poster URL from a TMDB image path."""
        if not path:
            return None
        return f"https://image.tmdb.org/t/p/{size}{path}"

    async def get_poster(self, tmdb_id: int, poster_path: str | None, size: str = "w342") -> Path | None:
        """Download + cache a poster to disk. Returns the local file path."""
        if not poster_path:
            return None

        # Determine extension from URL path
        ext = Path(poster_path).suffix or ".jpg"
        dest = (self._poster_dir / f"{tmdb_id}{ext}") if self._poster_dir else None
        if dest is None:
            return None

        # Serve from disk cache if fresh
        if dest.exists():
            age = time.time() - dest.stat().st_mtime
            if age < TTL_POSTER:
                return dest

        # Download
        url = f"https://image.tmdb.org/t/p/{size}{poster_path}"
        client = self._client
        resp = await client.get(url, base_url=None)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
