"""Unit tests for tdnet/history_queries.py's SQL building and row shaping,
using the slice-2 fake Conn/Result pattern (portfolio-manager's
backend/tests/test_research_market_repository.py) instead of a live
database. `fetch_disclosures`/`count_disclosures`/`fetch_disclosure` read
the SQL044 analysis.tdnet_disclosure_history_v1 view; everything else reads
raw_tdnet_history.* directly (research_reader has SELECT on every table in
that schema) -- never the legacy tdnet DB."""
from __future__ import annotations

import pytest

from tdnet import history_queries


class Result:
    def __init__(self, rows=None, first=None):
        self._rows = rows if rows is not None else []
        self._first = first

    def mappings(self):
        return self._rows

    def first(self):
        return self._first


class Conn:
    """Returns the first canned result whose substring matches the SQL."""

    def __init__(self, responses=None):
        self.responses = responses or []
        self.sql = []
        self.params = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        self.params.append(params)
        for substring, result in self.responses:
            if substring in sql:
                return result
        return Result(rows=[])


@pytest.mark.asyncio
async def test_fetch_disclosures_queries_the_disclosure_history_view():
    rows = [{"disclosure_id": "abc", "code": "1234"}]
    conn = Conn([(history_queries.DISCLOSURE_VIEW, Result(rows=rows))])
    out = await history_queries.fetch_disclosures(conn, code="1234")
    assert out == rows
    sql = conn.sql[-1]
    assert history_queries.DISCLOSURE_VIEW in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert "ORDER BY disclosure_date DESC, time DESC, code ASC" in sql


@pytest.mark.asyncio
async def test_fetch_disclosures_normalizes_code_casing():
    conn = Conn([(history_queries.DISCLOSURE_VIEW, Result(rows=[]))])
    await history_queries.fetch_disclosures(conn, code=" 1234a ")
    assert conn.params[-1]["code"] == "1234A"


@pytest.mark.asyncio
async def test_fetch_disclosures_passes_through_none_filters():
    conn = Conn([(history_queries.DISCLOSURE_VIEW, Result(rows=[]))])
    await history_queries.fetch_disclosures(conn)
    params = conn.params[-1]
    assert params["disclosure_date"] is None
    assert params["date_from"] is None
    assert params["date_to"] is None
    assert params["code"] is None


@pytest.mark.asyncio
async def test_count_disclosures_returns_zero_on_missing_row():
    conn = Conn([])
    total = await history_queries.count_disclosures(conn)
    assert total == 0


@pytest.mark.asyncio
async def test_count_disclosures_normalizes_code_and_reads_first_column():
    conn = Conn([(history_queries.DISCLOSURE_VIEW, Result(first=(7,)))])
    total = await history_queries.count_disclosures(conn, code="toyota ".strip())
    assert total == 7
    assert conn.params[-1]["code"] == "TOYOTA"


@pytest.mark.asyncio
async def test_fetch_disclosure_returns_none_when_missing():
    conn = Conn([(history_queries.DISCLOSURE_VIEW, Result(rows=[]))])
    assert await history_queries.fetch_disclosure(conn, "missing") is None


@pytest.mark.asyncio
async def test_fetch_disclosure_returns_the_one_row():
    row = {"disclosure_id": "abc123def4567890", "code": "85600"}
    conn = Conn([(history_queries.DISCLOSURE_VIEW, Result(rows=[row]))])
    out = await history_queries.fetch_disclosure(conn, "abc123def4567890")
    assert out == row
    assert conn.params[-1]["disclosure_id"] == "abc123def4567890"


# ---------------------------------------------------------------------------
# raw_tdnet_history.file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_files_for_disclosures_short_circuits_on_empty_input():
    conn = Conn([])
    assert await history_queries.fetch_files_for_disclosures(conn, []) == []
    assert conn.sql == []


@pytest.mark.asyncio
async def test_fetch_files_for_disclosures_queries_the_raw_file_table():
    rows = [{"id": 1, "disclosure_id": "abc", "file_type": "pdf"}]
    conn = Conn([(history_queries.RAW_FILE, Result(rows=rows))])
    out = await history_queries.fetch_files_for_disclosures(conn, ["abc", "def"])
    assert out == rows
    sql = conn.sql[-1]
    assert "legacy_id AS id" in sql
    assert conn.params[-1]["disclosure_ids"] == ["abc", "def"]
    assert "ORDER BY disclosure_id ASC, file_type ASC, legacy_id ASC" in sql


@pytest.mark.asyncio
async def test_fetch_disclosure_files_filters_and_paginates():
    conn = Conn([(history_queries.RAW_FILE, Result(rows=[{"id": 1}]))])
    out = await history_queries.fetch_disclosure_files(
        conn, disclosure_id="abc", download_status="completed", file_type="pdf", limit=10, offset=5
    )
    assert out == [{"id": 1}]
    sql = conn.sql[-1]
    params = conn.params[-1]
    assert params == {
        "disclosure_id": "abc", "download_status": "completed", "file_type": "pdf",
        "limit": 10, "offset": 5,
    }
    assert "ORDER BY updated_at DESC" in sql
    assert "LIMIT :limit OFFSET :offset" in sql


@pytest.mark.asyncio
async def test_count_disclosure_files_returns_zero_on_missing_row():
    conn = Conn([])
    assert await history_queries.count_disclosure_files(conn) == 0


@pytest.mark.asyncio
async def test_count_disclosure_files_reads_first_column():
    conn = Conn([(history_queries.RAW_FILE, Result(first=(3,)))])
    assert await history_queries.count_disclosure_files(conn, download_status="completed") == 3


# ---------------------------------------------------------------------------
# raw_tdnet_history.parse_job / parse_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_parse_jobs_for_files_short_circuits_on_empty_input():
    conn = Conn([])
    assert await history_queries.fetch_parse_jobs_for_files(conn, []) == []
    assert conn.sql == []


@pytest.mark.asyncio
async def test_fetch_parse_jobs_for_files_left_joins_parse_text():
    rows = [{"id": 10, "file_id": 1, "text_id": None}]
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=rows))])
    out = await history_queries.fetch_parse_jobs_for_files(conn, [1, 2])
    assert out == rows
    sql = conn.sql[-1]
    assert "legacy_id AS id" in sql
    assert "LEFT JOIN" in sql and history_queries.RAW_PARSE_TEXT in sql
    assert conn.params[-1]["file_legacy_ids"] == [1, 2]
    assert "ORDER BY pj.file_legacy_id ASC, pj.parser_name ASC, pj.parser_version DESC, pj.legacy_id ASC" in sql


# ---------------------------------------------------------------------------
# raw_tdnet_history.analysis_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_analysis_results_for_files_short_circuits_on_empty_input():
    conn = Conn([])
    assert await history_queries.fetch_analysis_results_for_files(conn, []) == []
    assert conn.sql == []


@pytest.mark.asyncio
async def test_fetch_analysis_results_for_files_queries_the_raw_table():
    rows = [{"id": 99, "file_id": 1, "analysis_type": "financial_facts"}]
    conn = Conn([(history_queries.RAW_ANALYSIS_RESULT, Result(rows=rows))])
    out = await history_queries.fetch_analysis_results_for_files(conn, [1])
    assert out == rows
    sql = conn.sql[-1]
    assert "legacy_id AS id" in sql
    assert conn.params[-1]["file_legacy_ids"] == [1]
    assert "ORDER BY file_legacy_id ASC, analysis_type ASC, legacy_id ASC" in sql


# ---------------------------------------------------------------------------
# raw_tdnet_history.report_tag / report_tag_assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_tags_for_disclosures_short_circuits_on_empty_input():
    conn = Conn([])
    assert await history_queries.fetch_tags_for_disclosures(conn, []) == []
    assert conn.sql == []


@pytest.mark.asyncio
async def test_fetch_tags_for_disclosures_queries_the_raw_assignment_table():
    rows = [{"disclosure_id": "abc", "tag_slug": "forecast_revision", "file_id": 1, "parse_job_id": 10}]
    conn = Conn([(history_queries.RAW_REPORT_TAG_ASSIGNMENT, Result(rows=rows))])
    out = await history_queries.fetch_tags_for_disclosures(conn, ["abc", "def"])
    assert out == rows
    sql = conn.sql[-1]
    assert "file_legacy_id AS file_id" in sql
    assert "parse_job_legacy_id AS parse_job_id" in sql
    assert conn.params[-1]["disclosure_ids"] == ["abc", "def"]
    assert "ORDER BY a.disclosure_id ASC, a.is_primary DESC, t.priority ASC, t.slug ASC" in sql


@pytest.mark.asyncio
async def test_fetch_tag_summaries_left_joins_so_every_tag_appears():
    rows = [{"slug": "forecast_revision", "active": True, "assignment_count": 3, "primary_count": 1}]
    conn = Conn([(history_queries.RAW_REPORT_TAG, Result(rows=rows))])
    out = await history_queries.fetch_tag_summaries(conn)
    assert out == rows
    sql = conn.sql[-1]
    assert "LEFT JOIN" in sql and history_queries.RAW_REPORT_TAG_ASSIGNMENT in sql
    assert "t.active" in sql
    assert "GROUP BY t.slug, t.label_ja, t.label_en, t.description, t.priority, t.active" in sql
    assert "ORDER BY t.priority ASC, t.slug ASC" in sql


# ---------------------------------------------------------------------------
# parser quality (raw_tdnet_history.parse_job / parse_text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_parser_totals_returns_zeros_on_missing_row():
    conn = Conn([])
    assert await history_queries.fetch_parser_totals(conn) == {
        "total_jobs": 0, "completed_jobs": 0, "failed_jobs": 0,
        "running_jobs": 0, "pending_jobs": 0, "parse_texts": 0,
    }


@pytest.mark.asyncio
async def test_fetch_parser_totals_reads_the_full_status_breakdown():
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(first=(10, 7, 2, 1, 0, 7)))])
    totals = await history_queries.fetch_parser_totals(conn)
    assert totals == {
        "total_jobs": 10, "completed_jobs": 7, "failed_jobs": 2,
        "running_jobs": 1, "pending_jobs": 0, "parse_texts": 7,
    }


@pytest.mark.asyncio
async def test_fetch_parser_status_counts_groups_by_status():
    rows = [{"parse_status": "completed", "count": 7}, {"parse_status": "failed", "count": 2}]
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=rows))])
    out = await history_queries.fetch_parser_status_counts(conn)
    assert out == rows
    sql = conn.sql[-1]
    assert "GROUP BY parse_status" in sql
    assert "ORDER BY parse_status ASC" in sql


@pytest.mark.asyncio
async def test_fetch_parser_breakdown_groups_by_parser_identity_with_full_status():
    rows = [{
        "parser_name": "pymupdf4llm", "parser_version": "1", "total_jobs": 6,
        "completed_jobs": 5, "failed_jobs": 1, "running_jobs": 0, "pending_jobs": 0,
        "parse_texts": 5, "average_char_count": 120.0,
    }]
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=rows))])
    out = await history_queries.fetch_parser_breakdown(conn)
    assert out == rows
    sql = conn.sql[-1]
    assert "LEFT JOIN" in sql and history_queries.RAW_PARSE_TEXT in sql
    assert "GROUP BY pj.parser_name, pj.parser_version" in sql
    assert "ORDER BY pj.parser_name ASC, pj.parser_version DESC" in sql


# ---------------------------------------------------------------------------
# review candidates (raw_tdnet_history.parse_job/file/disclosure/parse_text)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_review_candidates_suspicious_uses_limit_times_20_and_parsed_at_recency():
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="suspicious", limit=50, parser_name="pymupdf4llm", parser_version="v1"
    )
    sql = conn.sql[-1]
    assert conn.params[-1]["sql_limit"] == 1000
    assert "ORDER BY pj.parsed_at DESC, pj.legacy_id DESC" in sql
    assert "random()" not in sql
    assert "pj.parse_status = 'completed'" in sql
    assert "pj.text_path IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_fetch_review_candidates_random_orders_by_random_with_plain_limit():
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="random", limit=25, parser_name="pymupdf4llm", parser_version="v1"
    )
    assert conn.params[-1]["sql_limit"] == 25
    assert "ORDER BY random()" in conn.sql[-1]


@pytest.mark.asyncio
async def test_fetch_review_candidates_forecast_correction_filters_title():
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="forecast-correction", limit=50, parser_name="pymupdf4llm", parser_version="v1"
    )
    sql = conn.sql[-1]
    assert conn.params[-1]["sql_limit"] == 50
    assert "d.title LIKE" in sql


@pytest.mark.asyncio
async def test_fetch_review_candidates_joins_file_and_disclosure_and_binds_parser_identity():
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="recent", limit=10, parser_name="apple-vision-ocr", parser_version="ocr-v1"
    )
    sql = conn.sql[-1]
    assert history_queries.RAW_PARSE_JOB in sql
    assert history_queries.RAW_FILE in sql
    assert history_queries.RAW_DISCLOSURE in sql
    assert "pages_json" in sql
    assert "file_size_bytes" in sql
    assert conn.params[-1]["parser_name"] == "apple-vision-ocr"
    assert conn.params[-1]["parser_version"] == "ocr-v1"
