"""`tdnet/review.py` reads raw_tdnet_history.parse_job/file/disclosure/
parse_text as research_reader (Root spec 2026-10-24, research DB migration
slice 7, §3.4 phase B) -- these tests exercise it with a fake research
engine/connection (the slice-2 Conn/Engine fake pattern in
portfolio-manager's backend/tests/test_research_market_repository.py),
never a live or legacy database. `score_pages` itself is DB-independent and
unchanged by the cutover; its tests are unchanged too."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tdnet import history_queries
from tdnet.review import ParsedPage, build_parse_review_report, score_pages


def test_score_pages_flags_sparse_extraction():
    score, warnings = score_pages(
        [
            ParsedPage(page=1, markdown="", char_count=0),
            ParsedPage(page=2, markdown="short", char_count=5),
        ],
        file_size_bytes=3_000_000,
    )

    assert score >= 70
    assert "very low total text" in warnings
    assert "large PDF with sparse extracted text" in warnings


def test_score_pages_flags_dense_garbled_extraction():
    garbled = ("\x01\x02\x03J®XG}uvªLMc\u0089§\u009a<br>" * 35) + "|<br>|<br>|<br>|"

    score, warnings = score_pages(
        [ParsedPage(page=1, markdown=garbled, char_count=len(garbled))],
        file_size_bytes=152_301,
    )

    assert score >= 40
    assert "high control-character ratio with no Japanese text" in warnings


def test_score_pages_does_not_flag_plain_ascii_text_as_garbled():
    text = "Revenue forecast update with normal extractable ASCII text. " * 20

    score, warnings = score_pages(
        [ParsedPage(page=1, markdown=text, char_count=len(text))],
        file_size_bytes=152_301,
    )

    assert score == 0
    assert warnings == ("looks normal",)


# ---------------------------------------------------------------------------
# Fake research connection (slice-2 Conn/Engine pattern)
# ---------------------------------------------------------------------------


class Result:
    def __init__(self, rows=None, first=None):
        self._rows = rows if rows is not None else []
        self._first = first

    def mappings(self):
        return self._rows

    def first(self):
        return self._first


class Conn:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.sql: list[str] = []
        self.params: list[dict | None] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        self.params.append(params)
        if "current_database()" in sql:
            return Result(first=("hft_research", "research_reader", 180003))
        if "pipeline.instance_identity" in sql:
            return Result(first=("hft_research", 1))
        for substring, result in self.responses:
            if substring in sql:
                return result
        return Result(rows=[])

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Engine:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        return self.conn


@pytest.mark.asyncio
async def test_build_parse_review_report(tmp_path, monkeypatch):
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    text = "業績予想の修正に関するお知らせ\n" \
           "今回修正予想 18,000 2,580\n"
    row = {
        "parse_job_id": 10,
        "file_id": 1,
        "disclosure_id": "abc123def4567890",
        "code": "12345",
        "name": "テスト株式会社",
        "title": "業績予想の修正に関するお知らせ",
        "disclosure_date": date(2026, 5, 16),
        "pdf_path": str(pdf_path),
        "file_size_bytes": 10,
        "text_path": str(tmp_path / "parsed.md"),
        "parser_name": "pymupdf4llm",
        "parser_version": "test-version",
        # raw_tdnet_history.parse_text.pages_json carries the legacy
        # per-page markdown structure verbatim (see _pages_from_json).
        "pages_json": {"pages": [{"page": 1, "markdown": text, "char_count": len(text)}]},
    }
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=[row]))])

    def fake_render(pdf_path: Path, page_number: int, destination: Path, *, zoom: float = 1.5) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"png")

    monkeypatch.setattr("tdnet.review.render_pdf_page", fake_render)

    report = await build_parse_review_report(
        output_root=tmp_path / "reviews",
        parser_version="test-version",
        limit=1,
        engine=Engine(conn),
    )

    html = report.index_path.read_text(encoding="utf-8")
    assert report.reviewed_count == 1
    assert "TDnet Parse Review" in html
    assert "業績予想の修正に関するお知らせ" in html
    # legacy_id-derived file/parse-job badges, matching the pre-cutover HTML.
    assert "file 1" in html
    assert "parse 10" in html
    assert list((report.output_dir / "assets").glob("file-1-page-001.png"))


@pytest.mark.asyncio
async def test_build_parse_review_report_handles_missing_pdf_path_gracefully(tmp_path):
    row = {
        "parse_job_id": 11,
        "file_id": 2,
        "disclosure_id": "noPdfPath00000001",
        "code": "99999",
        "name": "No PDF Co",
        "title": "Some disclosure",
        "disclosure_date": date(2026, 5, 16),
        "pdf_path": None,
        "file_size_bytes": None,
        "text_path": None,
        "parser_name": "pymupdf4llm",
        "parser_version": "test-version",
        "pages_json": None,
    }
    conn = Conn([(history_queries.RAW_PARSE_JOB, Result(rows=[row]))])

    report = await build_parse_review_report(
        output_root=tmp_path / "reviews",
        parser_version="test-version",
        limit=1,
        engine=Engine(conn),
    )

    assert report.reviewed_count == 1
    html = report.index_path.read_text(encoding="utf-8")
    assert "PDF page image was not available." in html
    # No pages_json at all -> score_pages' empty-pages fallback.
    assert "missing page JSON" in html
