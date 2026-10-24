"""Read already-exported manifests (never the legacy database) and write
them, batched, into PG18's seven raw_tdnet_history tables. Connects via
HFT_RESEARCH_DATABASE_URL, exactly as tdnet.research_ingest does -- set by
`hedge_research.disclosure_environment exec tdnet -- ...` when invoked
through that wrapper.

Import order is fixed: tags, then disclosures, then files, then parse
jobs, then parse texts, then analysis results, then tag assignments --
each later table's foreign keys reference an earlier one, so a fresh
import always satisfies them. A rerun of the same manifests inserts 0."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

# Imported by name (not aliased) so that patching e.g.
# `import_module.store_report_tag_batch` in a test is honored: _import_all
# below looks these up from this module's own globals() at call time,
# rather than closing over the function objects at import time.
from tdnet.history_store import (  # noqa: F401
    StoreError,
    store_analysis_result_batch,
    store_disclosure_batch,
    store_file_batch,
    store_parse_job_batch,
    store_parse_text_batch,
    store_report_tag_assignment_batch,
    store_report_tag_batch,
)

_BATCH_SIZE = 2000

# (manifest CLI flag suffix, result key), in required import order. The
# store function for each is resolved by name (store_<result_key>_batch)
# at call time -- see _import_all.
_IMPORT_ORDER = (
    ("report-tag", "report_tag"),
    ("disclosure", "disclosure"),
    ("file", "file"),
    ("parse-job", "parse_job"),
    ("parse-text", "parse_text"),
    ("analysis-result", "analysis_result"),
    ("report-tag-assignment", "report_tag_assignment"),
)


class ImportError_(ValueError):
    """A sanitized import failure (named to avoid shadowing the builtin)."""


def _read_manifest(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line:
                yield json.loads(line)


async def _import_manifest(manifest_path: Path, store_batch_fn, database_url, migrated_at) -> dict:
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    totals = {"inserted": 0, "skipped_existing": 0, "total": 0}
    batch = []
    for row in _read_manifest(manifest_path):
        batch.append(row)
        if len(batch) >= _BATCH_SIZE:
            result = await store_batch_fn(batch, manifest_digest, database_url, migrated_at)
            for key in totals:
                totals[key] += result[key]
            batch = []
    if batch:
        result = await store_batch_fn(batch, manifest_digest, database_url, migrated_at)
        for key in totals:
            totals[key] += result[key]
    return {"manifest_digest": manifest_digest, **totals}


def _flag_to_dest(flag: str) -> str:
    return flag.replace("-", "_")


async def _import_all(manifest_paths: dict, database_url: str, migrated_at) -> dict:
    results = {}
    module_globals = globals()
    for _flag, result_key in _IMPORT_ORDER:
        store_batch_fn = module_globals[f"store_{result_key}_batch"]
        results[result_key] = await _import_manifest(
            manifest_paths[result_key], store_batch_fn, database_url, migrated_at,
        )
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Import the seven TDnet history manifests into PG18, in FK-safe order")
    for flag, _result_key in _IMPORT_ORDER:
        parser.add_argument(f"--{flag}-manifest", required=True)
    try:
        args = parser.parse_args(argv)
        manifest_paths = {}
        for flag, result_key in _IMPORT_ORDER:
            path = Path(getattr(args, f"{_flag_to_dest(flag)}_manifest"))
            if not path.is_file():
                raise ImportError_(f"manifest not found: {result_key}")
            manifest_paths[result_key] = path
        database_url = os.environ.get("HFT_RESEARCH_DATABASE_URL")
        if not database_url:
            raise ImportError_("HFT_RESEARCH_DATABASE_URL is not set")
        migrated_at = datetime.now(timezone.utc)
        results = asyncio.run(_import_all(manifest_paths, database_url, migrated_at))
        print(json.dumps({
            "migrated_at": migrated_at.isoformat(), **results,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0
    except (ImportError_, StoreError, OSError, json.JSONDecodeError, asyncpg.PostgresError):
        print("import_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
