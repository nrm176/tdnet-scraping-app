"""Async SQLAlchemy setup for the legacy tdnet-postgres database.

PARKED (2026-10-25): the legacy tdnet-postgres database this module talks to
is being retired. TDnet disclosure history now lives in the research
database's `raw_tdnet_history` tables (see the hedge-fund-takedo repo's
docs/runbooks/tdnet-history.md). Moving document download/parse onto the
research DB is a separate future project tracked as Root issue #50.

Until that lands, every entry point into this module -- the `tdnet` CLI's
scrape(--persist)/list/download/parse/ocr/parse-ixbrl/persist-parse-text/
tag-reports/analyze-financials/list-tags/pipeline-run commands (tdnet/cli.py),
and the app/backend review workbench (app/backend/main.py) -- is disabled by
default: `init_db()` and taking a session from `SessionLocal()`/
`get_session()` raise `LegacyLocalDbParkedError`. The code is kept, not
deleted, so it can be re-enabled deliberately: set
TDNET_LEGACY_LOCAL_ENABLED=1 to do that locally.

Importing this module never creates the legacy engine or opens a connection;
`get_engine()` builds it lazily, and only once the guard has passed.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings
from .orm import Base

LEGACY_LOCAL_DB_PARKED_MESSAGE = (
    "The legacy tdnet-postgres database is retired (2026-10-25). TDnet "
    "disclosure history now lives in the research database's "
    "raw_tdnet_history (see docs/runbooks/tdnet-history.md in the "
    "hedge-fund-takedo repo). Document download/parse against this local "
    "database will move to the research DB under Root issue #50. This code "
    "path is parked, not deleted -- set TDNET_LEGACY_LOCAL_ENABLED=1 to "
    "re-enable it deliberately."
)


class LegacyLocalDbParkedError(RuntimeError):
    """Raised when legacy tdnet-postgres access is attempted while parked."""

    def __init__(self, message: str = LEGACY_LOCAL_DB_PARKED_MESSAGE) -> None:
        super().__init__(message)


def require_legacy_local_enabled() -> None:
    """Single guard for every legacy tdnet-postgres entry point.

    Raises `LegacyLocalDbParkedError` unless TDNET_LEGACY_LOCAL_ENABLED is
    set truthy. Call this before touching the legacy engine/session/tables.
    """
    if not get_settings().legacy_local_db_enabled:
        raise LegacyLocalDbParkedError()


def create_engine(database_url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        database_url or get_settings().database_url,
        pool_pre_ping=True,
    )


_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the shared legacy engine, built lazily on first enabled use.

    Guarded by `require_legacy_local_enabled()`; importing this module never
    creates the engine.
    """
    require_legacy_local_enabled()
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


class _SessionLocal:
    """Callable sessionmaker proxy, parked until opted in.

    Mirrors the previous module-level `async_sessionmaker` instance so
    existing call sites (`async with SessionLocal() as session:`) are
    unchanged, but defers engine/sessionmaker construction behind the guard.
    """

    _sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def __call__(self) -> AsyncSession:
        require_legacy_local_enabled()
        if self._sessionmaker is None:
            self._sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
        return self._sessionmaker()


SessionLocal = _SessionLocal()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db(target_engine: AsyncEngine | None = None) -> None:
    """Create database tables for local/dev use.

    A later Alembic migration layer can own schema evolution; this keeps v1
    runnable with only Docker Postgres and the package installed.
    """
    require_legacy_local_enabled()
    bind = target_engine or get_engine()
    async with bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
