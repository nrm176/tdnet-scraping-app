"""Queries against the research DB's migrated TDnet history (Root spec
2026-10-24, research DB migration slice 7), used by `tdnet/api.py` and
`tdnet/review.py` through a `research_reader` connection
(`tdnet/research_reader.py`).

`research_reader` has `SELECT` on every table in the `raw_tdnet_history`
schema (SQL004's grants), not just the SQL044 `analysis.tdnet_*_history_v1`
views, so every field the legacy ORM tables carried is reachable here --
there is no functional loss relative to the legacy DB this replaces. Each
raw table keeps the legacy integer `id` as `legacy_id` (or, for `file`,
`parse_job`, `parse_text`, `analysis_result`, `report_tag_assignment`,
`<fk column>_legacy_id` for the FK), so a legacy `id`/`file_id`/
`parse_job_id` in a response is reproduced by selecting `legacy_id AS id` /
`file_legacy_id AS file_id` / `parse_job_legacy_id AS parse_job_id`. The one
legacy table that's always empty and was never migrated is
`document_parse_reviews` (ruling T2 in the slice-7 design) -- review
decisions stay empty for that reason alone, not because of a reader gap.

The three SQL044 `analysis.tdnet_*_history_v1` views are still used for the
plain disclosure listing/lookup queries below (`fetch_disclosures` /
`count_disclosures` / `fetch_disclosure`): they carry every column those
need, and querying them is simpler than the equivalent raw-table SELECT.
Every other query here reads `raw_tdnet_history.*` directly, because the
views intentionally don't carry per-file identity, per-parse-job status,
`pages_json`, or `document_analysis_result` at all.

Every function here takes an already-open, already-identity-validated
`AsyncConnection` (or a test fake with the same `execute(stmt, params)` ->
awaited result shape) and returns plain dicts -- never an ORM record, and
never a read against the legacy `tdnet` DB.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy import text

DISCLOSURE_VIEW = "analysis.tdnet_disclosure_history_v1"

RAW_DISCLOSURE = "raw_tdnet_history.disclosure"
RAW_FILE = "raw_tdnet_history.file"
RAW_PARSE_JOB = "raw_tdnet_history.parse_job"
RAW_PARSE_TEXT = "raw_tdnet_history.parse_text"
RAW_ANALYSIS_RESULT = "raw_tdnet_history.analysis_result"
RAW_REPORT_TAG = "raw_tdnet_history.report_tag"
RAW_REPORT_TAG_ASSIGNMENT = "raw_tdnet_history.report_tag_assignment"

_DISCLOSURE_COLUMNS = (
    "disclosure_id, disclosure_date, time, code, name, title, pdf_url,"
    " xbrl_available, xbrl_url, place, history"
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

# -- files (raw_tdnet_history.file) ------------------------------------------

_FILE_COLUMNS = (
    "legacy_id AS id, disclosure_id, file_type, source_url, source_file_id,"
    " storage_bucket, storage_path, content_type, file_size_bytes, sha256,"
    " download_status, download_attempts, downloaded_at, last_download_error"
)

FILES_FOR_DISCLOSURES_QUERY = text(
    f"SELECT {_FILE_COLUMNS} FROM {RAW_FILE}"
    " WHERE disclosure_id = ANY(:disclosure_ids)"
    " ORDER BY disclosure_id ASC, file_type ASC, legacy_id ASC"
)

_DISCLOSURE_FILES_FILTERS = (
    " WHERE (CAST(:disclosure_id AS text) IS NULL OR disclosure_id = CAST(:disclosure_id AS text))"
    "   AND (CAST(:download_status AS text) IS NULL OR download_status = CAST(:download_status AS text))"
    "   AND (CAST(:file_type AS text) IS NULL OR file_type = CAST(:file_type AS text))"
)

DISCLOSURE_FILES_QUERY = text(
    f"SELECT {_FILE_COLUMNS} FROM {RAW_FILE}{_DISCLOSURE_FILES_FILTERS}"
    " ORDER BY updated_at DESC"
    " LIMIT :limit OFFSET :offset"
)

DISCLOSURE_FILES_COUNT_QUERY = text(
    f"SELECT count(*) FROM {RAW_FILE}{_DISCLOSURE_FILES_FILTERS}"
)

# -- parse jobs + their parse_text summary (raw_tdnet_history.parse_job/parse_text) --

PARSE_JOBS_FOR_FILES_QUERY = text(
    "SELECT pj.legacy_id AS id, pj.file_legacy_id AS file_id, pj.parser_name, pj.parser_version,"
    " pj.parse_status, pj.parse_attempts, pj.text_path, pj.text_sha256, pj.parsed_at, pj.last_parse_error,"
    " pt.legacy_id AS text_id, pt.page_count AS text_page_count, pt.char_count AS text_char_count,"
    " pt.content_sha256 AS text_content_sha256, pt.created_at AS text_created_at, pt.updated_at AS text_updated_at"
    f" FROM {RAW_PARSE_JOB} pj"
    f" LEFT JOIN {RAW_PARSE_TEXT} pt ON pt.parse_job_legacy_id = pj.legacy_id"
    " WHERE pj.file_legacy_id = ANY(:file_legacy_ids)"
    " ORDER BY pj.file_legacy_id ASC, pj.parser_name ASC, pj.parser_version DESC, pj.legacy_id ASC"
)

# -- analysis results (raw_tdnet_history.analysis_result) -------------------

ANALYSIS_RESULTS_FOR_FILES_QUERY = text(
    "SELECT legacy_id AS id, file_legacy_id AS file_id, parse_job_legacy_id AS parse_job_id,"
    " analysis_type, analyzer_name, analyzer_version, status, result_json, result_text,"
    " analyzed_at, last_analysis_error, created_at, updated_at"
    f" FROM {RAW_ANALYSIS_RESULT}"
    " WHERE file_legacy_id = ANY(:file_legacy_ids)"
    " ORDER BY file_legacy_id ASC, analysis_type ASC, legacy_id ASC"
)

# -- tags (raw_tdnet_history.report_tag/report_tag_assignment) --------------

TAGS_FOR_DISCLOSURES_QUERY = text(
    "SELECT a.disclosure_id, a.tag_slug, t.label_ja, t.label_en, a.is_primary, a.confidence,"
    " a.source, a.file_legacy_id AS file_id, a.parse_job_legacy_id AS parse_job_id,"
    " a.evidence_json, a.tagger_name, a.tagger_version"
    f" FROM {RAW_REPORT_TAG_ASSIGNMENT} a"
    f" JOIN {RAW_REPORT_TAG} t ON t.slug = a.tag_slug"
    " WHERE a.disclosure_id = ANY(:disclosure_ids)"
    " ORDER BY a.disclosure_id ASC, a.is_primary DESC, t.priority ASC, t.slug ASC"
)

# Every registered tag (including one with zero assignments), left-joined to
# its assignment counts -- unlike the SQL044 view, which is assignments
# joined to tags and so only ever shows a tag that has at least one.
TAG_SUMMARIES_QUERY = text(
    "SELECT t.slug, t.label_ja, t.label_en, t.description, t.priority, t.active,"
    " count(a.report_tag_assignment_entry_id) AS assignment_count,"
    " coalesce(sum(CASE WHEN a.is_primary THEN 1 ELSE 0 END), 0) AS primary_count"
    f" FROM {RAW_REPORT_TAG} t"
    f" LEFT JOIN {RAW_REPORT_TAG_ASSIGNMENT} a ON a.tag_slug = t.slug"
    " GROUP BY t.slug, t.label_ja, t.label_en, t.description, t.priority, t.active"
    " ORDER BY t.priority ASC, t.slug ASC"
)

# -- parser quality (raw_tdnet_history.parse_job/parse_text) ----------------

_STATUS_SUM = (
    "sum(CASE WHEN pj.parse_status = 'completed' THEN 1 ELSE 0 END) AS completed_jobs,"
    " sum(CASE WHEN pj.parse_status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,"
    " sum(CASE WHEN pj.parse_status = 'running' THEN 1 ELSE 0 END) AS running_jobs,"
    " sum(CASE WHEN pj.parse_status = 'pending' THEN 1 ELSE 0 END) AS pending_jobs"
)

PARSER_TOTALS_QUERY = text(
    f"SELECT count(pj.legacy_id) AS total_jobs, {_STATUS_SUM}, count(pt.legacy_id) AS parse_texts"
    f" FROM {RAW_PARSE_JOB} pj"
    f" LEFT JOIN {RAW_PARSE_TEXT} pt ON pt.parse_job_legacy_id = pj.legacy_id"
)

PARSER_STATUS_COUNTS_QUERY = text(
    f"SELECT parse_status, count(legacy_id) AS count FROM {RAW_PARSE_JOB}"
    " GROUP BY parse_status ORDER BY parse_status ASC"
)

PARSER_BREAKDOWN_QUERY = text(
    f"SELECT pj.parser_name, pj.parser_version, count(pj.legacy_id) AS total_jobs, {_STATUS_SUM},"
    " count(pt.legacy_id) AS parse_texts, avg(pt.char_count) AS average_char_count"
    f" FROM {RAW_PARSE_JOB} pj"
    f" LEFT JOIN {RAW_PARSE_TEXT} pt ON pt.parse_job_legacy_id = pj.legacy_id"
    " GROUP BY pj.parser_name, pj.parser_version"
    " ORDER BY pj.parser_name ASC, pj.parser_version DESC"
)


def _review_candidates_query(strategy: str, *, filter_forecast_correction: bool) -> Any:
    where = (
        " WHERE pj.parse_status = 'completed' AND pj.text_path IS NOT NULL"
        " AND pj.parser_name = :parser_name AND pj.parser_version = :parser_version"
    )
    if filter_forecast_correction:
        where += " AND (d.title LIKE '%業績予想%' OR d.title LIKE '%予想値%')"
        # i.e. title LIKE '%業績予想%' OR title LIKE '%予想値%' (escaped so the
        # file stays readable through tools that mangle non-ASCII source).
    if strategy == "random":
        order = " ORDER BY random()"
    else:
        # "recent" and "forecast-correction" both want newest-parsed-first;
        # so does the "suspicious"/default branch's bulk fetch, scored and
        # re-sorted in review.py afterwards -- mirrors the legacy ORM's
        # `DocumentParseJobRecord.parsed_at.desc(), .id.desc()`.
        order = " ORDER BY pj.parsed_at DESC, pj.legacy_id DESC"
    return text(
        "SELECT pj.legacy_id AS parse_job_id, f.legacy_id AS file_id, d.disclosure_id, d.code, d.name,"
        " d.title, d.disclosure_date, f.storage_path AS pdf_path, f.file_size_bytes,"
        " pj.text_path, pj.parser_name, pj.parser_version, pt.pages_json"
        f" FROM {RAW_PARSE_JOB} pj"
        f" JOIN {RAW_FILE} f ON f.legacy_id = pj.file_legacy_id"
        f" JOIN {RAW_DISCLOSURE} d ON d.disclosure_id = f.disclosure_id"
        f" LEFT JOIN {RAW_PARSE_TEXT} pt ON pt.parse_job_legacy_id = pj.legacy_id"
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


async def fetch_files_for_disclosures(conn: Any, disclosure_ids: Sequence[str]) -> list[dict]:
    if not disclosure_ids:
        return []
    result = await conn.execute(FILES_FOR_DISCLOSURES_QUERY, {"disclosure_ids": list(disclosure_ids)})
    return _rows(result)


async def fetch_disclosure_files(
    conn: Any,
    *,
    disclosure_id: Optional[str] = None,
    download_status: Optional[str] = None,
    file_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    result = await conn.execute(
        DISCLOSURE_FILES_QUERY,
        {
            "disclosure_id": disclosure_id,
            "download_status": download_status,
            "file_type": file_type,
            "limit": limit,
            "offset": offset,
        },
    )
    return _rows(result)


async def count_disclosure_files(
    conn: Any,
    *,
    disclosure_id: Optional[str] = None,
    download_status: Optional[str] = None,
    file_type: Optional[str] = None,
) -> int:
    result = await conn.execute(
        DISCLOSURE_FILES_COUNT_QUERY,
        {"disclosure_id": disclosure_id, "download_status": download_status, "file_type": file_type},
    )
    row = result.first()
    if row is None:
        return 0
    return int(row[0] or 0)


async def fetch_parse_jobs_for_files(conn: Any, file_legacy_ids: Sequence[int]) -> list[dict]:
    if not file_legacy_ids:
        return []
    result = await conn.execute(PARSE_JOBS_FOR_FILES_QUERY, {"file_legacy_ids": list(file_legacy_ids)})
    return _rows(result)


async def fetch_analysis_results_for_files(conn: Any, file_legacy_ids: Sequence[int]) -> list[dict]:
    if not file_legacy_ids:
        return []
    result = await conn.execute(ANALYSIS_RESULTS_FOR_FILES_QUERY, {"file_legacy_ids": list(file_legacy_ids)})
    return _rows(result)


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


async def fetch_parser_totals(conn: Any) -> dict:
    # Positional, matching PARSER_TOTALS_QUERY's SELECT column order exactly
    # (total_jobs, completed_jobs, failed_jobs, running_jobs, pending_jobs,
    # parse_texts) -- avoids relying on a real Row's ._mapping, which a
    # simple test fake tuple doesn't have.
    result = await conn.execute(PARSER_TOTALS_QUERY)
    row = result.first()
    if row is None:
        row = (0, 0, 0, 0, 0, 0)
    return {
        "total_jobs": int(row[0] or 0),
        "completed_jobs": int(row[1] or 0),
        "failed_jobs": int(row[2] or 0),
        "running_jobs": int(row[3] or 0),
        "pending_jobs": int(row[4] or 0),
        "parse_texts": int(row[5] or 0),
    }


async def fetch_parser_status_counts(conn: Any) -> list[dict]:
    result = await conn.execute(PARSER_STATUS_COUNTS_QUERY)
    return _rows(result)


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
