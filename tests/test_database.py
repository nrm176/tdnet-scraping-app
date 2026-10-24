"""Tests for the parked legacy tdnet-postgres guard in tdnet/database.py.

Context: the legacy tdnet-postgres database is retired (2026-10-25). Every
entry point into tdnet/database.py -- init_db(), SessionLocal(), get_session()
-- is disabled by default behind a single guard, require_legacy_local_enabled(),
gated on the TDNET_LEGACY_LOCAL_ENABLED environment variable. The code is kept
(parked), not deleted, so it can be re-enabled deliberately later.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tdnet import database
from tdnet.database import (
    LegacyLocalDbParkedError,
    get_engine,
    get_session,
    init_db,
    require_legacy_local_enabled,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_legacy_db_state(monkeypatch):
    """Default every test to parked, and reset the lazily cached engine so
    one test enabling it can't leak into the next."""
    monkeypatch.delenv("TDNET_LEGACY_LOCAL_ENABLED", raising=False)
    database._engine = None
    yield
    database._engine = None


def test_require_legacy_local_enabled_raises_with_clear_message_by_default():
    with pytest.raises(LegacyLocalDbParkedError) as exc_info:
        require_legacy_local_enabled()
    message = str(exc_info.value)
    assert "retired" in message
    assert "raw_tdnet_history" in message
    assert "Root issue #50" in message
    assert "TDNET_LEGACY_LOCAL_ENABLED" in message


def test_require_legacy_local_enabled_passes_when_opted_in(monkeypatch):
    monkeypatch.setenv("TDNET_LEGACY_LOCAL_ENABLED", "1")
    require_legacy_local_enabled()  # does not raise


def test_get_engine_raises_by_default_and_never_constructs_an_engine():
    with patch("tdnet.database.create_async_engine") as mock_create:
        with pytest.raises(LegacyLocalDbParkedError):
            get_engine()
        mock_create.assert_not_called()
    assert database._engine is None


def test_get_engine_builds_lazily_and_caches_once_enabled(monkeypatch):
    monkeypatch.setenv("TDNET_LEGACY_LOCAL_ENABLED", "1")
    with patch("tdnet.database.create_async_engine") as mock_create:
        mock_create.return_value = "sentinel-engine"
        first = get_engine()
        second = get_engine()
    assert first == "sentinel-engine"
    assert second is first
    mock_create.assert_called_once()


def test_session_local_call_raises_by_default():
    with pytest.raises(LegacyLocalDbParkedError):
        database.SessionLocal()


@pytest.mark.asyncio
async def test_get_session_raises_by_default():
    with pytest.raises(LegacyLocalDbParkedError):
        async for _ in get_session():
            pass  # pragma: no cover - never reached while parked


@pytest.mark.asyncio
async def test_init_db_raises_by_default_and_touches_no_engine():
    with patch("tdnet.database.create_async_engine") as mock_create:
        with pytest.raises(LegacyLocalDbParkedError):
            await init_db()
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_init_db_accepts_an_explicit_engine_only_once_enabled(monkeypatch):
    """A caller that passes its own engine (e.g. a test's sqlite engine via
    database.init_db directly, if anyone ever did that) is still gated by the
    same guard -- the guard is unconditional, not merely "no default engine"."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(LegacyLocalDbParkedError):
            await init_db(target_engine=engine)

        monkeypatch.setenv("TDNET_LEGACY_LOCAL_ENABLED", "1")
        await init_db(target_engine=engine)  # does not raise once opted in
    finally:
        await engine.dispose()


def test_create_engine_helper_is_not_gated_itself():
    """`create_engine()` is the raw builder (used by get_engine()); the guard
    lives in get_engine()/SessionLocal()/init_db(), not here, so this must
    keep working for whoever re-enables the parked pipeline."""
    engine = database.create_engine("sqlite+aiosqlite:///:memory:")
    assert isinstance(engine, AsyncEngine)


def test_importing_tdnet_database_fresh_never_creates_an_engine():
    """Import tdnet.database (and the CLI/app modules that import from it)
    in a clean subprocess and confirm the module-level engine stays unbuilt --
    importing must never create or connect to the legacy engine."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    script = (
        "import tdnet.database as d\n"
        "import tdnet.cli\n"
        "assert d._engine is None, 'engine was created merely by importing'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_importing_app_backend_main_never_creates_an_engine():
    """app/backend/main.py is the parked legacy review workbench; merely
    importing it (e.g. for `uvicorn app.backend.main:app` to even parse the
    module) must not create or connect to the legacy engine either."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    script = (
        "import app.backend.main\n"
        "import tdnet.database as d\n"
        "assert d._engine is None, 'engine was created merely by importing'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
