"""Test that Config loads expected fields from a .env file."""

from pathlib import Path

from app.config import Config, load_config


def test_config_loads_expected_fields(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# test env\n"
        "TMDB_READ_ACCESS_TOKEN=tmdb_token_value\n"
        "TORBOX_API_KEY=torbox_key_value\n"
        "TRAKT_CLIENT_ID=trakt_id_value\n"
        "DOWNLOAD_DIR=/tmp/downloads\n"
        "DB_PATH=/tmp/test.db\n",
        encoding="utf-8",
    )

    cfg: Config = load_config(env_file)

    # Assert attribute names exist and non-secret values are correct.
    # We do NOT assert on or print secret values.
    assert hasattr(cfg, "tmdb_read_access_token")
    assert hasattr(cfg, "torbox_api_key")
    assert hasattr(cfg, "trakt_client_id")
    # Device Code Flow needs no client_secret.
    assert not hasattr(cfg, "trakt_client_secret")
    assert cfg.download_dir == "/tmp/downloads"
    assert cfg.db_path == "/tmp/test.db"
