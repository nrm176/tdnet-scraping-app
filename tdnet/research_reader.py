"""Read-only access to the Research DB (`hft_research`) as `research_reader`,
for `tdnet/api.py` and `tdnet/review.py` (Root spec 2026-10-24, research DB
migration slice 7, §3.4 phase B).

The reader URL comes from `RESEARCH_DATABASE_URL` if set, otherwise it is
built in memory from Root's session file (`.research-runtime/session.json`,
mode 0600, `reader_password`) -- mirroring EdinetAPI's
`lib/research_connection.py::build_reader_url` and the slice-2 consumer
pattern in portfolio-manager's `backend/research_db.py` /
`research_market_repository.py`, adapted here to this repo's existing async
SQLAlchemy engine (`tdnet/database.py`) instead of a sync one.

This module never connects to the legacy `tdnet` Postgres DB (the one
`tdnet/database.py`/`tdnet/orm.py` read and write for the scraper pipeline)
and never prints, logs or raises a URL or password.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional, TypeVar
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# Root's session file, read-only from here. Never written to by this repo.
RESEARCH_SESSION_PATH = Path(
    "/Users/nrm176p/GitHub2/hedge-fund-takedo/.research-runtime/session.json"
)

# Host/port as seen from the host (not the Docker-internal hostname), matching
# hedge_research.research_environment.HOST/PORT and compose.research.yaml's
# published "127.0.0.1:54790:5432" -- same as EdinetAPI's research_connection.py.
RESEARCH_HOST = "127.0.0.1"
RESEARCH_PORT = "54790"

RESEARCH_DATABASE = "hft_research"
RESEARCH_ROLE = "research_reader"
RESEARCH_INSTANCE_NAME = "hft_research"
RESEARCH_SCHEMA_VERSION = 1

# Generous timeout for a dev tool's ad-hoc read; keeps a hung connection from
# blocking a request/report indefinitely.
OPERATION_TIMEOUT_SECONDS = 20

T = TypeVar("T")


class ResearchReaderError(RuntimeError):
    """Safe Research reader failure with a stable, machine-readable code.

    Never includes a URL, password or raw driver text.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _load_reader_password(path: Path) -> str:
    """Read `reader_password` from the session file: mode 0600, owned by the
    current user -- mirroring EdinetAPI's `research_connection._load_reader_password`."""
    try:
        info = path.stat()
    except OSError:
        raise ResearchReaderError(
            "session_unavailable", "research session file not found"
        ) from None
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ResearchReaderError(
            "session_unavailable", "research session file must be mode 0600"
        )
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ResearchReaderError(
            "session_unavailable", "research session file is unreadable"
        ) from None
    password = values.get("reader_password") if isinstance(values, dict) else None
    if not isinstance(password, str) or not password:
        raise ResearchReaderError(
            "session_unavailable", "research session file has no reader_password"
        )
    return password


def build_reader_url(
    environ: Optional[Mapping[str, str]] = None,
    session_path: Path = RESEARCH_SESSION_PATH,
) -> str:
    """Return the `research_reader` URL for `hft_research`, in the
    `postgresql+asyncpg://` form `create_async_engine` needs.

    `RESEARCH_DATABASE_URL` wins if set (non-empty, used as-is); otherwise the
    URL is built in memory from the session file. Callers must never print or
    log the return value.
    """
    environ = os.environ if environ is None else environ
    explicit = environ.get("RESEARCH_DATABASE_URL")
    if explicit:
        return explicit
    password = _load_reader_password(Path(session_path))
    return (
        f"postgresql+asyncpg://{RESEARCH_ROLE}:{quote(password, safe='')}"
        f"@{RESEARCH_HOST}:{RESEARCH_PORT}/{RESEARCH_DATABASE}"
    )


def _major_version(server_version_num: Any) -> int:
    try:
        parsed = int(server_version_num)
    except (TypeError, ValueError):
        return -1
    return parsed // 10000 if parsed >= 10000 else parsed


def _first_row(result: Any) -> Any:
    """Read one row from a real SQLAlchemy `CursorResult` or a small test fake."""
    first = getattr(result, "first", None)
    if callable(first):
        return first()
    fetchone = getattr(result, "fetchone", None)
    if callable(fetchone):
        return fetchone()
    return None


def _row_value(row: Any, index: int, key: str) -> Any:
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        mapping = getattr(row, "_mapping", None)
        if mapping is not None:
            return mapping[key]
        return row[key]


async def validate_research_identity(conn: AsyncConnection) -> None:
    """Fail closed unless `conn` is the expected PostgreSQL 18 `research_reader`
    on `hft_research`, following EdinetAPI's `research_connection.validate_research_identity`
    and portfolio-manager's `backend/research_db.py::validate_research_identity`."""
    try:
        identity_result = await conn.execute(
            text(
                "SELECT current_database(), current_user, "
                "current_setting('server_version_num')::int"
            )
        )
        identity = _first_row(identity_result)
        if identity is None:
            raise ResearchReaderError("identity_mismatch", "research identity mismatch")
        database = _row_value(identity, 0, "current_database")
        role = _row_value(identity, 1, "current_user")
        server_version_num = _row_value(identity, 2, "server_version_num")
        if (
            database != RESEARCH_DATABASE
            or role != RESEARCH_ROLE
            or _major_version(server_version_num) != 18
        ):
            raise ResearchReaderError("identity_mismatch", "research identity mismatch")

        marker_result = await conn.execute(
            text(
                "SELECT instance_name, schema_version FROM pipeline.instance_identity "
                "WHERE instance_name = :expected_instance_name"
            ),
            {"expected_instance_name": RESEARCH_INSTANCE_NAME},
        )
        marker = _first_row(marker_result)
        if (
            marker is None
            or _row_value(marker, 0, "instance_name") != RESEARCH_INSTANCE_NAME
            or _row_value(marker, 1, "schema_version") != RESEARCH_SCHEMA_VERSION
        ):
            raise ResearchReaderError("identity_mismatch", "research identity mismatch")
    except ResearchReaderError:
        raise
    except Exception:
        # Never propagate driver text: it can embed a DSN or password.
        raise ResearchReaderError(
            "unavailable", "research identity validation was unavailable"
        ) from None


@lru_cache(maxsize=2)
def _cached_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def get_engine(database_url: Optional[str] = None) -> AsyncEngine:
    """Return the cached engine for the research reader URL.

    Separate from `tdnet/database.py`'s legacy engine -- this is the only
    place in the package that opens a connection to `hft_research`.
    """
    return _cached_engine(database_url or build_reader_url())


async def run_read(
    fn: Callable[[AsyncConnection], Awaitable[T]],
    *,
    database_url: Optional[str] = None,
    engine: Optional[AsyncEngine] = None,
) -> T:
    """Open one `REPEATABLE READ READ ONLY` transaction against the research
    reader, validate its identity, then await `fn(conn)`.

    `engine` lets tests substitute a fake engine/connection (the slice-2
    `Conn`/`Engine` fake pattern in portfolio-manager's
    `tests/test_research_market_repository.py`); production callers leave it
    unset and get the cached real engine.
    """
    try:
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
            selected = engine if engine is not None else get_engine(database_url)
            async with selected.connect() as conn:
                async with conn.begin():
                    await conn.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    )
                    await validate_research_identity(conn)
                    return await fn(conn)
    except ResearchReaderError:
        raise
    except TimeoutError:
        raise ResearchReaderError("unavailable", "research read timed out") from None
    except Exception:
        raise ResearchReaderError("unavailable", "research read was unavailable") from None


async def get_research_connection() -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency: one `REPEATABLE READ READ ONLY`, identity-validated
    connection per request, held open for the handler's queries then closed.

    `tdnet/api.py` depends on this exactly as it used to depend on
    `tdnet/database.py::get_session` for the legacy DB; tests override it via
    `app.dependency_overrides[get_research_connection]` with a fake connection
    the same way `database.get_session` was overridden before.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await validate_research_identity(conn)
            yield conn
