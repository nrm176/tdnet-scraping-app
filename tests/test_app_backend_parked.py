"""app/backend is the legacy tdnet-postgres review workbench; it is parked
(2026-10-25) alongside the rest of the --legacy-local pipeline -- see
app/backend/main.py and tdnet/database.py.

This only checks that starting the app refuses fast, before any legacy DB
access (no Postgres connection is ever attempted). It does not exercise the
individual routers/services, which tests/test_app_backend.py already covers
against an in-memory sqlite session built independently of this app.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tdnet.database import LegacyLocalDbParkedError


@pytest.fixture(autouse=True)
def _parked_by_default(monkeypatch):
    monkeypatch.delenv("TDNET_LEGACY_LOCAL_ENABLED", raising=False)


def test_importing_the_app_does_not_create_a_legacy_engine():
    with patch("tdnet.database.create_async_engine") as mock_create:
        import app.backend.main  # noqa: F401  (import-time check only)

        mock_create.assert_not_called()


def test_app_startup_refuses_before_any_legacy_db_access():
    from app.backend.main import app

    with patch("tdnet.database.create_async_engine") as mock_create:
        with pytest.raises(LegacyLocalDbParkedError) as exc_info:
            with TestClient(app):
                pass  # pragma: no cover - startup raises before this runs
        mock_create.assert_not_called()

    message = str(exc_info.value)
    assert "retired" in message
    assert "Root issue #50" in message
