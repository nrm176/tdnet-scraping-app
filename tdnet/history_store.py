"""Transactional, append-only asyncpg persistence for the TDnet history
backfill. Only ever INSERTs into the seven raw_tdnet_history tables; never
UPDATE, never DELETE (the tdnet_ingest role itself is not granted those
privileges on this schema -- see sql/research/004_tdnet_history.sql --
this module's own restraint is a second, redundant line of defense, not
the only one).

Connects exactly like tdnet.research_store: HFT_RESEARCH_DATABASE_URL,
validated by the same tdnet.research_store.validate_database_url (role
tdnet_ingest, database hft_research, or a controller-supplied disposable
test database), with the same current_database/current_user/server
version and pipeline.instance_identity marker checks before any write."""
from __future__ import annotations

import asyncpg

from tdnet.history_transform import (
    ANALYSIS_RESULT_FIELD_ORDER,
    DISCLOSURE_FIELD_ORDER,
    FILE_FIELD_ORDER,
    PARSE_JOB_FIELD_ORDER,
    PARSE_TEXT_FIELD_ORDER,
    REPORT_TAG_ASSIGNMENT_FIELD_ORDER,
    REPORT_TAG_FIELD_ORDER,
    content_id,
)
from tdnet.research_store import validate_database_url

__all__ = [
    "StoreError",
    "store_report_tag_batch",
    "store_disclosure_batch",
    "store_file_batch",
    "store_parse_job_batch",
    "store_parse_text_batch",
    "store_analysis_result_batch",
    "store_report_tag_assignment_batch",
]


class StoreError(ValueError):
    """A sanitized store validation or consistency failure."""


_TABLE_SPECS = {
    "report_tag": {
        "table": "raw_tdnet_history.report_tag",
        "entry_id_column": "report_tag_entry_id",
        "field_order": REPORT_TAG_FIELD_ORDER,
        "column_types": {
            "priority": "integer", "active": "boolean",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_report_tag",
    },
    "disclosure": {
        "table": "raw_tdnet_history.disclosure",
        "entry_id_column": "disclosure_entry_id",
        "field_order": DISCLOSURE_FIELD_ORDER,
        "column_types": {
            "disclosure_date": "date", "xbrl_available": "boolean",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_disclosure",
    },
    "file": {
        "table": "raw_tdnet_history.file",
        "entry_id_column": "file_entry_id",
        "field_order": FILE_FIELD_ORDER,
        "column_types": {
            "legacy_id": "integer", "file_size_bytes": "integer",
            "download_attempts": "integer", "downloaded_at": "timestamptz",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_file",
    },
    "parse_job": {
        "table": "raw_tdnet_history.parse_job",
        "entry_id_column": "parse_job_entry_id",
        "field_order": PARSE_JOB_FIELD_ORDER,
        "column_types": {
            "legacy_id": "integer", "file_legacy_id": "integer",
            "parse_attempts": "integer", "parsed_at": "timestamptz",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_parse_job",
    },
    "parse_text": {
        "table": "raw_tdnet_history.parse_text",
        "entry_id_column": "parse_text_entry_id",
        "field_order": PARSE_TEXT_FIELD_ORDER,
        "column_types": {
            "legacy_id": "integer", "parse_job_legacy_id": "integer",
            "pages_json": "jsonb", "page_count": "integer",
            "char_count": "integer",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_parse_text",
    },
    "analysis_result": {
        "table": "raw_tdnet_history.analysis_result",
        "entry_id_column": "analysis_result_entry_id",
        "field_order": ANALYSIS_RESULT_FIELD_ORDER,
        "column_types": {
            "legacy_id": "integer", "file_legacy_id": "integer",
            "parse_job_legacy_id": "integer", "result_json": "jsonb",
            "analyzed_at": "timestamptz",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_analysis_result",
    },
    "report_tag_assignment": {
        "table": "raw_tdnet_history.report_tag_assignment",
        "entry_id_column": "report_tag_assignment_entry_id",
        "field_order": REPORT_TAG_ASSIGNMENT_FIELD_ORDER,
        "column_types": {
            "legacy_id": "integer", "file_legacy_id": "integer",
            "parse_job_legacy_id": "integer", "is_primary": "boolean",
            "confidence": "double precision", "evidence_json": "jsonb",
            "created_at": "timestamptz", "updated_at": "timestamptz",
        },
        "lock_key": "tdnet_history_report_tag_assignment",
    },
}


async def _validate_connection_marker(connection, expected_database: str) -> None:
    identity = await connection.fetchrow(
        "SELECT current_database() AS database, current_user AS role, "
        "current_setting('server_version_num') AS version"
    )
    if (
        identity["database"] != expected_database
        or identity["role"] != "tdnet_ingest"
        or not 180000 <= int(identity["version"]) < 190000
    ):
        raise StoreError("connected database identity is not approved")
    marker = await connection.fetchrow(
        "SELECT instance_name, schema_version FROM pipeline.instance_identity "
        "WHERE instance_name = 'hft_research'"
    )
    if (
        marker is None
        or marker["instance_name"] != "hft_research"
        or marker["schema_version"] != 1
    ):
        raise StoreError("connected database marker is not approved")


def _verify_row(row: dict, field_order: tuple, entry_id_column: str) -> dict:
    try:
        payload = {key: row[key] for key in field_order}
    except KeyError as exc:
        raise StoreError(f"manifest row missing required field: {exc}") from exc
    expected = content_id(payload)
    if row.get(entry_id_column) != expected:
        raise StoreError(f"{entry_id_column} does not match its own content")
    return row


def _build_insert(spec: dict, row_count: int) -> str:
    columns = [spec["entry_id_column"], *spec["field_order"], "migration_batch_id", "migrated_at"]
    casts = spec["column_types"]
    value_groups = []
    index = 1
    for _ in range(row_count):
        placeholders = []
        for column in columns:
            cast = casts.get(column)
            placeholders.append(f"${index}::{cast}" if cast else f"${index}")
            index += 1
        value_groups.append(f"({', '.join(placeholders)})")
    return (
        f"INSERT INTO {spec['table']} ({', '.join(columns)}) "
        f"VALUES {', '.join(value_groups)} "
        f"ON CONFLICT ({spec['entry_id_column']}) DO NOTHING"
    )


def _row_values(row: dict, spec: dict, manifest_digest: str, migrated_at) -> list:
    values = [row[spec["entry_id_column"]]]
    values.extend(row[field] for field in spec["field_order"])
    values.append(manifest_digest)
    values.append(migrated_at)
    return values


def _parse_inserted_count(status: str) -> int:
    # asyncpg's Connection.execute() returns the command tag, e.g. "INSERT 0 123".
    return int(status.rsplit(" ", 1)[1])


async def _store_batch(table_key: str, rows: list, manifest_digest: str,
                        database_url: str, migrated_at) -> dict:
    spec = _TABLE_SPECS[table_key]
    try:
        expected_database = validate_database_url(database_url)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc
    verified = [_verify_row(row, spec["field_order"], spec["entry_id_column"]) for row in rows]
    connection = await asyncpg.connect(database_url)
    try:
        await _validate_connection_marker(connection, expected_database)
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", spec["lock_key"],
            )
            if verified:
                sql = _build_insert(spec, len(verified))
                params = [
                    value
                    for row in verified
                    for value in _row_values(row, spec, manifest_digest, migrated_at)
                ]
                status = await connection.execute(sql, *params)
                inserted = _parse_inserted_count(status)
            else:
                inserted = 0
    finally:
        await connection.close()
    return {"inserted": inserted, "skipped_existing": len(verified) - inserted, "total": len(verified)}


async def store_report_tag_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("report_tag", rows, manifest_digest, database_url, migrated_at)


async def store_disclosure_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("disclosure", rows, manifest_digest, database_url, migrated_at)


async def store_file_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("file", rows, manifest_digest, database_url, migrated_at)


async def store_parse_job_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("parse_job", rows, manifest_digest, database_url, migrated_at)


async def store_parse_text_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("parse_text", rows, manifest_digest, database_url, migrated_at)


async def store_analysis_result_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("analysis_result", rows, manifest_digest, database_url, migrated_at)


async def store_report_tag_assignment_batch(rows: list, manifest_digest: str, database_url: str, migrated_at) -> dict:
    return await _store_batch("report_tag_assignment", rows, manifest_digest, database_url, migrated_at)
