"""Tests for tdnet/history_transform.py -- pure functions, no I/O."""
import datetime
import json

import pytest

from tdnet.history_transform import (
    TransformError,
    content_id,
    transform_analysis_result,
    transform_disclosure,
    transform_file,
    transform_parse_job,
    transform_parse_text,
    transform_report_tag,
    transform_report_tag_assignment,
)

_CREATED = datetime.datetime(2026, 5, 1, 3, 0, 0, tzinfo=datetime.timezone.utc)
_UPDATED = datetime.datetime(2026, 5, 1, 4, 0, 0, tzinfo=datetime.timezone.utc)


def _report_tag_row(**overrides):
    row = {
        "slug": "earnings_release", "label_ja": "決算発表", "label_en": "Earnings release",
        "description": "", "priority": 100, "active": True,
        "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_report_tag_slug_is_natural_key():
    out = transform_report_tag(_report_tag_row())
    assert out["slug"] == "earnings_release"
    assert len(out["report_tag_entry_id"]) == 64


def test_transform_report_tag_rejects_null_slug():
    with pytest.raises(TransformError):
        transform_report_tag(_report_tag_row(slug=None))


def test_transform_report_tag_id_deterministic():
    first = transform_report_tag(_report_tag_row())
    second = transform_report_tag(_report_tag_row())
    assert first["report_tag_entry_id"] == second["report_tag_entry_id"]


def test_transform_report_tag_id_changes_per_field():
    base = transform_report_tag(_report_tag_row())["report_tag_entry_id"]
    for overrides in (
        {"label_ja": "別の表示"}, {"label_en": "Other"}, {"description": "x"},
        {"priority": 1}, {"active": False},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_report_tag(_report_tag_row(**overrides))["report_tag_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def _disclosure_row(**overrides):
    row = {
        "id": "0003165d7ed92b63", "disclosure_date": datetime.date(2026, 5, 1),
        "time": "15:00", "code": "13010", "name": "株式会社サンプル",
        "title": "決算短信", "pdf_url": "https://www.release.tdnet.info/inbs/140120260515539038.pdf",
        "xbrl_available": True, "xbrl_url": "https://www.release.tdnet.info/inbs/140120260515539038.zip",
        "place": "東", "history": "", "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_disclosure_id_is_legacy_id():
    out = transform_disclosure(_disclosure_row())
    assert out["disclosure_id"] == "0003165d7ed92b63"
    assert out["disclosure_date"] == "2026-05-01"


def test_transform_disclosure_rejects_null_id():
    with pytest.raises(TransformError):
        transform_disclosure(_disclosure_row(id=None))


def test_transform_disclosure_rejects_null_pdf_url():
    with pytest.raises(TransformError):
        transform_disclosure(_disclosure_row(pdf_url=None))


def test_transform_disclosure_preserves_null_xbrl_url():
    out = transform_disclosure(_disclosure_row(xbrl_available=False, xbrl_url=None))
    assert out["xbrl_url"] is None


def test_transform_disclosure_id_deterministic():
    first = transform_disclosure(_disclosure_row())["disclosure_entry_id"]
    second = transform_disclosure(_disclosure_row())["disclosure_entry_id"]
    assert first == second
    assert len(first) == 64


def test_transform_disclosure_id_changes_per_field():
    base = transform_disclosure(_disclosure_row())["disclosure_entry_id"]
    for overrides in (
        {"disclosure_date": datetime.date(2026, 5, 2)}, {"time": "15:30"},
        {"code": "13020"}, {"name": "別会社"}, {"title": "訂正"},
        {"pdf_url": "https://www.release.tdnet.info/inbs/other.pdf"},
        {"xbrl_available": False}, {"xbrl_url": None}, {"place": "名"},
        {"history": "x"},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_disclosure(_disclosure_row(**overrides))["disclosure_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def _file_row(**overrides):
    row = {
        "id": 42, "disclosure_id": "0003165d7ed92b63", "file_type": "pdf",
        "source_url": "https://www.release.tdnet.info/inbs/x.pdf",
        "source_file_id": "140120260515539038", "storage_bucket": "tdnet",
        "storage_path": "/Volumes/yakushimachi/Downloads/tdnet/140120260515539038/140120260515539038.pdf",
        "content_type": "application/pdf", "file_size_bytes": 102400,
        "sha256": "a" * 64, "download_status": "completed", "download_attempts": 1,
        "downloaded_at": _CREATED, "last_download_error": None,
        "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_file_legacy_id_kept_as_integer():
    out = transform_file(_file_row())
    assert out["legacy_id"] == 42


def test_transform_file_rejects_null_id():
    with pytest.raises(TransformError):
        transform_file(_file_row(id=None))


def test_transform_file_rejects_null_disclosure_id():
    with pytest.raises(TransformError):
        transform_file(_file_row(disclosure_id=None))


def test_transform_file_preserves_null_sha256_for_failed_download():
    out = transform_file(_file_row(sha256=None, download_status="failed", downloaded_at=None))
    assert out["sha256"] is None
    assert out["downloaded_at"] is None


def test_transform_file_id_deterministic():
    first = transform_file(_file_row())["file_entry_id"]
    second = transform_file(_file_row())["file_entry_id"]
    assert first == second


def test_transform_file_id_changes_per_field():
    base = transform_file(_file_row())["file_entry_id"]
    for overrides in (
        {"disclosure_id": "other"}, {"file_type": "xbrl"}, {"source_url": "https://x/y.pdf"},
        {"source_file_id": "999"}, {"storage_bucket": "tdnet-forecast-correction"},
        {"storage_path": "/other/path.pdf"}, {"content_type": None},
        {"file_size_bytes": 1}, {"sha256": "b" * 64}, {"download_status": "failed"},
        {"download_attempts": 2}, {"downloaded_at": None}, {"last_download_error": "boom"},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_file(_file_row(**overrides))["file_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def _parse_job_row(**overrides):
    row = {
        "id": 7, "file_id": 42, "parser_name": "pymupdf4llm", "parser_version": "0.3.4+tdnet-1",
        "parse_status": "completed", "parse_attempts": 1,
        "text_path": "/Volumes/yakushimachi/Downloads/tdnet/140120260515539038/parsed/pymupdf4llm.0.3.4+tdnet-1.md",
        "text_sha256": "c" * 64, "parsed_at": _CREATED, "last_parse_error": None,
        "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_parse_job_keys():
    out = transform_parse_job(_parse_job_row())
    assert out["legacy_id"] == 7
    assert out["file_legacy_id"] == 42


def test_transform_parse_job_rejects_null_id():
    with pytest.raises(TransformError):
        transform_parse_job(_parse_job_row(id=None))


def test_transform_parse_job_rejects_null_file_id():
    with pytest.raises(TransformError):
        transform_parse_job(_parse_job_row(file_id=None))


def test_transform_parse_job_preserves_null_text_fields_for_failed_parse():
    out = transform_parse_job(_parse_job_row(
        parse_status="failed", text_path=None, text_sha256=None, parsed_at=None,
        last_parse_error="OCR failed"))
    assert out["text_path"] is None
    assert out["text_sha256"] is None
    assert out["parsed_at"] is None


def test_transform_parse_job_id_changes_per_field():
    base = transform_parse_job(_parse_job_row())["parse_job_entry_id"]
    for overrides in (
        {"file_id": 43}, {"parser_name": "apple-vision-ocr"}, {"parser_version": "1"},
        {"parse_status": "failed"}, {"parse_attempts": 2}, {"text_path": None},
        {"text_sha256": "d" * 64}, {"parsed_at": None}, {"last_parse_error": "x"},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_parse_job(_parse_job_row(**overrides))["parse_job_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def _parse_text_row(**overrides):
    row = {
        "id": 99, "parse_job_id": 7, "content_text": "決算短信の本文です。",
        "pages_json": {"pages": [1, 2, 3]}, "page_count": 3, "char_count": 11,
        "content_sha256": "e" * 64, "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_parse_text_keys():
    out = transform_parse_text(_parse_text_row())
    assert out["legacy_id"] == 99
    assert out["parse_job_legacy_id"] == 7


def test_transform_parse_text_rejects_null_id():
    with pytest.raises(TransformError):
        transform_parse_text(_parse_text_row(id=None))


def test_transform_parse_text_rejects_null_parse_job_id():
    with pytest.raises(TransformError):
        transform_parse_text(_parse_text_row(parse_job_id=None))


def test_transform_parse_text_preserves_null_pages_json():
    out = transform_parse_text(_parse_text_row(pages_json=None))
    assert out["pages_json"] is None


def test_transform_parse_text_jsonb_canonicalization_is_key_order_independent():
    a = transform_parse_text(_parse_text_row(pages_json={"b": 2, "a": 1}))
    b = transform_parse_text(_parse_text_row(pages_json={"a": 1, "b": 2}))
    assert a["parse_text_entry_id"] == b["parse_text_entry_id"]


def test_transform_parse_text_jsonb_canonicalization_has_no_whitespace():
    from tdnet.history_transform import canonical_bytes
    out = transform_parse_text(_parse_text_row(pages_json={"a": 1, "b": [1, 2]}))
    hashed = {"pages_json": out["pages_json"]}
    encoded = canonical_bytes(hashed)
    assert b" " not in encoded
    assert json.loads(encoded)["pages_json"] == {"a": 1, "b": [1, 2]}


def test_transform_parse_text_id_changes_per_field():
    base = transform_parse_text(_parse_text_row())["parse_text_entry_id"]
    for overrides in (
        {"parse_job_id": 8}, {"content_text": "別の本文"}, {"pages_json": None},
        {"page_count": 4}, {"char_count": 12}, {"content_sha256": "f" * 64},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_parse_text(_parse_text_row(**overrides))["parse_text_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def _analysis_result_row(**overrides):
    row = {
        "id": 500, "file_id": 42, "parse_job_id": 7, "analysis_type": "financial_facts",
        "analyzer_name": "tdnet-deterministic-financial-facts", "analyzer_version": "2",
        "status": "completed", "result_json": {"revenue": 100}, "result_text": None,
        "analyzed_at": _CREATED, "last_analysis_error": None,
        "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_analysis_result_keys():
    out = transform_analysis_result(_analysis_result_row())
    assert out["legacy_id"] == 500
    assert out["file_legacy_id"] == 42
    assert out["parse_job_legacy_id"] == 7


def test_transform_analysis_result_rejects_null_id():
    with pytest.raises(TransformError):
        transform_analysis_result(_analysis_result_row(id=None))


def test_transform_analysis_result_rejects_null_file_id():
    with pytest.raises(TransformError):
        transform_analysis_result(_analysis_result_row(file_id=None))


def test_transform_analysis_result_preserves_null_parse_job_id():
    out = transform_analysis_result(_analysis_result_row(parse_job_id=None))
    assert out["parse_job_legacy_id"] is None


def test_transform_analysis_result_id_changes_per_field():
    base = transform_analysis_result(_analysis_result_row())["analysis_result_entry_id"]
    for overrides in (
        {"file_id": 43}, {"parse_job_id": None}, {"analysis_type": "other"},
        {"analyzer_name": "other"}, {"analyzer_version": "3"}, {"status": "failed"},
        {"result_json": None}, {"result_text": "x"}, {"analyzed_at": None},
        {"last_analysis_error": "x"},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_analysis_result(_analysis_result_row(**overrides))["analysis_result_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def _report_tag_assignment_row(**overrides):
    row = {
        "id": 1000, "disclosure_id": "0003165d7ed92b63", "tag_slug": "earnings_release",
        "file_id": 42, "parse_job_id": 7, "is_primary": True, "confidence": 0.95,
        "source": "title", "evidence_json": {"matched": "決算短信"},
        "tagger_name": "tdnet-deterministic-hybrid", "tagger_version": "5",
        "created_at": _CREATED, "updated_at": _UPDATED,
    }
    row.update(overrides)
    return row


def test_transform_report_tag_assignment_keys():
    out = transform_report_tag_assignment(_report_tag_assignment_row())
    assert out["legacy_id"] == 1000
    assert out["disclosure_id"] == "0003165d7ed92b63"
    assert out["tag_slug"] == "earnings_release"


def test_transform_report_tag_assignment_rejects_null_id():
    with pytest.raises(TransformError):
        transform_report_tag_assignment(_report_tag_assignment_row(id=None))


def test_transform_report_tag_assignment_rejects_null_disclosure_id():
    with pytest.raises(TransformError):
        transform_report_tag_assignment(_report_tag_assignment_row(disclosure_id=None))


def test_transform_report_tag_assignment_rejects_null_tag_slug():
    with pytest.raises(TransformError):
        transform_report_tag_assignment(_report_tag_assignment_row(tag_slug=None))


def test_transform_report_tag_assignment_preserves_null_file_and_parse_job():
    out = transform_report_tag_assignment(_report_tag_assignment_row(file_id=None, parse_job_id=None))
    assert out["file_legacy_id"] is None
    assert out["parse_job_legacy_id"] is None


def test_transform_report_tag_assignment_confidence_is_repr_string():
    out = transform_report_tag_assignment(_report_tag_assignment_row(confidence=0.1))
    assert out["confidence"] == repr(0.1)
    assert isinstance(out["confidence"], str)


def test_transform_report_tag_assignment_confidence_round_trips_exactly():
    # 0.1 + 0.2 != 0.3 in binary floating point; repr() must still carry the
    # exact double-precision value through canonical JSON and back.
    value = 0.1 + 0.2
    out = transform_report_tag_assignment(_report_tag_assignment_row(confidence=value))
    from tdnet.history_transform import canonical_bytes
    encoded = canonical_bytes({"confidence": out["confidence"]})
    round_tripped = float(json.loads(encoded)["confidence"])
    assert round_tripped == value


def test_transform_report_tag_assignment_id_changes_per_field():
    base = transform_report_tag_assignment(_report_tag_assignment_row())["report_tag_assignment_entry_id"]
    for overrides in (
        {"disclosure_id": "other"}, {"tag_slug": "dividend_distribution"},
        {"file_id": None}, {"parse_job_id": None}, {"is_primary": False},
        {"confidence": 0.5}, {"source": "content"}, {"evidence_json": None},
        {"tagger_name": "other"}, {"tagger_version": "6"},
        {"created_at": _CREATED + datetime.timedelta(seconds=1)},
        {"updated_at": _UPDATED + datetime.timedelta(seconds=1)},
    ):
        changed = transform_report_tag_assignment(
            _report_tag_assignment_row(**overrides))["report_tag_assignment_entry_id"]
        assert changed != base, f"id did not change for {overrides}"


def test_content_id_is_sha256_hex_of_canonical_json():
    import hashlib
    value = {"b": 2, "a": 1}
    expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    assert content_id(value) == expected
