"""Tests for tdnet/history_store.py, faking asyncpg.connect -- no real
database connection. Mirrors tests/test_research_store.py's Connection/
Transaction fake pattern."""
import datetime
from unittest.mock import AsyncMock, patch

import pytest

import tdnet.history_store as store
from tdnet.history_transform import (
    transform_disclosure,
    transform_file,
    transform_parse_text,
    transform_report_tag,
    transform_report_tag_assignment,
)

_URL = "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research"
_MIGRATED_AT = datetime.datetime(2026, 10, 24, tzinfo=datetime.timezone.utc)


class Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, *unused): return False


class Connection:
    def __init__(self, marker=None, status="INSERT 0 0", identity=None):
        self.calls = []
        self.closed = False
        self.marker = marker if marker is not None else {"instance_name": "hft_research", "schema_version": 1}
        self.status = status
        self.identity = identity or {"database": "hft_research", "role": "tdnet_ingest", "version": "180001"}

    def transaction(self):
        return Transaction()

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "current_database" in sql:
            return self.identity
        if "instance_identity" in sql:
            return self.marker
        return None

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.status

    async def close(self):
        self.closed = True


def _tag_row(**overrides):
    row = {
        "slug": "earnings_release", "label_ja": "決算発表", "label_en": "Earnings release",
        "description": "", "priority": 100, "active": True,
        "created_at": _MIGRATED_AT, "updated_at": _MIGRATED_AT,
    }
    row.update(overrides)
    return transform_report_tag(row)


def _assignment_row(**overrides):
    row = {
        "id": 1, "disclosure_id": "0003165d7ed92b63", "tag_slug": "earnings_release",
        "file_id": 42, "parse_job_id": 7, "is_primary": True, "confidence": 0.95,
        "source": "title", "evidence_json": {"matched": "決算短信"},
        "tagger_name": "tdnet-deterministic-hybrid", "tagger_version": "5",
        "created_at": _MIGRATED_AT, "updated_at": _MIGRATED_AT,
    }
    row.update(overrides)
    return transform_report_tag_assignment(row)


def _parse_text_row(**overrides):
    row = {
        "id": 1, "parse_job_id": 7, "content_text": "本文", "pages_json": {"a": 1},
        "page_count": 1, "char_count": 2, "content_sha256": "e" * 64,
        "created_at": _MIGRATED_AT, "updated_at": _MIGRATED_AT,
    }
    row.update(overrides)
    return transform_parse_text(row)


@pytest.mark.asyncio
async def test_store_rejects_a_tampered_entry_id_before_connecting():
    row = _tag_row()
    row["label_ja"] = "改ざん"  # mutate after report_tag_entry_id was already computed
    with patch.object(store.asyncpg, "connect", new=AsyncMock()) as connect:
        with pytest.raises(store.StoreError):
            await store.store_report_tag_batch([row], "a" * 64, _URL, _MIGRATED_AT)
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_rejects_a_row_missing_a_required_field():
    row = _tag_row()
    del row["priority"]
    with patch.object(store.asyncpg, "connect", new=AsyncMock()) as connect:
        with pytest.raises(store.StoreError):
            await store.store_report_tag_batch([row], "b" * 64, _URL, _MIGRATED_AT)
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_rejects_an_invalid_database_url():
    with pytest.raises(store.StoreError):
        await store.store_report_tag_batch(
            [_tag_row()], "c" * 64, "postgresql://wrong@127.0.0.1:1/x", _MIGRATED_AT)


@pytest.mark.asyncio
async def test_store_rejects_wrong_role_identity_and_closes_connection():
    connection = Connection(identity={"database": "hft_research", "role": "other", "version": "180001"})
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        with pytest.raises(store.StoreError):
            await store.store_report_tag_batch([_tag_row()], "d" * 64, _URL, _MIGRATED_AT)
    assert connection.closed


@pytest.mark.asyncio
async def test_store_rejects_wrong_schema_version_marker_and_closes_connection():
    connection = Connection(marker={"instance_name": "hft_research", "schema_version": 2})
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        with pytest.raises(store.StoreError):
            await store.store_report_tag_batch([_tag_row()], "e" * 64, _URL, _MIGRATED_AT)
    assert connection.closed


@pytest.mark.asyncio
async def test_store_acquires_a_distinct_advisory_lock_per_table():
    connection = Connection(status="INSERT 0 1")
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        await store.store_report_tag_batch([_tag_row()], "f" * 64, _URL, _MIGRATED_AT)
    lock_calls = [call for call in connection.calls if "pg_advisory_xact_lock" in call[1]]
    assert len(lock_calls) == 1
    assert lock_calls[0][2] == ("tdnet_history_report_tag",)


@pytest.mark.asyncio
async def test_store_insert_uses_on_conflict_do_nothing_on_entry_id():
    connection = Connection(status="INSERT 0 1")
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        await store.store_report_tag_batch([_tag_row()], "1" * 64, _URL, _MIGRATED_AT)
    insert_calls = [call for call in connection.calls if call[1].startswith("INSERT")]
    assert len(insert_calls) == 1
    assert "ON CONFLICT (report_tag_entry_id) DO NOTHING" in insert_calls[0][1]


@pytest.mark.asyncio
async def test_store_parses_inserted_count_from_command_tag():
    connection = Connection(status="INSERT 0 2")
    rows = [_tag_row(), _tag_row(slug="forecast_revision")]
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        result = await store.store_report_tag_batch(rows, "2" * 64, _URL, _MIGRATED_AT)
    assert result == {"inserted": 2, "skipped_existing": 0, "total": 2}


@pytest.mark.asyncio
async def test_store_reports_skipped_existing_when_command_tag_is_lower_than_total():
    connection = Connection(status="INSERT 0 1")
    rows = [_tag_row(), _tag_row(slug="forecast_revision")]
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        result = await store.store_report_tag_batch(rows, "3" * 64, _URL, _MIGRATED_AT)
    assert result == {"inserted": 1, "skipped_existing": 1, "total": 2}


@pytest.mark.asyncio
async def test_store_empty_batch_is_a_no_op():
    connection = Connection()
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        result = await store.store_report_tag_batch([], "4" * 64, _URL, _MIGRATED_AT)
    assert result == {"inserted": 0, "skipped_existing": 0, "total": 0}
    assert not any(call[1].startswith("INSERT") for call in connection.calls)
    assert connection.closed


@pytest.mark.asyncio
async def test_store_disclosure_rejects_tampered_natural_key():
    row = transform_disclosure({
        "id": "0003165d7ed92b63", "disclosure_date": datetime.date(2026, 5, 1), "time": "15:00",
        "code": "13010", "name": "株式会社サンプル", "title": "決算短信",
        "pdf_url": "https://www.release.tdnet.info/inbs/x.pdf", "xbrl_available": False,
        "xbrl_url": None, "place": "東", "history": "",
        "created_at": _MIGRATED_AT, "updated_at": _MIGRATED_AT,
    })
    row["title"] = "改ざん"
    with patch.object(store.asyncpg, "connect", new=AsyncMock()) as connect:
        with pytest.raises(store.StoreError):
            await store.store_disclosure_batch([row], "5" * 64, _URL, _MIGRATED_AT)
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_file_insert_casts_legacy_id_as_integer():
    connection = Connection(status="INSERT 0 1")
    row = transform_file({
        "id": 42, "disclosure_id": "0003165d7ed92b63", "file_type": "pdf",
        "source_url": "https://x/y.pdf", "source_file_id": "140120260515539038",
        "storage_bucket": "tdnet", "storage_path": "/Volumes/yakushimachi/Downloads/tdnet/x.pdf",
        "content_type": "application/pdf", "file_size_bytes": 1, "sha256": "a" * 64,
        "download_status": "completed", "download_attempts": 1,
        "downloaded_at": _MIGRATED_AT, "last_download_error": None,
        "created_at": _MIGRATED_AT, "updated_at": _MIGRATED_AT,
    })
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        await store.store_file_batch([row], "6" * 64, _URL, _MIGRATED_AT)
    insert_sql = next(call[1] for call in connection.calls if call[1].startswith("INSERT"))
    assert "::integer" in insert_sql


@pytest.mark.asyncio
async def test_store_report_tag_assignment_insert_casts_confidence_as_double_precision():
    connection = Connection(status="INSERT 0 1")
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        await store.store_report_tag_assignment_batch([_assignment_row()], "7" * 64, _URL, _MIGRATED_AT)
    insert_sql = next(call[1] for call in connection.calls if call[1].startswith("INSERT"))
    assert "::double precision" in insert_sql
    insert_params = next(call[2] for call in connection.calls if call[1].startswith("INSERT"))
    assert repr(0.95) in insert_params


@pytest.mark.asyncio
async def test_store_parse_text_insert_casts_pages_json_as_jsonb():
    connection = Connection(status="INSERT 0 1")
    with patch.object(store.asyncpg, "connect", new=AsyncMock(return_value=connection)):
        await store.store_parse_text_batch([_parse_text_row()], "8" * 64, _URL, _MIGRATED_AT)
    insert_sql = next(call[1] for call in connection.calls if call[1].startswith("INSERT"))
    assert "::jsonb" in insert_sql


def test_cast_columns_bind_as_text_so_postgres_parses_them():
    from datetime import datetime, timezone

    from tdnet import history_store as hs

    assert hs._as_text(None) is None
    assert hs._as_text("2026-05-17T13:27:35.033429+00:00") == "2026-05-17T13:27:35.033429+00:00"
    assert hs._as_text(True) == "true" and hs._as_text(False) == "false"
    assert hs._as_text(7) == "7"
    assert hs._as_text(0.1 + 0.2) == repr(0.1 + 0.2)
    assert hs._as_text({"b": 1, "a": [1, "x"]}) == '{"a":[1,"x"],"b":1}'
    assert hs._as_text(datetime(2026, 1, 2, tzinfo=timezone.utc)) == "2026-01-02T00:00:00+00:00"
    spec = hs._TABLE_SPECS["report_tag"]
    sql = hs._build_insert(spec, 1)
    for column, cast in spec["column_types"].items():
        assert f"::text::{cast}" in sql


def test_statement_parameters_stay_under_the_protocol_limit():
    from tdnet import history_store as hs

    for spec in hs._TABLE_SPECS.values():
        width = len(spec["field_order"]) + 3
        assert (hs._MAX_PARAMS // width) * width <= 32767
