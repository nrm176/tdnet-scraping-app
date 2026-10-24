"""FastAPI application for persisted TDnet disclosures.

Reads `analysis.tdnet_disclosure_history_v1` / `tdnet_parse_text_history_v1`
/ `tdnet_report_tag_history_v1` (SQL044) as `research_reader`, instead of the
legacy `tdnet` DB's ORM tables (Root spec 2026-10-24, research DB migration
slice 7, §3.4 phase B). This app never opens a connection to the legacy DB.

Response shapes are unchanged from before the cutover. Where a view doesn't
carry data a field used to come from, that field is populated with an empty
list, `None`, zero, or a documented default -- never by falling back to a
legacy read. `tdnet/history_queries.py`'s module docstring is the one place
that spells out exactly what each view does and doesn't carry; the comments
below say which gap applies at each call site.
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
        # analysis.tdnet_report_tag_history_v1 carries no per-assignment
        # file/parse-job linkage or evidence_json (history_queries.py's
        # module docstring) -- null rather than a legacy read.
        file_id=None,
        parse_job_id=None,
        evidence_json=None,
        tagger_name=row["tagger_name"],
        tagger_version=row["tagger_version"],
    )


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
    tag_rows = await history_queries.fetch_tags_for_disclosures(conn, [disclosure_id])
    return DisclosureLineageResponse(
        disclosure=_disclosure_from_row(row),
        tags=[_tag_assignment_from_row(tag_row) for tag_row in tag_rows],
        # No per-file identity/download/parse-job status survives into the
        # history views (history_queries.py's module docstring), so file
        # lineage can't be rebuilt; empty rather than a legacy read.
        files=[],
    )


@app.get("/disclosures/{disclosure_id}/parser-status", response_model=ParserStatusResponse)
async def read_disclosure_parser_status(
    disclosure_id: str,
    conn: Annotated[AsyncConnection, Depends(get_research_connection)],
) -> ParserStatusResponse:
    row = await history_queries.fetch_disclosure(conn, disclosure_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Disclosure not found")
    # Same files=[] limitation as /lineage above.
    return ParserStatusResponse(disclosure_id=disclosure_id, files=[])


@app.get("/disclosure-files", response_model=DisclosureFileListResponse)
async def list_disclosure_files(
    download_status: str | None = None,
    file_type: str | None = None,
    disclosure_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisclosureFileListResponse:
    # No view exposes per-file identity or download metadata at all
    # (history_queries.py's module docstring) -- this endpoint can no longer
    # be served from the research reader, so it always returns empty rather
    # than reading the legacy DB.
    return DisclosureFileListResponse(total=0, limit=limit, offset=offset, files=[])


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
    disclosure_ids = [row["disclosure_id"] for row in rows]
    tag_rows = await history_queries.fetch_tags_for_disclosures(conn, disclosure_ids)
    tags_by_disclosure: dict[str, list[ReportTagAssignmentLineageResponse]] = defaultdict(list)
    for tag_row in tag_rows:
        tags_by_disclosure[tag_row["disclosure_id"]].append(_tag_assignment_from_row(tag_row))

    timeline_items: list[CompanyTimelineDisclosureResponse] = []
    for row in rows:
        has_pdf = row["pdf_path"] is not None
        has_xbrl = bool(row["has_xbrl_file"])
        has_parse_text = bool(row["has_parse_text"])
        pdf_completed = row["pdf_sha256"] is not None
        # No view exposes per-file/per-parse-job identity (history_queries.py's
        # module docstring); these counts are derived from the
        # existence/completion booleans the disclosure-history view does
        # carry, rather than from a legacy read. has_xbrl_file is an EXISTS
        # check (not a download-success check -- the view has no xbrl
        # sha256), so the xbrl file is counted as "completed" once it exists.
        file_count = (1 if has_pdf else 0) + (1 if has_xbrl else 0)
        completed_file_count = (1 if pdf_completed else 0) + (1 if has_xbrl else 0)
        parse_job_count = 1 if has_parse_text else 0
        completed_parse_job_count = parse_job_count
        timeline_items.append(
            CompanyTimelineDisclosureResponse(
                disclosure=_disclosure_from_row(row),
                tags=tags_by_disclosure[row["disclosure_id"]],
                file_count=file_count,
                completed_file_count=completed_file_count,
                parse_job_count=parse_job_count,
                completed_parse_job_count=completed_parse_job_count,
                # document_analysis_result has no SQL044 view at all.
                analysis_result_count=0,
                files=[],
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
    total = await history_queries.fetch_parser_totals(conn)
    breakdown = await history_queries.fetch_parser_breakdown(conn)
    # document_parse_texts only ever held successfully parsed output, and no
    # view exposes document_parse_jobs' status at all -- so "failed",
    # "running" and "pending" are unobservable post-migration, and every
    # count below is really just "completed" (history_queries.py's module
    # docstring).
    return ParserQualityResponse(
        total_jobs=total,
        completed_jobs=total,
        failed_jobs=0,
        running_jobs=0,
        pending_jobs=0,
        parse_texts=total,
        status_counts=(
            [ParserQualityStatusCount(parse_status="completed", count=total)] if total else []
        ),
        parsers=[
            ParserQualityParserResponse(
                parser_name=row["parser_name"],
                parser_version=row["parser_version"],
                total_jobs=row["parse_texts"],
                completed_jobs=row["parse_texts"],
                failed_jobs=0,
                running_jobs=0,
                pending_jobs=0,
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
                # analysis.tdnet_report_tag_history_v1 carries no
                # report_tag.active; every migrated tag was active as of the
                # 2026-09-05 freeze, so True stands in for a legacy read.
                # Note: this view is assignments joined to tags, so a tag
                # with zero assignments (none, as far as the 2026-10-24
                # survey found) would not appear here at all.
                active=True,
                assignment_count=row["assignment_count"],
                primary_count=row["primary_count"],
            )
            for row in rows
        ]
    )
