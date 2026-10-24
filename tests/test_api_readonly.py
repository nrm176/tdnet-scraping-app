"""tdnet/api.py reads analysis.tdnet_*_history_v1 as research_reader (Root
spec 2026-10-24, research DB migration slice 7, §3.4 phase B) -- these tests
exercise it with a fake research connection (the slice-2 Conn/Result fake
pattern), never a live or legacy database. `tdnet.api.get_research_connection`
is overridden the same way `tdnet.api.get_session` used to be overridden for
the legacy ORM session."""
from __future__ import annotations

from datetime import date
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
    "pdf_sha256": "a" * 64,
    "pdf_path": "/Volumes/yakushimachi/Downloads/tdnet/140120260414503449.pdf",
    "has_xbrl_file": False,
    "has_parse_text": True,
}

# TdnetDisclosure.id is a computed sha256 of code/date/pdf_url/time (unchanged
# by this cutover) -- not DISCLOSURE_ROW's own "disclosure_id", which is only
# the fake's stand-in for the view's column of the same name.
DISCLOSURE_ROW_COMPUTED_ID = sha256(
    f"{DISCLOSURE_ROW['code']}_{DISCLOSURE_ROW['disclosure_date']}_"
    f"{DISCLOSURE_ROW['pdf_url']}_{DISCLOSURE_ROW['time']}".encode("utf-8")
).hexdigest()[:16]

TAG_ROW = {
    "disclosure_id": "abc123def4567890",
    "tag_slug": "forecast_revision",
    "label_ja": "業績予想・差異",
    "label_en": "Forecast revision or variance",
    "is_primary": True,
    "confidence": 0.95,
    "source": "deterministic",
    "tagger_name": "tdnet-tagging",
    "tagger_version": "test",
}

TAG_SUMMARY_ROW = {
    "slug": "forecast_revision",
    "label_ja": "業績予想・差異",
    "label_en": "Forecast revision or variance",
    "description": "Forecast revisions and differences between forecasts and results.",
    "priority": 30,
    "assignment_count": 1,
    "primary_count": 1,
}

PARSER_BREAKDOWN_ROW = {
    "parser_name": "pymupdf4llm",
    "parser_version": "test-version",
    "parse_texts": 1,
    "average_char_count": 42.0,
}


def _fake_responses():
    # Ordered most-specific-substring first so a query never matches a
    # broader response meant for a different endpoint.
    return [
        ("WHERE disclosure_id = :disclosure_id", Result(rows=[DISCLOSURE_ROW])),
        ("ANY(:disclosure_ids)", Result(rows=[TAG_ROW])),
        ("GROUP BY tag_slug", Result(rows=[TAG_SUMMARY_ROW])),
        ("GROUP BY parser_name, parser_version", Result(rows=[PARSER_BREAKDOWN_ROW])),
        (f"SELECT count(*) FROM {history_queries.PARSE_TEXT_VIEW}", Result(first=(1,))),
        (f"SELECT count(*) FROM {history_queries.DISCLOSURE_VIEW}", Result(first=(1,))),
        ("LIMIT :limit OFFSET :offset", Result(rows=[DISCLOSURE_ROW])),
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
async def test_lineage_carries_tags_but_leaves_files_empty(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/disclosures/abc123def4567890/lineage")
    assert response.status_code == 200
    body = response.json()
    assert body["disclosure"]["code"] == "85600"
    assert body["tags"][0]["slug"] == "forecast_revision"
    # analysis.tdnet_report_tag_history_v1 carries no per-assignment
    # file/parse-job linkage or evidence_json.
    assert body["tags"][0]["file_id"] is None
    assert body["tags"][0]["parse_job_id"] is None
    assert body["tags"][0]["evidence_json"] is None
    # No view exposes per-file identity -- files always empty.
    assert body["files"] == []


@pytest.mark.asyncio
async def test_parser_status_leaves_files_empty(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/disclosures/abc123def4567890/parser-status")
    assert response.status_code == 200
    body = response.json()
    assert body["disclosure_id"] == "abc123def4567890"
    assert body["files"] == []


@pytest.mark.asyncio
async def test_disclosure_files_always_empty_without_touching_the_connection(client_and_conn):
    client, conn = client_and_conn
    async with client as c:
        response = await c.get("/disclosure-files?download_status=completed")
    assert response.status_code == 200
    assert response.json() == {"total": 0, "limit": 100, "offset": 0, "files": []}
    assert conn.sql == []  # no query at all: the view has nothing to serve here


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
async def test_company_timeline_derives_counts_from_view_booleans(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/companies/85600/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["disclosures"][0]
    assert item["tags"][0]["slug"] == "forecast_revision"
    # has pdf_path + pdf_sha256, no xbrl -> 1 file, 1 completed.
    assert item["file_count"] == 1
    assert item["completed_file_count"] == 1
    # has_parse_text True -> 1 parse job, counted as completed (only
    # "completed" is ever observable from the view).
    assert item["parse_job_count"] == 1
    assert item["completed_parse_job_count"] == 1
    # document_analysis_result has no SQL044 view at all.
    assert item["analysis_result_count"] == 0
    assert item["files"] == []


@pytest.mark.asyncio
async def test_parser_quality_only_reports_the_completed_bucket(client_and_conn):
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
    assert body["parsers"][0]["parser_name"] == "pymupdf4llm"
    assert body["parsers"][0]["average_char_count"] == 42.0
    assert body["parsers"][0]["failed_jobs"] == 0


@pytest.mark.asyncio
async def test_tags_defaults_active_true(client_and_conn):
    client, _ = client_and_conn
    async with client as c:
        response = await c.get("/tags")
    assert response.status_code == 200
    tags = response.json()["tags"]
    assert tags[0]["slug"] == "forecast_revision"
    assert tags[0]["assignment_count"] == 1
    # report_tag.active isn't carried by the view; every migrated tag stands
    # in as active.
    assert tags[0]["active"] is True
