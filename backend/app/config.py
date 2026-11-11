"""Load application config from a .env file.

Uses a minimal stdlib .env parser to avoid extra dependencies.
Secret values are never logged or printed.

Two locations are used:

  * App dir  -- read-only app files. Frozen: the executable's folder;
    dev: the project root (parent of backend/). Holds mpv.exe.
  * Data dir -- persistent user data that must survive app updates.
    Frozen: %LOCALAPPDATA%\\HomeMedia (created on demand); dev: <repo>/data.
    Holds the DB, downloads, posters, and .env.

Keeping the data dir OUTSIDE the install dir means an update (which
replaces the install) never touches the user's library, tokens, or cache.
On first launch after an upgrade, a one-time migration moves any legacy
in-install-dir data into the data dir.
"""

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Product name used for the persistent data directory.
_DATA_DIR_NAME = "HomeMedia"


def _frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def _app_dir() -> Path:
    """Read-only app/install dir. Frozen: the executable's folder;
    dev: the project root (parent of backend/)."""
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _localappdata() -> Path:
    """%LOCALAPPDATA% (with a sane fallback for non-Windows / unset)."""
    env = os.environ.get("LOCALAPPDATA")
    return Path(env) if env else (Path.home() / "AppData" / "Local")


def _data_dir() -> Path:
    """Persistent user-data dir (DB, downloads, posters, .env).

    Frozen: %LOCALAPPDATA%\\HomeMedia (created on demand) so app updates
    never touch it. Dev: <repo>/data (unchanged).
    """
    if _frozen():
        d = _localappdata() / _DATA_DIR_NAME
    else:
        d = Path(__file__).resolve().parent.parent.parent / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _env_path() -> Path:
    if _frozen():
        return _data_dir() / ".env"
    return Path(__file__).resolve().parent.parent / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file: split on first '=', strip whitespace, skip comments/blanks."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _default_db_path() -> str:
    return str(_data_dir() / "home_media.db")


def _default_download_dir() -> str:
    return str(_data_dir() / "downloads")


def _default_poster_dir() -> str:
    return str(_data_dir() / "posters")


def _default_mpv_path() -> str:
    # Bundled next to the executable when packaged; empty in dev (the .env
    # supplies the real path).
    if _frozen():
        candidate = _app_dir() / "mpv.exe"
        return str(candidate) if candidate.exists() else ""
    return ""


def _legacy_data_roots() -> list[Path]:
    """Install dirs where data/ used to live, in migration order."""
    roots = [_app_dir() / "data"]
    # Known previous per-user install location (pre-Program Files).
    roots.append(
        _localappdata() / "Programs" / "Home Media" / "resources" / "home-media" / "data"
    )
    return roots


def _migrate_legacy_data() -> None:
    """One-time: move user data out of the install dir(s) into the data dir.

    Earlier builds wrote data/ (DB, downloads, posters) and .env next to the
    executable. Move any of that into _data_dir() so it survives updates.
    Idempotent: an item is only moved if it is not already present in the
    data dir. Frozen-only; a no-op in development.
    """
    if not _frozen():
        return
    new_root = _data_dir()
    for root in _legacy_data_roots():
        for name in ("home_media.db", "downloads", "posters"):
            src = root / name
            dst = new_root / name
            if src.exists() and not dst.exists():
                shutil.move(str(src), str(dst))
        # A legacy .env lived next to the executable (root's parent).
        old_env = root.parent / ".env"
        new_env = new_root / ".env"
        if old_env.exists() and not new_env.exists():
            shutil.move(str(old_env), str(new_env))


@dataclass
class Config:
    tmdb_read_access_token: str = ""
    torbox_api_key: str = ""
    trakt_client_id: str = ""
    download_dir: str = field(default_factory=_default_download_dir)
    poster_dir: str = field(default_factory=_default_poster_dir)
    mpv_path: str = field(default_factory=_default_mpv_path)
    db_path: str = field(default_factory=_default_db_path)


def load_config(env_path: Path | None = None) -> Config:
    """Load Config from the given .env path (defaults to the app .env)."""
    _migrate_legacy_data()
    if env_path is None:
        env_path = _env_path()
    env = _parse_env_file(env_path)
    return Config(
        tmdb_read_access_token=env.get("TMDB_READ_ACCESS_TOKEN", ""),
        torbox_api_key=env.get("TORBOX_API_KEY", ""),
        trakt_client_id=env.get("TRAKT_CLIENT_ID", ""),
        download_dir=env.get("DOWNLOAD_DIR", _default_download_dir()),
        poster_dir=env.get("POSTER_DIR", _default_poster_dir()),
        mpv_path=env.get("MPV_PATH", _default_mpv_path()),
        db_path=env.get("DB_PATH", _default_db_path()),
    )
