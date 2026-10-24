"""Runtime configuration for the TDnet service."""
from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_DATABASE_URL = "postgresql+asyncpg://tdnet:tdnet@localhost:55432/tdnet"
DEFAULT_DOWNLOAD_ROOT = "/Volumes/yakushimachi/Downloads"


def _parse_bool_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    """Environment-backed application settings."""

    database_url: str = DEFAULT_DATABASE_URL
    download_root: str = DEFAULT_DOWNLOAD_ROOT
    api_title: str = "TDnet API"
    api_version: str = "0.1.0"
    # Legacy tdnet-postgres (DATABASE_URL above) is parked/retired as of
    # 2026-10-25 -- see tdnet/database.py. False unless explicitly opted in.
    legacy_local_db_enabled: bool = False


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
        download_root=os.getenv("TDNET_DOWNLOAD_ROOT", DEFAULT_DOWNLOAD_ROOT),
        api_title=os.getenv("TDNET_API_TITLE", "TDnet API"),
        api_version=os.getenv("TDNET_API_VERSION", "0.1.0"),
        legacy_local_db_enabled=_parse_bool_env(os.getenv("TDNET_LEGACY_LOCAL_ENABLED")),
    )
