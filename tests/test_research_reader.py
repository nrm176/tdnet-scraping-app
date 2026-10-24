"""Unit tests for tdnet/research_reader.py: the research_reader URL builder
(RESEARCH_DATABASE_URL override, session-file parsing, mode check) and the
async identity validator. No live database or network access; everything
here is stubbed, mirroring EdinetAPI's tests/test_research_connection.py and
portfolio-manager's backend/tests/test_research_db.py adapted to this
module's async SQLAlchemy shape."""
from __future__ import annotations

import json
import os

import pytest

from tdnet.research_reader import (
    RESEARCH_DATABASE,
    RESEARCH_HOST,
    RESEARCH_INSTANCE_NAME,
    RESEARCH_PORT,
    RESEARCH_ROLE,
    RESEARCH_SCHEMA_VERSION,
    ResearchReaderError,
    build_reader_url,
    validate_research_identity,
)


def write_session(path, payload, mode=0o600):
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, mode)
    return path


# --- build_reader_url: RESEARCH_DATABASE_URL override -----------------------


def test_build_reader_url_prefers_explicit_env(tmp_path):
    missing = tmp_path / "session.json"  # never touched: explicit URL wins first
    url = build_reader_url(
        {"RESEARCH_DATABASE_URL": "postgresql+asyncpg://someone:secret@elsewhere:5432/hft_research"},
        session_path=missing,
    )
    assert url == "postgresql+asyncpg://someone:secret@elsewhere:5432/hft_research"
    assert not missing.exists()


def test_build_reader_url_ignores_blank_explicit_env(tmp_path):
    session_path = write_session(tmp_path / "session.json", {"reader_password": "pw"})
    url = build_reader_url({"RESEARCH_DATABASE_URL": ""}, session_path=session_path)
    assert url.startswith(f"postgresql+asyncpg://{RESEARCH_ROLE}:pw@")


# --- build_reader_url: built from the session file ---------------------------


def test_build_reader_url_from_session_file(tmp_path):
    session_path = write_session(tmp_path / "session.json", {"reader_password": "pw-123"})
    url = build_reader_url({}, session_path=session_path)
    assert url == (
        f"postgresql+asyncpg://{RESEARCH_ROLE}:pw-123@{RESEARCH_HOST}:{RESEARCH_PORT}/{RESEARCH_DATABASE}"
    )


def test_build_reader_url_quotes_special_characters(tmp_path):
    password = "p@ss/word:with?special&chars"
    session_path = write_session(tmp_path / "session.json", {"reader_password": password})
    url = build_reader_url({}, session_path=session_path)
    expected = (
        f"postgresql+asyncpg://{RESEARCH_ROLE}:p%40ss%2Fword%3Awith%3Fspecial%26chars"
        f"@{RESEARCH_HOST}:{RESEARCH_PORT}/{RESEARCH_DATABASE}"
    )
    assert url == expected
    assert password not in url


def test_build_reader_url_missing_session_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(ResearchReaderError) as exc_info:
        build_reader_url({}, session_path=missing)
    assert exc_info.value.code == "session_unavailable"


def test_build_reader_url_rejects_wrong_mode(tmp_path):
    session_path = write_session(tmp_path / "session.json", {"reader_password": "pw"}, mode=0o644)
    with pytest.raises(ResearchReaderError) as exc_info:
        build_reader_url({}, session_path=session_path)
    assert exc_info.value.code == "session_unavailable"
    assert "pw" not in str(exc_info.value)


def test_build_reader_url_missing_password_key(tmp_path):
    session_path = write_session(tmp_path / "session.json", {"admin_password": "x"})
    with pytest.raises(ResearchReaderError) as exc_info:
        build_reader_url({}, session_path=session_path)
    assert exc_info.value.code == "session_unavailable"


def test_build_reader_url_invalid_json(tmp_path):
    session_path = tmp_path / "session.json"
    session_path.write_text("not json", encoding="utf-8")
    os.chmod(session_path, 0o600)
    with pytest.raises(ResearchReaderError) as exc_info:
        build_reader_url({}, session_path=session_path)
    assert exc_info.value.code == "session_unavailable"


# --- validate_research_identity: stubbed connection, no live database -------


class FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeConnection:
    """Returns one canned row per `execute()` call, in order."""

    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, _query, _params=None):
        return FakeResult(self._rows.pop(0))


class RaisingConnection:
    def __init__(self, exc):
        self._exc = exc

    async def execute(self, _query, _params=None):
        raise self._exc


@pytest.mark.asyncio
async def test_validate_research_identity_accepts_matching_identity():
    conn = FakeConnection(
        [
            (RESEARCH_DATABASE, RESEARCH_ROLE, 180005),
            (RESEARCH_INSTANCE_NAME, RESEARCH_SCHEMA_VERSION),
        ]
    )
    await validate_research_identity(conn)  # must not raise


@pytest.mark.asyncio
async def test_validate_research_identity_accepts_legacy_small_int_version():
    conn = FakeConnection(
        [
            (RESEARCH_DATABASE, RESEARCH_ROLE, 18),
            (RESEARCH_INSTANCE_NAME, RESEARCH_SCHEMA_VERSION),
        ]
    )
    await validate_research_identity(conn)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity_row",
    [
        ("wrong_database", RESEARCH_ROLE, 180005),
        (RESEARCH_DATABASE, "wrong_role", 180005),
        (RESEARCH_DATABASE, RESEARCH_ROLE, 170003),  # PG17, not 18
        None,
    ],
)
async def test_validate_research_identity_rejects_identity_mismatch(identity_row):
    conn = FakeConnection([identity_row, (RESEARCH_INSTANCE_NAME, RESEARCH_SCHEMA_VERSION)])
    with pytest.raises(ResearchReaderError) as exc_info:
        await validate_research_identity(conn)
    assert exc_info.value.code == "identity_mismatch"


@pytest.mark.asyncio
async def test_validate_research_identity_rejects_marker_mismatch():
    conn = FakeConnection(
        [
            (RESEARCH_DATABASE, RESEARCH_ROLE, 180005),
            None,
        ]
    )
    with pytest.raises(ResearchReaderError) as exc_info:
        await validate_research_identity(conn)
    assert exc_info.value.code == "identity_mismatch"


@pytest.mark.asyncio
async def test_validate_research_identity_rejects_schema_version_mismatch():
    conn = FakeConnection(
        [
            (RESEARCH_DATABASE, RESEARCH_ROLE, 180005),
            (RESEARCH_INSTANCE_NAME, RESEARCH_SCHEMA_VERSION + 1),
        ]
    )
    with pytest.raises(ResearchReaderError) as exc_info:
        await validate_research_identity(conn)
    assert exc_info.value.code == "identity_mismatch"


@pytest.mark.asyncio
async def test_validate_research_identity_never_leaks_driver_text():
    secret_dsn = "postgresql://research_reader:leaked-secret@127.0.0.1:54790/hft_research"
    conn = RaisingConnection(RuntimeError(f"connection failed: {secret_dsn}"))
    with pytest.raises(ResearchReaderError) as exc_info:
        await validate_research_identity(conn)
    assert exc_info.value.code == "unavailable"
    assert "leaked-secret" not in str(exc_info.value)
    assert secret_dsn not in str(exc_info.value)
