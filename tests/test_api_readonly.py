"""tdnet/api.py reads raw_tdnet_history.* (and the SQL044
analysis.tdnet_disclosure_history_v1 view for plain disclosure lookups) as
research_reader (Root spec 2026-10-24, research DB migration slice 7,
§3.4 phase B) -- these tests exercise it with a fake research connection
(the slice-2 Conn/Result fake pattern), never a live or legacy database.
`tdnet.api.get_research_connection` is overridden the same way
`tdnet.api.get_session` used to be overridden for the legacy ORM session.

Every response field this suite checks matches what the legacy ORM path
produced for the same fixture data (see PR discussion: research_reader has
SELECT on every raw_tdnet_history table, so there is no functional loss --
only `file_id`/`parse_job_id` etc. are now `legacy_id`/`*_legacy_id`
columns instead of ORM auto-increment PKs, which is why the fakes below use
the same integers for both)."""
from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256

import pytest
from httpx import ASGITransport, AsyncClient

from tdnet import api as tdnet_api
from tdnet import history_queries


class Result:
    def __init__(self, rows=None, first=None):
        self._rows = rows if rows is not None else []
        self._first = first

    def mappings(self):
        return self._rows

    def first(self):
        return self._first


class FakeConn:
    """Routes a query to the first canned result whose substring matches."""

    def __init__(self, responses):
        self.responses = responses
        self.sql: list[str] = []
        self.params: list[dict | None] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        self.params.append(params)
        for substring, result in self.responses:
            if substring in sql:
                return result
        return Result(rows=[])


DISCLOSURE_ROW = {
    "disclosure_id": "abc123def4567890",
    "disclosure_date": date(2026, 4, 20),
    "time": "15:30",
    "code": "85600",
    "name": "宮崎太銀",
    "title": "業績予想の修正に関するお知らせ",
    "pdf_url": "https://www.release.tdnet.info/inbs/140120260414503449.pdf",
    "xbrl_available": False,
    "xbrl_url": None,
    "place": "福",
    "history": "",
}

# TdnetDisclosure.id is a computed sha256 of code/date/pdf_url/time (unchanged
# by this cutover) -- not DISCLOSURE_ROW's own "disclosure_id", which is only
# the fake's stand-in for the view's column of the same name.
DISCLOSURE_ROW_COMPUTED_ID = sha256(
    f"{DISCLOSURE_ROW['code']}_{DISCLOSURE_ROW['disclosure_date']}_"
    f"{DISCLOSURE_ROW['pdf_url']}_{DISCLOSURE_ROW['time']}".encode("utf-8")
).hexdigest()[:16]

FILE_PDF_ROW = {
    "id": 1,
    "disclosure_id": "abc123def4567890",
    "file_type": "pdf",
    "source_url": "https://www.release.tdnet.info/inbs/140120260414503449.pdf",
    "source_file_id": "140120260414503449",
    "storage_bucket": "tdnet-forecast-correction",
    "storage_path": "/Volumes/yakushimachi/Downloads/tdnet/140120260414503449.pdf",
    "content_type": "application/pdf",
    "file_size_bytes": 10,
    "sha256": "a" * 64,
    "download_status": "completed",
    "download_attempts": 1,
    "downloaded_at": datetime(2026, 4, 20, 15, 31, 0),
    "last_download_error": None,
}

PARSE_JOB_ROW = {
    "id": 10,
    "file_id": 1,
    "parser_name": "apple-vision-ocr",
    "parser_version": "ocr-version",
    "parse_status": "completed",
    "parse_attempts": 1,
    "text_path": "/tmp/parsed.md",
    "text_sha256": "b" * 64,
    "parsed_at": datetime(2026, 4, 20, 15, 32, 0),
    "last_parse_error": None,
    "text_id": 100,
    "text_page_count": 1,
    "text_char_count": 42,
    "text_content_sha256": "c" * 64,
    "text_created_at": datetime(2026, 4, 20, 15, 33, 0),
    "text_updated_at": datetime(2026, 4, 20, 15, 33, 0),
}

ANALYSIS_RESULT_ROW = {
    "id": 1000,
    "file_id": 1,
    "parse_job_id": 10,
    "analysis_type": "financial_facts",
    "analyzer_name": "tdnet-financial-facts",
    "analyzer_version": "test",
    "status": "completed",
    "result_json": {"facts": [{"label": "sales", "value": "18,000"}]},
    "result_text": "sales=18,000",
    "analyzed_at": datetime(2026, 4, 20, 15, 34, 0),
    "last_analysis_error": None,
    "created_at": datetime(2026, 4, 20, 15, 34, 0),
    "updated_at": datetime(2026, 4, 20, 15, 34, 0),
}

TAG_ROW = {
    "disclosure_id": "abc123def4567890",
    "tag_slug": "forecast_revision",
    "label_ja": "業績予想・差異",
    "label_en": "Forecast revision or variance",
    "is_primary": True,
    "confidence": 0.95,
    "source": "deterministic",
    "file_id": 1,
    "parse_job_id": 10,
    "evidence_json": {"title": ["業績予想"]},
    "tagger_name": "tdnet-tagging",
    "tagger_version": "test",
}

TAG_SUMMARY_ROW = {
    "slug": "forecast_revision",
    "label_ja": "業績予想・差異",
    "label_en": "Forecast revision or variance",
    "description": "Forecast revisions and differences between forecasts and results.",
    "priority": 30,
    "active": True,
    "assignment_count": 1,
    "primary_count": 1,
}

# (total_jobs, completed_jobs, failed_jobs, running_jobs, pending_jobs, parse_texts)
PARSER_TOTALS_ROW = (1, 1, 0, 0, 0, 1)

PARSER_STATUS_COUNT_ROW = {"parse_status": "completed", "count": 1}

PARSER_BREAKDOWN_ROW = {
    "parser_name": "apple-vision-ocr",
    "parser_version": "ocr-version",
    "total_jobs": 1,
    "completed_jobs": 1,
    "failed_jobs": 0,
    "running_jobs": 0,
    "pending_jobs": 0,
    "parse_texts": 1,
    "average_char_count": 42.0,
}


def _fake_responses():
    # Ordered most-specific-substring first so a query never matches a
    # broader response meant for a different endpoint.
    return [
        ("WHERE disclosure_id = :disclosure_id", Result(rows=[DISCLOSURE_ROW])),
        ("ORDER BY disclosure_id ASC, file_type ASC, legacy_id ASC", Result(rows=[FILE_PDF_ROW])),
        ("ORDER BY updated_at DESC", Result(rows=[FILE_PDF_ROW])),
        (f"SELECT count(*) FROM {history_queries.RAW_FILE}", Result(first=(1,))),
        ("ORDER BY pj.file_legacy_id ASC", Result(rows=[PARSE_JOB_ROW])),
        ("ORDER BY file_legacy_id ASC, analysis_type ASC, legacy_id ASC", Result(rows=[ANALYSIS_RESULT_ROW])),
        ("a.disclosure_id ASC, a.is_primary DESC", Result(rows=[TAG_ROW])),
        ("GROUP BY t.slug, t.label_ja", Result(rows=[TAG_SUMMARY_ROW])),
        ("GROUP BY pj.parser_name, pj.parser_version", Result(rows=[PARSER_BREAKDOWN_ROW])),
        ("GROUP BY parse_status ORDER BY parse_status", Result(rows=[PARSER_STATUS_COUNT_ROW])),
        ("SELECT count(pj.legacy_id) AS total_jobs", Result(first=PARSER_TOTALS_ROW)),
        (f"SELECT count(*) FROM {history_queries.DISCLOSURE_VIEW}", Result(first=(1,))),
        ("ORDER BY disclosure_date DESC, time DESC, code ASC", Result(rows=[DISCLOSURE_ROW])),
    ]


@pytest.fixture
def client_and_conn():
    conn = FakeConn(_fake_responses())

    async def override_get_research_connection():
        yield conn

    tdnet_api.app.dependency_overrides[tdnet_api.get_research_connection] = override_get_research_connection
    transport = ASGITransport(app=tdnet_api.app)
    try:
        yield AsyncClient(transport=transport, base_url="http://test"), conn
    finally:
        tdnet_api.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.asyncio
async def test_list_disclosures(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/disclosures?code=85600")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["disclosures"][0]["code"] == "85600"
    assert body["disclosures"][0]["id"] == DISCLOSURE_ROW_COMPUTED_ID


@pytest.mark.asyncio
async def test_read_disclosure_found_and_missing(client_and_conn):
    client, conn = client_and_conn
    async with client as c:
        found = await c.get("/disclosures/abc123def4567890")
        conn.responses.insert(0, ("WHERE disclosure_id = :disclosure_id", Result(rows=[])))
        missing = await c.get("/disclosures/does-not-exist")
    assert found.status_code == 200
    assert found.json()["code"] == "85600"
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_lineage_has_full_fidelity_files_parse_jobs_analysis_and_tags(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/disclosures/abc123def4567890/lineage")
    assert response.status_code == 200
    body = response.json()
    assert body["disclosure"]["code"] == "85600"

    tag = body["tags"][0]
    assert tag["slug"] == "forecast_revision"
    # raw_tdnet_history.report_tag_assignment carries file_legacy_id/
    # parse_job_legacy_id/evidence_json, unlike the SQL044 view.
    assert tag["file_id"] == 1
    assert tag["parse_job_id"] == 10
    assert tag["evidence_json"] == {"title": ["業績予想"]}

    assert len(body["files"]) == 1
    file_entry = body["files"][0]
    assert file_entry["id"] == 1
    assert file_entry["source_url"] == FILE_PDF_ROW["source_url"]
    assert file_entry["download_status"] == "completed"
    assert len(file_entry["parse_jobs"]) == 1
    parse_job = file_entry["parse_jobs"][0]
    assert parse_job["id"] == 10
    assert parse_job["parser_name"] == "apple-vision-ocr"
    assert parse_job["parse_text"]["id"] == 100
    assert parse_job["parse_text"]["char_count"] == 42
    assert len(file_entry["analysis_results"]) == 1
    assert file_entry["analysis_results"][0]["analysis_type"] == "financial_facts"


@pytest.mark.asyncio
async def test_parser_status_carries_the_same_files_as_lineage(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/disclosures/abc123def4567890/parser-status")
    assert response.status_code == 200
    body = response.json()
    assert body["disclosure_id"] == "abc123def4567890"
    assert len(body["files"]) == 1
    assert body["files"][0]["parse_jobs"][0]["parser_name"] == "apple-vision-ocr"


@pytest.mark.asyncio
async def test_disclosure_files_reads_the_raw_file_table(client_and_conn):
    client, conn = client_and_conn
    async with client as c:
        response = await c.get("/disclosure-files?download_status=completed")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["files"][0]["id"] == 1
    assert body["files"][0]["source_file_id"] == "140120260414503449"
    assert any(
        p is not None and p.get("download_status") == "completed" for p in conn.params
    )


@pytest.mark.asyncio
async def test_company_disclosures(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/companies/85600/disclosures")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["disclosures"][0]["code"] == "85600"


@pytest.mark.asyncio
async def test_company_timeline_counts_match_the_real_nested_data(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/companies/85600/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["disclosures"][0]
    assert item["tags"][0]["slug"] == "forecast_revision"
    assert item["file_count"] == 1
    assert item["completed_file_count"] == 1
    assert item["parse_job_count"] == 1
    assert item["completed_parse_job_count"] == 1
    assert item["analysis_result_count"] == 1
    assert len(item["files"]) == 1
    assert item["files"][0]["analysis_results"][0]["analysis_type"] == "financial_facts"


@pytest.mark.asyncio
async def test_parser_quality_reads_the_full_status_breakdown(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/parser-quality")
    assert response.status_code == 200
    body = response.json()
    assert body["total_jobs"] == 1
    assert body["completed_jobs"] == 1
    assert body["failed_jobs"] == 0
    assert body["running_jobs"] == 0
    assert body["pending_jobs"] == 0
    assert body["parse_texts"] == 1
    assert body["status_counts"] == [{"parse_status": "completed", "count": 1}]
    assert body["parsers"][0]["parser_name"] == "apple-vision-ocr"
    assert body["parsers"][0]["average_char_count"] == 42.0
    assert body["parsers"][0]["failed_jobs"] == 0


@pytest.mark.asyncio
async def test_tags_reads_the_real_active_flag(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/tags")
    assert response.status_code == 200
    tags = response.json()["tags"]
    assert tags[0]["slug"] == "forecast_revision"
    assert tags[0]["assignment_count"] == 1
    assert tags[0]["active"] is True
