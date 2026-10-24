"""FastAPI application for persisted TDnet disclosures.

Reads the research DB's migrated TDnet history (`raw_tdnet_history.*` and
the SQL044 `analysis.tdnet_disclosure_history_v1` view) as `research_reader`,
instead of the legacy `tdnet` DB's ORM tables (Root spec 2026-10-24, research
DB migration slice 7, §3.4 phase B). This app never opens a connection to
the legacy DB. `research_reader` has `SELECT` on every `raw_tdnet_history`
table, so every endpoint here returns the same data it did against legacy --
see `tdnet/history_queries.py`'s module docstring for exactly which table
backs which field. The one legacy table that was never migrated,
`document_parse_reviews`, was already empty (ruling T2), so there is nothing
to lose there either.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from . import history_queries
from .config import get_settings
from .models import TdnetDisclosure
from .research_reader import get_research_connection

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # The research reader engine is created lazily on first connection
    # (research_reader.get_engine); there is no legacy-DB table setup to run
    # here any more.
    yield


app = FastAPI(title=settings.api_title, version=settings.api_version, lifespan=lifespan)


class HealthResponse(BaseModel):
    status: str
    database: str


class DisclosureListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    disclosures: list[TdnetDisclosure]


class DisclosureFileResponse(BaseModel):
    id: int
    disclosure_id: str
    file_type: str
    source_url: str
    source_file_id: str
    storage_bucket: str
    storage_path: str
    content_type: str | None
    file_size_bytes: int | None
    sha256: str | None
    download_status: str
    download_attempts: int
    downloaded_at: datetime | None
    last_download_error: str | None


class DisclosureFileListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    files: list[DisclosureFileResponse]


class ParseTextSummaryResponse(BaseModel):
    id: int
    parse_job_id: int
    page_count: int = Field(ge=0)
    char_count: int = Field(ge=0)
    content_sha256: str
    created_at: datetime
    updated_at: datetime


class ParseJobLineageResponse(BaseModel):
    id: int
    file_id: int
    parser_name: str
    parser_version: str
    parse_status: str
    parse_attempts: int = Field(ge=0)
    text_path: str | None
    text_sha256: str | None
    parsed_at: datetime | None
    last_parse_error: str | None
    parse_text: ParseTextSummaryResponse | None = None


class AnalysisResultResponse(BaseModel):
    id: int
    file_id: int
    parse_job_id: int | None
    analysis_type: str
    analyzer_name: str
    analyzer_version: str
    status: str
    result_json: dict | None
    result_text: str | None
    analyzed_at: datetime | None
    last_analysis_error: str | None
    created_at: datetime
    updated_at: datetime


class ReportTagAssignmentLineageResponse(BaseModel):
    slug: str
    label_ja: str
    label_en: str
    is_primary: bool
    confidence: float = Field(ge=0, le=1)
    source: str
    file_id: int | None
    parse_job_id: int | None
    evidence_json: dict | None
    tagger_name: str
    tagger_version: str


class DisclosureFileLineageResponse(DisclosureFileResponse):
    parse_jobs: list[ParseJobLineageResponse] = Field(default_factory=list)
    analysis_results: list[AnalysisResultResponse] = Field(default_factory=list)


class DisclosureLineageResponse(BaseModel):
    disclosure: TdnetDisclosure
    tags: list[ReportTagAssignmentLineageResponse] = Field(default_factory=list)
    files: list[DisclosureFileLineageResponse] = Field(default_factory=list)


class ParserStatusResponse(BaseModel):
    disclosure_id: str
    files: list[DisclosureFileLineageResponse] = Field(default_factory=list)


class CompanyTimelineDisclosureResponse(BaseModel):
    disclosure: TdnetDisclosure
    tags: list[ReportTagAssignmentLineageResponse] = Field(default_factory=list)
    file_count: int = Field(ge=0)
    completed_file_count: int = Field(ge=0)
    parse_job_count: int = Field(ge=0)
    completed_parse_job_count: int = Field(ge=0)
    analysis_result_count: int = Field(ge=0)
    files: list[DisclosureFileLineageResponse] = Field(default_factory=list)


class CompanyTimelineResponse(BaseModel):
    code: str
    company_name: str | None
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    disclosures: list[CompanyTimelineDisclosureResponse] = Field(default_factory=list)


class ParserQualityStatusCount(BaseModel):
    parse_status: str
    count: int = Field(ge=0)


class ParserQualityParserResponse(BaseModel):
    parser_name: str
    parser_version: str
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)
    average_char_count: float | None


class ParserQualityResponse(BaseModel):
    total_jobs: int = Field(ge=0)
    completed_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    pending_jobs: int = Field(ge=0)
    parse_texts: int = Field(ge=0)
    status_counts: list[ParserQualityStatusCount] = Field(default_factory=list)
    parsers: list[ParserQualityParserResponse] = Field(default_factory=list)


class ReportTagSummaryResponse(BaseModel):
    slug: str
    label_ja: str
    label_en: str
    description: str
    priority: int
    active: bool
    assignment_count: int = Field(ge=0)
    primary_count: int = Field(ge=0)


class ReportTagSummaryListResponse(BaseModel):
    tags: list[ReportTagSummaryResponse] = Field(default_factory=list)


def _disclosure_from_row(row: dict) -> TdnetDisclosure:
    return TdnetDisclosure(
        time=row["time"],
        code=row["code"],
        name=row["name"],
        title=row["title"],
        pdf_url=row["pdf_url"],
        xbrl_available=row["xbrl_available"],
        xbrl_url=row["xbrl_url"],
        place=row["place"],
        history=row["history"],
        disclosure_date=row["disclosure_date"],
    )


def _tag_assignment_from_row(row: dict) -> ReportTagAssignmentLineageResponse:
    return ReportTagAssignmentLineageResponse(
        slug=row["tag_slug"],
        label_ja=row["label_ja"],
        label_en=row["label_en"],
        is_primary=row["is_primary"],
        confidence=row["confidence"],
        source=row["source"],
        file_id=row["file_id"],
        parse_job_id=row["parse_job_id"],
        evidence_json=row["evidence_json"],
        tagger_name=row["tagger_name"],
        tagger_version=row["tagger_version"],
    )


def _file_response_from_row(row: dict) -> DisclosureFileResponse:
    return DisclosureFileResponse(
        id=row["id"],
        disclosure_id=row["disclosure_id"],
        file_type=row["file_type"],
        source_url=row["source_url"],
        source_file_id=row["source_file_id"],
        storage_bucket=row["storage_bucket"],
        storage_path=row["storage_path"],
        content_type=row["content_type"],
        file_size_bytes=row["file_size_bytes"],
        sha256=row["sha256"],
        download_status=row["download_status"],
        download_attempts=row["download_attempts"],
        downloaded_at=row["downloaded_at"],
        last_download_error=row["last_download_error"],
    )


def _parse_text_summary_from_row(row: dict) -> ParseTextSummaryResponse | None:
    if row["text_id"] is None:
        return None
    return ParseTextSummaryResponse(
        id=row["text_id"],
        parse_job_id=row["id"],
        page_count=row["text_page_count"],
        char_count=row["text_char_count"],
        content_sha256=row["text_content_sha256"],
        created_at=row["text_created_at"],
        updated_at=row["text_updated_at"],
    )


def _parse_job_from_row(row: dict) -> ParseJobLineageResponse:
    return ParseJobLineageResponse(
        id=row["id"],
        file_id=row["file_id"],
        parser_name=row["parser_name"],
        parser_version=row["parser_version"],
        parse_status=row["parse_status"],
        parse_attempts=row["parse_attempts"],
        text_path=row["text_path"],
        text_sha256=row["text_sha256"],
        parsed_at=row["parsed_at"],
        last_parse_error=row["last_parse_error"],
        parse_text=_parse_text_summary_from_row(row),
    )


def _analysis_result_from_row(row: dict) -> AnalysisResultResponse:
    return AnalysisResultResponse(
        id=row["id"],
        file_id=row["file_id"],
        parse_job_id=row["parse_job_id"],
        analysis_type=row["analysis_type"],
        analyzer_name=row["analyzer_name"],
        analyzer_version=row["analyzer_version"],
        status=row["status"],
        result_json=row["result_json"],
        result_text=row["result_text"],
        analyzed_at=row["analyzed_at"],
        last_analysis_error=row["last_analysis_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _load_lineage(conn: AsyncConnection, disclosure_rows: list[dict]) -> dict[str, DisclosureLineageResponse]:
    """Mirror the legacy ORM's `_load_lineage`: one query per table, batched
    across every disclosure in `disclosure_rows`, then assembled in Python --
    just reading `raw_tdnet_history.*` instead of the ORM session."""
    if not disclosure_rows:
        return {}

    disclosure_ids = [row["disclosure_id"] for row in disclosure_rows]
    file_rows = await history_queries.fetch_files_for_disclosures(conn, disclosure_ids)
    file_legacy_ids = [row["id"] for row in file_rows]

    parse_jobs_by_file: dict[int, list[ParseJobLineageResponse]] = defaultdict(list)
    analysis_results_by_file: dict[int, list[AnalysisResultResponse]] = defaultdict(list)
    if file_legacy_ids:
        for parse_job_row in await history_queries.fetch_parse_jobs_for_files(conn, file_legacy_ids):
            parse_jobs_by_file[parse_job_row["file_id"]].append(_parse_job_from_row(parse_job_row))
        for analysis_row in await history_queries.fetch_analysis_results_for_files(conn, file_legacy_ids):
            analysis_results_by_file[analysis_row["file_id"]].append(_analysis_result_from_row(analysis_row))

    files_by_disclosure: dict[str, list[DisclosureFileLineageResponse]] = defaultdict(list)
    for file_row in file_rows:
        files_by_disclosure[file_row["disclosure_id"]].append(
            DisclosureFileLineageResponse(
                **_file_response_from_row(file_row).model_dump(),
                parse_jobs=parse_jobs_by_file[file_row["id"]],
                analysis_results=analysis_results_by_file[file_row["id"]],
            )
        )

    tag_rows = await history_queries.fetch_tags_for_disclosures(conn, disclosure_ids)
    tags_by_disclosure: dict[str, list[ReportTagAssignmentLineageResponse]] = defaultdict(list)
    for tag_row in tag_rows:
        tags_by_disclosure[tag_row["disclosure_id"]].append(_tag_assignment_from_row(tag_row))

    return {
        disclosure_row["disclosure_id"]: DisclosureLineageResponse(
            disclosure=_disclosure_from_row(disclosure_row),
            tags=tags_by_disclosure[disclosure_row["disclosure_id"]],
            files=files_by_disclosure[disclosure_row["disclosure_id"]],
        )
        for disclosure_row in disclosure_rows
    }


@app.get("/health", response_model=HealthResponse)
async def health(conn: Annotated[AsyncConnection, Depends(get_research_connection)]) -> HealthResponse:
    await conn.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="ok")


@app.get("/disclosures", response_model=DisclosureListResponse)
async def list_disclosures(
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
    disclosure_date: Annotated[date | None, Query(alias="date")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    code: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureListResponse:
    total = await history_queries.count_disclosures(
        conn,
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    rows = await history_queries.fetch_disclosures(
        conn,
        disclosure_date=disclosure_date,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    return DisclosureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        disclosures=[_disclosure_from_row(row) for row in rows],
    )


@app.get("/disclosures/{disclosure_id}", response_model=TdnetDisclosure)
async def read_disclosure(
    disclosure_id: str,
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
) -> TdnetDisclosure:
    row = await history_queries.fetch_disclosure(conn, disclosure_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    return _disclosure_from_row(row)


@app.get("/disclosures/{disclosure_id}/lineage", response_model=DisclosureLineageResponse)
async def read_disclosure_lineage(
    disclosure_id: str,
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
) -> DisclosureLineageResponse:
    row = await history_queries.fetch_disclosure(conn, disclosure_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    lineage = await _load_lineage(conn, [row])
    return lineage[disclosure_id]


@app.get("/disclosures/{disclosure_id}/parser-status", response_model=ParserStatusResponse)
async def read_disclosure_parser_status(
    disclosure_id: str,
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
) -> ParserStatusResponse:
    row = await history_queries.fetch_disclosure(conn, disclosure_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    lineage = await _load_lineage(conn, [row])
    return ParserStatusResponse(disclosure_id=disclosure_id, files=lineage[disclosure_id].files)


@app.get("/disclosure-files", response_model=DisclosureFileListResponse)
async def list_disclosure_files(
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
    download_status: str | None = None,
    file_type: str | None = None,
    disclosure_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureFileListResponse:
    total = await history_queries.count_disclosure_files(
        conn,
        disclosure_id=disclosure_id,
        download_status=download_status,
        file_type=file_type,
    )
    rows = await history_queries.fetch_disclosure_files(
        conn,
        disclosure_id=disclosure_id,
        download_status=download_status,
        file_type=file_type,
        limit=limit,
        offset=offset,
    )
    return DisclosureFileListResponse(
        total=total,
        limit=limit,
        offset=offset,
        files=[_file_response_from_row(row) for row in rows],
    )


@app.get("/companies/{code}/disclosures", response_model=DisclosureListResponse)
async def list_company_disclosures(
    code: str,
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureListResponse:
    total = await history_queries.count_disclosures(
        conn,
        date_from=date_from,
        date_to=date_to,
        code=code,
    )
    rows = await history_queries.fetch_disclosures(
        conn,
        date_from=date_from,
        date_to=date_to,
        code=code,
        limit=limit,
        offset=offset,
    )
    return DisclosureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        disclosures=[_disclosure_from_row(row) for row in rows],
    )


@app.get("/companies/{code}/timeline", response_model=CompanyTimelineResponse)
async def read_company_timeline(
    code: str,
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompanyTimelineResponse:
    normalized_code = code.strip().upper()
    total = await history_queries.count_disclosures(
        conn,
        date_from=date_from,
        date_to=date_to,
        code=normalized_code,
    )
    rows = await history_queries.fetch_disclosures(
        conn,
        date_from=date_from,
        date_to=date_to,
        code=normalized_code,
        limit=limit,
        offset=offset,
    )
    lineage_by_disclosure = await _load_lineage(conn, rows)

    timeline_items: list[CompanyTimelineDisclosureResponse] = []
    for row in rows:
        lineage = lineage_by_disclosure[row["disclosure_id"]]
        parse_job_count = sum(len(file.parse_jobs) for file in lineage.files)
        completed_parse_job_count = sum(
            1 for file in lineage.files for parse_job in file.parse_jobs if parse_job.parse_status == "completed"
        )
        analysis_result_count = sum(len(file.analysis_results) for file in lineage.files)
        timeline_items.append(
            CompanyTimelineDisclosureResponse(
                disclosure=lineage.disclosure,
                tags=lineage.tags,
                file_count=len(lineage.files),
                completed_file_count=sum(1 for file in lineage.files if file.download_status == "completed"),
                parse_job_count=parse_job_count,
                completed_parse_job_count=completed_parse_job_count,
                analysis_result_count=analysis_result_count,
                files=lineage.files,
            )
        )
    return CompanyTimelineResponse(
        code=normalized_code,
        company_name=timeline_items[0].disclosure.name if timeline_items else None,
        total=total,
        limit=limit,
        offset=offset,
        disclosures=timeline_items,
    )


@app.get("/parser-quality", response_model=ParserQualityResponse)
async def read_parser_quality(
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
) -> ParserQualityResponse:
    totals = await history_queries.fetch_parser_totals(conn)
    status_rows = await history_queries.fetch_parser_status_counts(conn)
    breakdown = await history_queries.fetch_parser_breakdown(conn)
    return ParserQualityResponse(
        total_jobs=totals["total_jobs"],
        completed_jobs=totals["completed_jobs"],
        failed_jobs=totals["failed_jobs"],
        running_jobs=totals["running_jobs"],
        pending_jobs=totals["pending_jobs"],
        parse_texts=totals["parse_texts"],
        status_counts=[
            ParserQualityStatusCount(parse_status=row["parse_status"], count=row["count"])
            for row in status_rows
        ],
        parsers=[
            ParserQualityParserResponse(
                parser_name=row["parser_name"],
                parser_version=row["parser_version"],
                total_jobs=row["total_jobs"],
                completed_jobs=row["completed_jobs"],
                failed_jobs=row["failed_jobs"],
                running_jobs=row["running_jobs"],
                pending_jobs=row["pending_jobs"],
                parse_texts=row["parse_texts"],
                average_char_count=(
                    float(row["average_char_count"]) if row["average_char_count"] is not None else None
                ),
            )
            for row in breakdown
        ],
    )


@app.get("/tags", response_model=ReportTagSummaryListResponse)
async def read_tag_summaries(
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
) -> ReportTagSummaryListResponse:
    rows = await history_queries.fetch_tag_summaries(conn)
    return ReportTagSummaryListResponse(
        tags=[
            ReportTagSummaryResponse(
                slug=row["slug"],
                label_ja=row["label_ja"],
                label_en=row["label_en"],
                description=row["description"],
                priority=row["priority"],
                active=row["active"],
                assignment_count=row["assignment_count"],
                primary_count=row["primary_count"],
            )
            for row in rows
        ]
    )
