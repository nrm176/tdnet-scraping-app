"""Unit tests for tdnet/history_queries.py's SQL building and row shaping,
using the slice-2 fake Conn/Result pattern (portfolio-manager's
backend/tests/test_research_market_repository.py) instead of a live
database. Every query here reads analysis.tdnet_*_history_v1 (SQL044) --
never the legacy tdnet DB."""
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


@pytest.mark.asyncio
async def test_fetch_tags_for_disclosures_short_circuits_on_empty_input():
    conn = Conn([])
    assert await history_queries.fetch_tags_for_disclosures(conn, []) == []
    assert conn.sql == []


@pytest.mark.asyncio
async def test_fetch_tags_for_disclosures_queries_the_tag_history_view():
    rows = [{"disclosure_id": "abc", "tag_slug": "forecast_revision"}]
    conn = Conn([(history_queries.REPORT_TAG_VIEW, Result(rows=rows))])
    out = await history_queries.fetch_tags_for_disclosures(conn, ["abc", "def"])
    assert out == rows
    assert conn.params[-1]["disclosure_ids"] == ["abc", "def"]
    assert "ORDER BY disclosure_id ASC, is_primary DESC, tag_priority ASC, tag_slug ASC" in conn.sql[-1]


@pytest.mark.asyncio
async def test_fetch_tag_summaries_groups_by_tag():
    rows = [{"slug": "forecast_revision", "assignment_count": 3, "primary_count": 1}]
    conn = Conn([(history_queries.REPORT_TAG_VIEW, Result(rows=rows))])
    out = await history_queries.fetch_tag_summaries(conn)
    assert out == rows
    sql = conn.sql[-1]
    assert "GROUP BY tag_slug, label_ja, label_en, tag_description, tag_priority" in sql
    assert "ORDER BY tag_priority ASC, slug ASC" in sql


@pytest.mark.asyncio
async def test_fetch_parser_totals_returns_zero_on_missing_row():
    conn = Conn([])
    assert await history_queries.fetch_parser_totals(conn) == 0


@pytest.mark.asyncio
async def test_fetch_parser_totals_reads_the_count():
    conn = Conn([(history_queries.PARSE_TEXT_VIEW, Result(first=(42,)))])
    assert await history_queries.fetch_parser_totals(conn) == 42


@pytest.mark.asyncio
async def test_fetch_parser_breakdown_groups_by_parser_identity():
    rows = [{"parser_name": "pymupdf4llm", "parser_version": "1", "parse_texts": 5, "average_char_count": 120.0}]
    conn = Conn([(history_queries.PARSE_TEXT_VIEW, Result(rows=rows))])
    out = await history_queries.fetch_parser_breakdown(conn)
    assert out == rows
    sql = conn.sql[-1]
    assert "GROUP BY parser_name, parser_version" in sql
    assert "ORDER BY parser_name ASC, parser_version DESC" in sql


@pytest.mark.asyncio
async def test_fetch_review_candidates_suspicious_uses_limit_times_20_and_disclosure_recency():
    conn = Conn([(history_queries.PARSE_TEXT_VIEW, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="suspicious", limit=50, parser_name="pymupdf4llm", parser_version="v1"
    )
    sql = conn.sql[-1]
    assert conn.params[-1]["sql_limit"] == 1000
    assert "ORDER BY d.disclosure_date DESC, d.time DESC" in sql
    assert "random()" not in sql


@pytest.mark.asyncio
async def test_fetch_review_candidates_random_orders_by_random_with_plain_limit():
    conn = Conn([(history_queries.PARSE_TEXT_VIEW, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="random", limit=25, parser_name="pymupdf4llm", parser_version="v1"
    )
    assert conn.params[-1]["sql_limit"] == 25
    assert "ORDER BY random()" in conn.sql[-1]


@pytest.mark.asyncio
async def test_fetch_review_candidates_forecast_correction_filters_title():
    conn = Conn([(history_queries.PARSE_TEXT_VIEW, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="forecast-correction", limit=50, parser_name="pymupdf4llm", parser_version="v1"
    )
    sql = conn.sql[-1]
    assert conn.params[-1]["sql_limit"] == 50
    assert "d.title LIKE" in sql


@pytest.mark.asyncio
async def test_fetch_review_candidates_joins_disclosure_and_parse_text_views_and_binds_parser_identity():
    conn = Conn([(history_queries.PARSE_TEXT_VIEW, Result(rows=[]))])
    await history_queries.fetch_review_candidates(
        conn, strategy="recent", limit=10, parser_name="apple-vision-ocr", parser_version="ocr-v1"
    )
    sql = conn.sql[-1]
    assert history_queries.PARSE_TEXT_VIEW in sql
    assert history_queries.DISCLOSURE_VIEW in sql
    assert conn.params[-1]["parser_name"] == "apple-vision-ocr"
    assert conn.params[-1]["parser_version"] == "ocr-v1"
