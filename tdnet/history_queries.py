"""Queries against the SQL044 TDnet history views (Root spec 2026-10-24,
research DB migration slice 7, `analysis.tdnet_disclosure_history_v1` /
`tdnet_parse_text_history_v1` / `tdnet_report_tag_history_v1`), used by
`tdnet/api.py` and `tdnet/review.py` through a `research_reader` connection
(`tdnet/research_reader.py`).

Every function here takes an already-open, already-identity-validated
`AsyncConnection` (or a test fake with the same `execute(stmt, params)` ->
awaited result shape) and returns plain dicts -- never an ORM record, and
never a read against the legacy `tdnet` DB.

Gaps, relative to the legacy ORM tables these views replace (documented here
once; call sites reference this docstring rather than repeating it):

- No per-file identity or download metadata. The disclosure view carries only
  the one pdf file's `pdf_sha256`/`pdf_path`, plus `has_xbrl_file` (an
  existence check, not a download-success check) -- there is no `file.id`,
  `source_url`, `source_file_id`, `storage_bucket`, `content_type`,
  `file_size_bytes`, `download_status`, `download_attempts`,
  `downloaded_at`, or `last_download_error` for either the pdf or the xbrl
  file, and no row at all for the xbrl file.
- No per-parse-job identity or status. The parse-text view carries only
  `parser_name`/`parser_version` plus the extracted `content_text` and its
  `page_count`/`char_count` -- there is no `parse_job.id`, `file_id`,
  `parse_status`, `parse_attempts`, `text_path`, `text_sha256`, `parsed_at`,
  or `last_parse_error`, and (because `document_parse_texts` only ever held
  successful output) no row at all for a failed, running, or pending parse
  job.
- No `pages_json` per-page breakdown -- only the concatenated `content_text`
  and its aggregate counts.
- No `document_analysis_result` view at all (not in SQL044's three views).
- The tag-history view is assignments joined to tags, so a tag with zero
  assignments does not appear in it (unlike the legacy `report_tag` table,
  which lists every registered tag); it also does not carry
  `report_tag.active`, or the per-assignment `file_id`/`parse_job_id`/
  `evidence_json`.

Callers return these fields empty/omitted/defaulted (never by reading the
legacy DB) rather than leaving them out of the response shape -- see api.py
and review.py for exactly where.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy import text

DISCLOSURE_VIEW = "analysis.tdnet_disclosure_history_v1"
PARSE_TEXT_VIEW = "analysis.tdnet_parse_text_history_v1"
REPORT_TAG_VIEW = "analysis.tdnet_report_tag_history_v1"

_DISCLOSURE_COLUMNS = (
    "disclosure_id, disclosure_date, time, code, name, title, pdf_url,"
    " xbrl_available, xbrl_url, place, history, pdf_sha256, pdf_path,"
    " has_xbrl_file, has_parse_text"
)

_DISCLOSURE_FILTERS = (
    " WHERE (CAST(:disclosure_date AS date) IS NULL OR disclosure_date = CAST(:disclosure_date AS date))"
    "   AND (CAST(:date_from AS date) IS NULL OR disclosure_date >= CAST(:date_from AS date))"
    "   AND (CAST(:date_to AS date) IS NULL OR disclosure_date <= CAST(:date_to AS date))"
    "   AND (CAST(:code AS text) IS NULL OR code = CAST(:code AS text))"
)

DISCLOSURES_QUERY = text(
    f"SELECT {_DISCLOSURE_COLUMNS} FROM {DISCLOSURE_VIEW}"
    f"{_DISCLOSURE_FILTERS}"
    " ORDER BY disclosure_date DESC, time DESC, code ASC"
    " LIMIT :limit OFFSET :offset"
)

DISCLOSURES_COUNT_QUERY = text(
    f"SELECT count(*) FROM {DISCLOSURE_VIEW}{_DISCLOSURE_FILTERS}"
)

DISCLOSURE_BY_ID_QUERY = text(
    f"SELECT {_DISCLOSURE_COLUMNS} FROM {DISCLOSURE_VIEW} WHERE disclosure_id = :disclosure_id"
)

TAGS_FOR_DISCLOSURES_QUERY = text(
    "SELECT disclosure_id, tag_slug, label_ja, label_en, is_primary, confidence,"
    " source, tagger_name, tagger_version"
    f" FROM {REPORT_TAG_VIEW}"
    " WHERE disclosure_id = ANY(:disclosure_ids)"
    " ORDER BY disclosure_id ASC, is_primary DESC, tag_priority ASC, tag_slug ASC"
)

TAG_SUMMARIES_QUERY = text(
    "SELECT tag_slug AS slug, label_ja, label_en, tag_description AS description,"
    " tag_priority AS priority, count(*) AS assignment_count,"
    " sum(CASE WHEN is_primary THEN 1 ELSE 0 END) AS primary_count"
    f" FROM {REPORT_TAG_VIEW}"
    " GROUP BY tag_slug, label_ja, label_en, tag_description, tag_priority"
    " ORDER BY tag_priority ASC, slug ASC"
)

PARSER_TOTALS_QUERY = text(f"SELECT count(*) FROM {PARSE_TEXT_VIEW}")

PARSER_BREAKDOWN_QUERY = text(
    "SELECT parser_name, parser_version, count(*) AS parse_texts,"
    " avg(char_count) AS average_char_count"
    f" FROM {PARSE_TEXT_VIEW}"
    " GROUP BY parser_name, parser_version"
    " ORDER BY parser_name ASC, parser_version DESC"
)


def _review_candidates_query(strategy: str, *, filter_forecast_correction: bool) -> Any:
    where = " WHERE pt.parser_name = :parser_name AND pt.parser_version = :parser_version"
    if filter_forecast_correction:
        where += " AND (d.title LIKE '%業績予想%' OR d.title LIKE '%予想値%')"
        # i.e. title LIKE '%業績予想%' OR title LIKE '%予想値%' (escaped so the
        # file stays readable through tools that mangle non-ASCII source).
    if strategy == "random":
        order = " ORDER BY random()"
    else:
        # "recent" and "forecast-correction" both want newest-first; so does
        # the "suspicious"/default branch's bulk fetch, scored and re-sorted
        # in review.py afterwards -- there is no `parsed_at` in the view (see
        # module docstring), so disclosure recency is the closest available
        # proxy for all of them.
        order = " ORDER BY d.disclosure_date DESC, d.time DESC"
    return text(
        "SELECT d.disclosure_id, d.code, d.name, d.title, d.disclosure_date, d.pdf_path,"
        " pt.parser_name, pt.parser_version, pt.content_text, pt.page_count, pt.char_count"
        f" FROM {PARSE_TEXT_VIEW} pt"
        f" JOIN {DISCLOSURE_VIEW} d ON d.disclosure_id = pt.disclosure_id"
        f"{where}{order} LIMIT :sql_limit"
    )


def _rows(result: Any) -> list[dict]:
    return [dict(m) for m in result.mappings()]


def _normalized_code(code: Optional[str]) -> Optional[str]:
    """Mirror `repository.py`'s `_query_disclosures_statement`: a code filter
    is matched upper-cased and stripped, regardless of the caller's casing."""
    return code.strip().upper() if code else None


async def fetch_disclosures(
    conn: Any,
    *,
    disclosure_date: Optional[date] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    code: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    result = await conn.execute(
        DISCLOSURES_QUERY,
        {
            "disclosure_date": disclosure_date,
            "date_from": date_from,
            "date_to": date_to,
            "code": _normalized_code(code),
            "limit": limit,
            "offset": offset,
        },
    )
    return _rows(result)


async def count_disclosures(
    conn: Any,
    *,
    disclosure_date: Optional[date] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    code: Optional[str] = None,
) -> int:
    result = await conn.execute(
        DISCLOSURES_COUNT_QUERY,
        {
            "disclosure_date": disclosure_date,
            "date_from": date_from,
            "date_to": date_to,
            "code": _normalized_code(code),
        },
    )
    row = result.first()
    if row is None:
        return 0
    return int(row[0] or 0)


async def fetch_disclosure(conn: Any, disclosure_id: str) -> Optional[dict]:
    result = await conn.execute(DISCLOSURE_BY_ID_QUERY, {"disclosure_id": disclosure_id})
    rows = _rows(result)
    return rows[0] if rows else None


async def fetch_tags_for_disclosures(conn: Any, disclosure_ids: Sequence[str]) -> list[dict]:
    if not disclosure_ids:
        return []
    result = await conn.execute(
        TAGS_FOR_DISCLOSURES_QUERY, {"disclosure_ids": list(disclosure_ids)}
    )
    return _rows(result)


async def fetch_tag_summaries(conn: Any) -> list[dict]:
    result = await conn.execute(TAG_SUMMARIES_QUERY)
    return _rows(result)


async def fetch_parser_totals(conn: Any) -> int:
    result = await conn.execute(PARSER_TOTALS_QUERY)
    row = result.first()
    if row is None:
        return 0
    return int(row[0] or 0)


async def fetch_parser_breakdown(conn: Any) -> list[dict]:
    result = await conn.execute(PARSER_BREAKDOWN_QUERY)
    return _rows(result)


async def fetch_review_candidates(
    conn: Any,
    *,
    strategy: str,
    limit: int,
    parser_name: str,
    parser_version: str,
) -> list[dict]:
    # The "suspicious"/default strategy scores a wide bulk fetch in Python
    # and keeps the top `limit` afterwards (review.py); every other strategy
    # already orders for what it wants, so the SQL LIMIT is the final one.
    sql_limit = limit * 20 if strategy not in ("random", "recent", "forecast-correction") else limit
    query = _review_candidates_query(strategy, filter_forecast_correction=strategy == "forecast-correction")
    result = await conn.execute(
        query,
        {"parser_name": parser_name, "parser_version": parser_version, "sql_limit": sql_limit},
    )
    return _rows(result)
