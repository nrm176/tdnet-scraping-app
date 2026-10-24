"""Live-database tests against the seven raw_tdnet_history tables, same
convention as tests/test_research_store.py: skipped unless the controller
supplies HFT_DISCLOSURE_TEST_DATABASE_URL for an already-migrated
(sql/research/001-004) disposable database. FK and GRANT behavior only
manifests against a real schema, so these cannot be faked."""
import os
from datetime import datetime, timezone

import asyncpg
import pytest

TEST_DSN = os.environ.get("HFT_DISCLOSURE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="requires controller disposable PostgreSQL DSN")


async def _insert_report_tag(connection, slug, entry_id):
    now = datetime.now(timezone.utc)
    await connection.execute(
        "INSERT INTO raw_tdnet_history.report_tag"
        " (report_tag_entry_id, slug, label_ja, label_en, description, priority, active,"
        " created_at, updated_at, migration_batch_id, migrated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, $9, $8)"
        " ON CONFLICT (report_tag_entry_id) DO NOTHING",
        entry_id, slug, slug, slug, "", 100, True, now, "a" * 64,
    )


async def _insert_disclosure(connection, disclosure_id, pdf_url, entry_id):
    now = datetime.now(timezone.utc)
    await connection.execute(
        "INSERT INTO raw_tdnet_history.disclosure"
        " (disclosure_entry_id, disclosure_id, disclosure_date, time, code, name, title,"
        " pdf_url, xbrl_available, place, history, created_at, updated_at,"
        " migration_batch_id, migrated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, false, $9, '', $10, $10, $11, $10)"
        " ON CONFLICT (disclosure_entry_id) DO NOTHING",
        entry_id, disclosure_id, now.date(), "15:00", "13010", "name", "title",
        pdf_url, "東", now, "b" * 64,
    )


@pytest.mark.asyncio
async def test_tdnet_ingest_can_insert_and_select_but_not_update_report_tag():
    entry_id = "1" * 64
    connection = await asyncpg.connect(TEST_DSN)
    try:
        await _insert_report_tag(connection, "test_slug_1", entry_id)
        row = await connection.fetchrow(
            "SELECT report_tag_entry_id FROM raw_tdnet_history.report_tag WHERE report_tag_entry_id=$1",
            entry_id,
        )
        assert row["report_tag_entry_id"] == entry_id
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await connection.execute(
                "UPDATE raw_tdnet_history.report_tag SET label_ja='x' WHERE report_tag_entry_id=$1",
                entry_id,
            )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_file_fk_rejects_an_orphan_disclosure_id():
    connection = await asyncpg.connect(TEST_DSN)
    try:
        now = datetime.now(timezone.utc)
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await connection.execute(
                "INSERT INTO raw_tdnet_history.file"
                " (file_entry_id, legacy_id, disclosure_id, file_type, source_url, source_file_id,"
                " storage_bucket, storage_path, download_status, download_attempts,"
                " created_at, updated_at, migration_batch_id, migrated_at)"
                " VALUES ($1, $2, 'NEVER_IMPORTED_DISCLOSURE', 'pdf', 'https://x/y.pdf', 'y',"
                " 'tdnet', '/p.pdf', 'completed', 1, $3, $3, $4, $3)",
                "2" * 64, 999999, now, "c" * 64,
            )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_parse_job_fk_rejects_an_orphan_file_legacy_id():
    connection = await asyncpg.connect(TEST_DSN)
    try:
        now = datetime.now(timezone.utc)
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await connection.execute(
                "INSERT INTO raw_tdnet_history.parse_job"
                " (parse_job_entry_id, legacy_id, file_legacy_id, parser_name, parser_version,"
                " parse_status, parse_attempts, created_at, updated_at, migration_batch_id, migrated_at)"
                " VALUES ($1, $2, $3, 'pymupdf4llm', '1', 'completed', 1, $4, $4, $5, $4)",
                "3" * 64, 888888, 777777, now, "d" * 64,
            )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_report_tag_assignment_fk_rejects_an_orphan_tag_slug():
    disclosure_entry_id = "4" * 64
    disclosure_id = "0004165d7ed92b64"
    connection = await asyncpg.connect(TEST_DSN)
    try:
        await _insert_disclosure(
            connection, disclosure_id, "https://www.release.tdnet.info/inbs/schema-test.pdf",
            disclosure_entry_id,
        )
        now = datetime.now(timezone.utc)
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await connection.execute(
                "INSERT INTO raw_tdnet_history.report_tag_assignment"
                " (report_tag_assignment_entry_id, legacy_id, disclosure_id, tag_slug, is_primary,"
                " confidence, source, tagger_name, tagger_version, created_at, updated_at,"
                " migration_batch_id, migrated_at)"
                " VALUES ($1, $2, $3, 'never_registered_tag', true, 0.5, 'title', 'tagger', '1',"
                " $4, $4, $5, $4)",
                "5" * 64, 666666, disclosure_id, now, "e" * 64,
            )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_tdnet_ingest_can_insert_report_tag_assignment_with_null_optional_fields():
    disclosure_entry_id = "6" * 64
    disclosure_id = "0006165d7ed92b66"
    tag_entry_id = "7" * 64
    connection = await asyncpg.connect(TEST_DSN)
    try:
        await _insert_disclosure(
            connection, disclosure_id, "https://www.release.tdnet.info/inbs/schema-test-2.pdf",
            disclosure_entry_id,
        )
        await _insert_report_tag(connection, "test_slug_2", tag_entry_id)
        now = datetime.now(timezone.utc)
        assignment_entry_id = "8" * 64
        await connection.execute(
            "INSERT INTO raw_tdnet_history.report_tag_assignment"
            " (report_tag_assignment_entry_id, legacy_id, disclosure_id, tag_slug, file_legacy_id,"
            " parse_job_legacy_id, is_primary, confidence, source, evidence_json, tagger_name,"
            " tagger_version, created_at, updated_at, migration_batch_id, migrated_at)"
            " VALUES ($1, $2, $3, 'test_slug_2', NULL, NULL, true, 0.5::double precision, 'title',"
            " NULL, 'tagger', '1', $4, $4, $5, $4)"
            " ON CONFLICT (report_tag_assignment_entry_id) DO NOTHING",
            assignment_entry_id, 555555, disclosure_id, now, "f" * 64,
        )
        row = await connection.fetchrow(
            "SELECT file_legacy_id, parse_job_legacy_id, evidence_json"
            " FROM raw_tdnet_history.report_tag_assignment WHERE report_tag_assignment_entry_id=$1",
            assignment_entry_id,
        )
        assert row["file_legacy_id"] is None
        assert row["parse_job_legacy_id"] is None
        assert row["evidence_json"] is None
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_research_reader_has_select_only_on_history_tables():
    connection = await asyncpg.connect(TEST_DSN)
    try:
        tables = (
            "report_tag", "disclosure", "file", "parse_job", "parse_text",
            "analysis_result", "report_tag_assignment",
        )
        for table in tables:
            qualified = f"raw_tdnet_history.{table}"
            can_select, can_insert, can_update = await connection.fetchrow(
                "SELECT has_table_privilege('research_reader', $1, 'SELECT'),"
                " has_table_privilege('research_reader', $1, 'INSERT'),"
                " has_table_privilege('research_reader', $1, 'UPDATE')",
                qualified,
            )
            assert can_select is True, table
            assert can_insert is False, table
            assert can_update is False, table
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_tdnet_ingest_has_insert_and_select_but_not_update_or_delete():
    connection = await asyncpg.connect(TEST_DSN)
    try:
        tables = (
            "report_tag", "disclosure", "file", "parse_job", "parse_text",
            "analysis_result", "report_tag_assignment",
        )
        for table in tables:
            qualified = f"raw_tdnet_history.{table}"
            can_select, can_insert, can_update, can_delete = await connection.fetchrow(
                "SELECT has_table_privilege('tdnet_ingest', $1, 'SELECT'),"
                " has_table_privilege('tdnet_ingest', $1, 'INSERT'),"
                " has_table_privilege('tdnet_ingest', $1, 'UPDATE'),"
                " has_table_privilege('tdnet_ingest', $1, 'DELETE')",
                qualified,
            )
            assert can_select is True, table
            assert can_insert is True, table
            assert can_update is False, table
            assert can_delete is False, table
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_public_has_no_privileges_on_history_tables():
    connection = await asyncpg.connect(TEST_DSN)
    try:
        tables = (
            "report_tag", "disclosure", "file", "parse_job", "parse_text",
            "analysis_result", "report_tag_assignment",
        )
        for table in tables:
            qualified = f"raw_tdnet_history.{table}"
            can_select = await connection.fetchval(
                "SELECT has_table_privilege('public', $1, 'SELECT')", qualified,
            )
            assert can_select is False, table
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_store_report_tag_batch_is_idempotent_on_rerun_live():
    from tdnet.history_transform import transform_report_tag
    from tdnet.history_store import store_report_tag_batch

    now = datetime.now(timezone.utc)
    token = now.strftime("%H%M%S%f")
    row = transform_report_tag({
        "slug": f"live_test_{token}", "label_ja": "x", "label_en": "x", "description": "",
        "priority": 1, "active": True, "created_at": now, "updated_at": now,
    })
    first = await store_report_tag_batch([row], "9" * 64, TEST_DSN, now)
    assert first["inserted"] == 1
    second = await store_report_tag_batch([row], "9" * 64, TEST_DSN, now)
    assert second["inserted"] == 0
    assert second["skipped_existing"] == 1


@pytest.mark.asyncio
async def test_store_report_tag_batch_rejects_a_tampered_row_live():
    from tdnet.history_store import StoreError, store_report_tag_batch
    from tdnet.history_transform import transform_report_tag

    now = datetime.now(timezone.utc)
    token = now.strftime("%H%M%S%f")
    row = transform_report_tag({
        "slug": f"live_test_tamper_{token}", "label_ja": "x", "label_en": "x", "description": "",
        "priority": 1, "active": True, "created_at": now, "updated_at": now,
    })
    row["label_ja"] = "改ざん"
    with pytest.raises(StoreError):
        await store_report_tag_batch([row], "a" * 64, TEST_DSN, now)
