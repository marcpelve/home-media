"""Load application config from a .env file.

Uses a minimal stdlib .env parser to avoid extra dependencies.
Secret values are never logged or printed.

Paths resolve relative to the repo in development and relative to the
executable when packaged (PyInstaller), so the same config works in both.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def _base_dir() -> Path:
    """Folder the app's .env + data live in.

    Packaged: the directory containing the executable.
    Development: the project root (parent of backend/).
    """
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _env_path() -> Path:
    if _frozen():
        return _base_dir() / ".env"
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
    return str(_base_dir() / "data" / "home_media.db")


def _default_mpv_path() -> str:
    # Bundled next to the executable when packaged; empty in dev (the .env
    # supplies the real path).
    if _frozen():
        candidate = _base_dir() / "mpv.exe"
        return str(candidate) if candidate.exists() else ""
    return ""


@dataclass
class Config:
    tmdb_read_access_token: str = ""
    torbox_api_key: str = ""
    trakt_client_id: str = ""
    download_dir: str = ""
    mpv_path: str = field(default_factory=_default_mpv_path)
    db_path: str = field(default_factory=_default_db_path)


def load_config(env_path: Path | None = None) -> Config:
    """Load Config from the given .env path (defaults to the app .env)."""
    if env_path is None:
        env_path = _env_path()
    env = _parse_env_file(env_path)
    return Config(
        tmdb_read_access_token=env.get("TMDB_READ_ACCESS_TOKEN", ""),
        torbox_api_key=env.get("TORBOX_API_KEY", ""),
        trakt_client_id=env.get("TRAKT_CLIENT_ID", ""),
        download_dir=env.get("DOWNLOAD_DIR", ""),
        mpv_path=env.get("MPV_PATH", _default_mpv_path()),
        db_path=env.get("DB_PATH", _default_db_path()),
    )
