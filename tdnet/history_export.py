"""Read the legacy tdnet-postgres database (read-only) and write seven
immutable, content-addressed NDJSON manifests, one per raw_tdnet_history
table. Never writes to any database. The legacy connection comes only
from the DATABASE_URL environment variable already documented in
.env.example, falling back to tdnet.config's own DEFAULT_DATABASE_URL the
same way tdnet.config.get_settings() does -- never printed or persisted
here.

All seven tables are read inside a single REPEATABLE READ READ ONLY
transaction, so every manifest is a consistent snapshot of the same
moment even though the tables are read one after another."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import asyncpg

from tdnet.config import get_settings
from tdnet.history_transform import (
    canonical_bytes,
    transform_analysis_result,
    transform_disclosure,
    transform_file,
    transform_parse_job,
    transform_parse_text,
    transform_report_tag,
    transform_report_tag_assignment,
)

# (output filename stem, legacy table, ORDER BY, legacy columns, transform)
_TABLES = (
    ("report_tag", "tdnet_report_tags", "slug", (
        "slug", "label_ja", "label_en", "description", "priority", "active",
        "created_at", "updated_at",
    ), transform_report_tag),
    ("disclosure", "tdnet_disclosures", "id", (
        "id", "disclosure_date", "time", "code", "name", "title", "pdf_url",
        "xbrl_available", "xbrl_url", "place", "history", "created_at", "updated_at",
    ), transform_disclosure),
    ("file", "disclosure_files", "id", (
        "id", "disclosure_id", "file_type", "source_url", "source_file_id",
        "storage_bucket", "storage_path", "content_type", "file_size_bytes",
        "sha256", "download_status", "download_attempts", "downloaded_at",
        "last_download_error", "created_at", "updated_at",
    ), transform_file),
    ("parse_job", "document_parse_jobs", "id", (
        "id", "file_id", "parser_name", "parser_version", "parse_status",
        "parse_attempts", "text_path", "text_sha256", "parsed_at",
        "last_parse_error", "created_at", "updated_at",
    ), transform_parse_job),
    ("parse_text", "document_parse_texts", "id", (
        "id", "parse_job_id", "content_text", "pages_json", "page_count",
        "char_count", "content_sha256", "created_at", "updated_at",
    ), transform_parse_text),
    ("analysis_result", "document_analysis_results", "id", (
        "id", "file_id", "parse_job_id", "analysis_type", "analyzer_name",
        "analyzer_version", "status", "result_json", "result_text",
        "analyzed_at", "last_analysis_error", "created_at", "updated_at",
    ), transform_analysis_result),
    ("report_tag_assignment", "tdnet_report_tag_assignments", "id", (
        "id", "disclosure_id", "tag_slug", "file_id", "parse_job_id",
        "is_primary", "confidence", "source", "evidence_json", "tagger_name",
        "tagger_version", "created_at", "updated_at",
    ), transform_report_tag_assignment),
)


class ExportError(ValueError):
    """A sanitized export failure."""


def _normalize_database_url(database_url: str) -> str:
    """Drop a SQLAlchemy-style `+driver` suffix from the scheme (e.g.
    `postgresql+asyncpg://` -> `postgresql://`), since asyncpg.connect()
    only accepts the bare `postgresql`/`postgres` scheme. tdnet.config's
    DEFAULT_DATABASE_URL -- and so the repo's own DATABASE_URL convention
    -- is written in the SQLAlchemy form everywhere else in this repo."""
    scheme, separator, rest = database_url.partition("://")
    if not separator:
        return database_url
    return f"{scheme.split('+', 1)[0]}://{rest}"


async def _register_json_codecs(connection: asyncpg.Connection) -> None:
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog",
        )


async def _write_manifest(rows, transform, output_path: Path) -> int:
    """Stream an async iterable of legacy-row dicts through `transform` into
    one NDJSON line each. `rows` only ever needs to support `async for`, so
    this is exercised in tests with a fake async generator -- no real
    database connection -- while production passes a live asyncpg cursor."""
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    count = 0
    with output_path.open("x", encoding="utf-8") as handle:
        async for legacy_row in rows:
            transformed = transform(legacy_row)
            handle.write(canonical_bytes(transformed).decode("utf-8"))
            handle.write("\n")
            count += 1
    return count


async def _export_table(connection, legacy_table, order_by, columns, transform, output_path: Path) -> int:
    query = f"SELECT {', '.join(columns)} FROM {legacy_table} ORDER BY {order_by}"

    async def _rows():
        async for record in connection.cursor(query):
            yield dict(record)

    return await _write_manifest(_rows(), transform, output_path)


def manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _export_all(database_url: str, output_dir: Path) -> dict:
    connection = await asyncpg.connect(database_url)
    try:
        await _register_json_codecs(connection)
        results = {}
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            for stem, legacy_table, order_by, columns, transform in _TABLES:
                output_path = output_dir / f"{stem}.ndjson"
                count = await _export_table(
                    connection, legacy_table, order_by, columns, transform, output_path,
                )
                results[stem] = {
                    "exported_rows": count,
                    "output_path": str(output_path),
                    "manifest_digest": manifest_digest(output_path),
                }
    finally:
        await connection.close()
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the legacy tdnet-postgres database to seven NDJSON manifests")
    parser.add_argument("--output-dir", required=True)
    try:
        args = parser.parse_args(argv)
        output_dir = Path(args.output_dir)
        if not output_dir.is_dir():
            raise ExportError("invalid output directory")
        # Same resolution order as the rest of the repo (tdnet.config.get_settings):
        # DATABASE_URL if set, else tdnet.config.DEFAULT_DATABASE_URL.
        database_url = _normalize_database_url(get_settings().database_url)
        results = asyncio.run(_export_all(database_url, output_dir))
        print(json.dumps(results, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False))
        return 0
    except (ExportError, FileExistsError, OSError, asyncpg.PostgresError,
            asyncpg.exceptions.ClientConfigurationError):
        print("export_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
