"""Load application config from backend/.env.

Uses a minimal stdlib .env parser to avoid extra dependencies.
Secret values are never logged or printed.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Default project root is the parent of the backend/ directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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


@dataclass
class Config:
    tmdb_read_access_token: str = ""
    torbox_api_key: str = ""
    trakt_client_id: str = ""
    trakt_client_secret: str = ""
    download_dir: str = ""
    db_path: str = field(
        default_factory=lambda: str(_PROJECT_ROOT / "data" / "home_media.db")
    )


def load_config(env_path: Path | None = None) -> Config:
    """Load Config from the given .env path (defaults to backend/.env)."""
    if env_path is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
    env = _parse_env_file(env_path)
    return Config(
        tmdb_read_access_token=env.get("TMDB_READ_ACCESS_TOKEN", ""),
        torbox_api_key=env.get("TORBOX_API_KEY", ""),
        trakt_client_id=env.get("TRAKT_CLIENT_ID", ""),
        trakt_client_secret=env.get("TRAKT_CLIENT_SECRET", ""),
        download_dir=env.get("DOWNLOAD_DIR", ""),
        db_path=env.get("DB_PATH", str(_PROJECT_ROOT / "data" / "home_media.db")),
    )
