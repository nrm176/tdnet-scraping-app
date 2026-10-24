"""FastAPI backend for parsed TDnet text search and review."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.routers.pipeline import router as pipeline_router
from app.backend.routers.search import router as search_router
from app.backend.schemas import HealthResponse
from app.backend.search_setup import ensure_postgres_search_support
from tdnet.config import get_settings
from tdnet.database import get_engine, get_session, init_db, require_legacy_local_enabled

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # app/backend is the legacy tdnet-postgres review workbench (search,
    # parser-quality, pipeline-run history, manual review decisions, page
    # images) -- parked 2026-10-25 alongside the rest of the --legacy-local
    # pipeline, see tdnet/database.py. Fails fast here, before any legacy DB
    # access, unless TDNET_LEGACY_LOCAL_ENABLED re-enables it deliberately.
    require_legacy_local_enabled()
    await init_db()
    await ensure_postgres_search_support(get_engine())
    yield


app = FastAPI(
    title="TDnet Review App API",
    version=settings.api_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> HealthResponse:
    await session.execute(text("select 1"))
    return HealthResponse(status="ok", database="ok")


app.include_router(search_router)
app.include_router(pipeline_router)
