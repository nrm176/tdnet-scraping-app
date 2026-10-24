"""Pure transforms from legacy tdnet-postgres rows (as returned by the
export query, i.e. plain Python str/int/bool/None/date/datetime/dict
values) into the JSON-safe shapes the seven raw_tdnet_history tables
store. No I/O, no database.

Every transform computes a sha256 "entry id" over a canonical JSON
encoding of the row's own source fields (in a fixed field order), so the
same legacy row always produces the same id and any field change produces
a different one. Dates and timestamps are carried as ISO strings; the one
double-precision float column (confidence) is carried as the string
produced by Python's repr(), never as a JSON number, so the same textual
value is hashed, stored and re-inserted on any later rerun regardless of
any json/float library version difference."""
import hashlib
import json

REPORT_TAG_FIELD_ORDER = (
    "slug", "label_ja", "label_en", "description", "priority", "active",
    "created_at", "updated_at",
)
DISCLOSURE_FIELD_ORDER = (
    "disclosure_id", "disclosure_date", "time", "code", "name", "title",
    "pdf_url", "xbrl_available", "xbrl_url", "place", "history",
    "created_at", "updated_at",
)
FILE_FIELD_ORDER = (
    "legacy_id", "disclosure_id", "file_type", "source_url", "source_file_id",
    "storage_bucket", "storage_path", "content_type", "file_size_bytes",
    "sha256", "download_status", "download_attempts", "downloaded_at",
    "last_download_error", "created_at", "updated_at",
)
PARSE_JOB_FIELD_ORDER = (
    "legacy_id", "file_legacy_id", "parser_name", "parser_version",
    "parse_status", "parse_attempts", "text_path", "text_sha256",
    "parsed_at", "last_parse_error", "created_at", "updated_at",
)
PARSE_TEXT_FIELD_ORDER = (
    "legacy_id", "parse_job_legacy_id", "content_text", "pages_json",
    "page_count", "char_count", "content_sha256", "created_at", "updated_at",
)
ANALYSIS_RESULT_FIELD_ORDER = (
    "legacy_id", "file_legacy_id", "parse_job_legacy_id", "analysis_type",
    "analyzer_name", "analyzer_version", "status", "result_json",
    "result_text", "analyzed_at", "last_analysis_error", "created_at",
    "updated_at",
)
REPORT_TAG_ASSIGNMENT_FIELD_ORDER = (
    "legacy_id", "disclosure_id", "tag_slug", "file_legacy_id",
    "parse_job_legacy_id", "is_primary", "confidence", "source",
    "evidence_json", "tagger_name", "tagger_version", "created_at",
    "updated_at",
)


class TransformError(ValueError):
    """A sanitized transform failure (malformed legacy row)."""


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_id(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _to_iso(value):
    if value is None:
        return None
    return value.isoformat()


def _require(value, message):
    if value in (None, ""):
        raise TransformError(message)
    return value


def transform_report_tag(legacy_row: dict) -> dict:
    slug = _require(legacy_row.get("slug"), "report_tag row missing required slug")
    out = {
        "slug": slug,
        "label_ja": legacy_row["label_ja"],
        "label_en": legacy_row["label_en"],
        "description": legacy_row["description"],
        "priority": legacy_row["priority"],
        "active": legacy_row["active"],
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in REPORT_TAG_FIELD_ORDER}
    out["report_tag_entry_id"] = content_id(hashed)
    return out


def transform_disclosure(legacy_row: dict) -> dict:
    disclosure_id = _require(legacy_row.get("id"), "disclosure row missing required id")
    pdf_url = _require(legacy_row.get("pdf_url"), "disclosure row missing required pdf_url")
    out = {
        "disclosure_id": disclosure_id,
        "disclosure_date": _to_iso(legacy_row["disclosure_date"]),
        "time": legacy_row["time"],
        "code": legacy_row["code"],
        "name": legacy_row["name"],
        "title": legacy_row["title"],
        "pdf_url": pdf_url,
        "xbrl_available": legacy_row["xbrl_available"],
        "xbrl_url": legacy_row.get("xbrl_url"),
        "place": legacy_row["place"],
        "history": legacy_row["history"],
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in DISCLOSURE_FIELD_ORDER}
    out["disclosure_entry_id"] = content_id(hashed)
    return out


def transform_file(legacy_row: dict) -> dict:
    legacy_id = legacy_row.get("id")
    disclosure_id = _require(legacy_row.get("disclosure_id"), "file row missing required disclosure_id")
    if legacy_id is None:
        raise TransformError("file row missing required id")
    out = {
        "legacy_id": legacy_id,
        "disclosure_id": disclosure_id,
        "file_type": legacy_row["file_type"],
        "source_url": legacy_row["source_url"],
        "source_file_id": legacy_row["source_file_id"],
        "storage_bucket": legacy_row["storage_bucket"],
        "storage_path": legacy_row["storage_path"],
        "content_type": legacy_row.get("content_type"),
        "file_size_bytes": legacy_row.get("file_size_bytes"),
        "sha256": legacy_row.get("sha256"),
        "download_status": legacy_row["download_status"],
        "download_attempts": legacy_row["download_attempts"],
        "downloaded_at": _to_iso(legacy_row.get("downloaded_at")),
        "last_download_error": legacy_row.get("last_download_error"),
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in FILE_FIELD_ORDER}
    out["file_entry_id"] = content_id(hashed)
    return out


def transform_parse_job(legacy_row: dict) -> dict:
    legacy_id = legacy_row.get("id")
    file_legacy_id = legacy_row.get("file_id")
    if legacy_id is None:
        raise TransformError("parse_job row missing required id")
    if file_legacy_id is None:
        raise TransformError("parse_job row missing required file_id")
    out = {
        "legacy_id": legacy_id,
        "file_legacy_id": file_legacy_id,
        "parser_name": legacy_row["parser_name"],
        "parser_version": legacy_row["parser_version"],
        "parse_status": legacy_row["parse_status"],
        "parse_attempts": legacy_row["parse_attempts"],
        "text_path": legacy_row.get("text_path"),
        "text_sha256": legacy_row.get("text_sha256"),
        "parsed_at": _to_iso(legacy_row.get("parsed_at")),
        "last_parse_error": legacy_row.get("last_parse_error"),
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in PARSE_JOB_FIELD_ORDER}
    out["parse_job_entry_id"] = content_id(hashed)
    return out


def transform_parse_text(legacy_row: dict) -> dict:
    legacy_id = legacy_row.get("id")
    parse_job_legacy_id = legacy_row.get("parse_job_id")
    if legacy_id is None:
        raise TransformError("parse_text row missing required id")
    if parse_job_legacy_id is None:
        raise TransformError("parse_text row missing required parse_job_id")
    out = {
        "legacy_id": legacy_id,
        "parse_job_legacy_id": parse_job_legacy_id,
        "content_text": legacy_row["content_text"],
        "pages_json": legacy_row.get("pages_json"),
        "page_count": legacy_row["page_count"],
        "char_count": legacy_row["char_count"],
        "content_sha256": legacy_row["content_sha256"],
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in PARSE_TEXT_FIELD_ORDER}
    out["parse_text_entry_id"] = content_id(hashed)
    return out


def transform_analysis_result(legacy_row: dict) -> dict:
    legacy_id = legacy_row.get("id")
    file_legacy_id = legacy_row.get("file_id")
    if legacy_id is None:
        raise TransformError("analysis_result row missing required id")
    if file_legacy_id is None:
        raise TransformError("analysis_result row missing required file_id")
    out = {
        "legacy_id": legacy_id,
        "file_legacy_id": file_legacy_id,
        "parse_job_legacy_id": legacy_row.get("parse_job_id"),
        "analysis_type": legacy_row["analysis_type"],
        "analyzer_name": legacy_row["analyzer_name"],
        "analyzer_version": legacy_row["analyzer_version"],
        "status": legacy_row["status"],
        "result_json": legacy_row.get("result_json"),
        "result_text": legacy_row.get("result_text"),
        "analyzed_at": _to_iso(legacy_row.get("analyzed_at")),
        "last_analysis_error": legacy_row.get("last_analysis_error"),
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in ANALYSIS_RESULT_FIELD_ORDER}
    out["analysis_result_entry_id"] = content_id(hashed)
    return out


def transform_report_tag_assignment(legacy_row: dict) -> dict:
    legacy_id = legacy_row.get("id")
    disclosure_id = _require(legacy_row.get("disclosure_id"),
                              "report_tag_assignment row missing required disclosure_id")
    tag_slug = _require(legacy_row.get("tag_slug"),
                         "report_tag_assignment row missing required tag_slug")
    if legacy_id is None:
        raise TransformError("report_tag_assignment row missing required id")
    out = {
        "legacy_id": legacy_id,
        "disclosure_id": disclosure_id,
        "tag_slug": tag_slug,
        "file_legacy_id": legacy_row.get("file_id"),
        "parse_job_legacy_id": legacy_row.get("parse_job_id"),
        "is_primary": legacy_row["is_primary"],
        "confidence": repr(legacy_row["confidence"]),
        "source": legacy_row["source"],
        "evidence_json": legacy_row.get("evidence_json"),
        "tagger_name": legacy_row["tagger_name"],
        "tagger_version": legacy_row["tagger_version"],
        "created_at": _to_iso(legacy_row["created_at"]),
        "updated_at": _to_iso(legacy_row["updated_at"]),
    }
    hashed = {key: out[key] for key in REPORT_TAG_ASSIGNMENT_FIELD_ORDER}
    out["report_tag_assignment_entry_id"] = content_id(hashed)
    return out
