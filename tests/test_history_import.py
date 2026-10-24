"""Tests for tdnet/history_import.py's CLI failure path and ordering --
mirrors tests/test_history_store.py's level of fakery: the store_*_batch
functions are patched out, so no real database connection is used."""
from unittest.mock import patch

import pytest

import tdnet.history_import as import_module

_FLAGS = (
    "--report-tag-manifest", "--disclosure-manifest", "--file-manifest",
    "--parse-job-manifest", "--parse-text-manifest", "--analysis-result-manifest",
    "--report-tag-assignment-manifest",
)


def _manifest_args(tmp_path, write=True):
    args = []
    for flag in _FLAGS:
        name = flag.lstrip("-").replace("-manifest", "").replace("-", "_")
        path = tmp_path / f"{name}.ndjson"
        if write:
            path.write_text("")
        args.extend([flag, str(path)])
    return args


def test_main_fails_closed_when_a_manifest_is_missing(tmp_path, capsys):
    rc = import_module.main(_manifest_args(tmp_path, write=False))
    assert rc == 1
    assert capsys.readouterr().err == "import_failed\n"


def test_main_fails_closed_when_database_url_unset(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HFT_RESEARCH_DATABASE_URL", raising=False)
    rc = import_module.main(_manifest_args(tmp_path))
    assert rc == 1
    assert capsys.readouterr().err == "import_failed\n"


def test_main_prints_import_failed_and_returns_1_on_store_error(tmp_path, monkeypatch, capsys):
    args = _manifest_args(tmp_path)
    (tmp_path / "report_tag.ndjson").write_text('{"report_tag_entry_id": "x"}\n')
    monkeypatch.setenv("HFT_RESEARCH_DATABASE_URL", "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research")
    with patch.object(import_module, "store_report_tag_batch",
                       side_effect=import_module.StoreError("boom")):
        rc = import_module.main(args)
    assert rc == 1
    assert capsys.readouterr().err == "import_failed\n"


def test_main_imports_tables_in_fk_safe_order(tmp_path, monkeypatch):
    expected_order = [
        "report_tag", "disclosure", "file", "parse_job", "parse_text",
        "analysis_result", "report_tag_assignment",
    ]
    args = _manifest_args(tmp_path, write=False)
    for flag in _FLAGS:
        name = flag.lstrip("-").replace("-manifest", "").replace("-", "_")
        path = tmp_path / f"{name}.ndjson"
        path.write_text(f'{{"{name}_entry_id": "x"}}\n')
    monkeypatch.setenv("HFT_RESEARCH_DATABASE_URL", "postgresql://tdnet_ingest:x@127.0.0.1:54790/hft_research")
    calls = []

    def _fake_store(name):
        async def _inner(batch, manifest_digest, database_url, migrated_at):
            calls.append(name)
            return {"inserted": len(batch), "skipped_existing": 0, "total": len(batch)}
        return _inner

    patches = [
        patch.object(import_module, f"store_{name}_batch", side_effect=_fake_store(name))
        for name in expected_order
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        rc = import_module.main(args)
    assert rc == 0
    assert calls == expected_order


@pytest.mark.asyncio
async def test_import_manifest_is_idempotent_with_a_fake_store_rerun():
    calls = []

    async def _fake_store(batch, manifest_digest, database_url, migrated_at):
        calls.append(len(batch))
        # First call "inserts" every row; a rerun with the same manifest_digest
        # and rows is expected (at the real-store layer, via ON CONFLICT DO
        # NOTHING) to insert 0 -- simulate that here directly.
        inserted = len(batch) if len(calls) == 1 else 0
        return {"inserted": inserted, "skipped_existing": len(batch) - inserted, "total": len(batch)}

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "report_tag.ndjson"
        manifest_path.write_text('{"report_tag_entry_id": "x"}\n{"report_tag_entry_id": "y"}\n')
        first = await import_module._import_manifest(manifest_path, _fake_store, "dsn", None)
        second = await import_module._import_manifest(manifest_path, _fake_store, "dsn", None)
    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["skipped_existing"] == 2


@pytest.mark.asyncio
async def test_import_manifest_batches_at_the_configured_batch_size():
    import tempfile
    from pathlib import Path

    calls = []

    async def _fake_store(batch, manifest_digest, database_url, migrated_at):
        calls.append(len(batch))
        return {"inserted": len(batch), "skipped_existing": 0, "total": len(batch)}

    rows = "\n".join(f'{{"report_tag_entry_id": "{i}"}}' for i in range(import_module._BATCH_SIZE + 1)) + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "report_tag.ndjson"
        manifest_path.write_text(rows)
        result = await import_module._import_manifest(manifest_path, _fake_store, "dsn", None)
    assert calls == [import_module._BATCH_SIZE, 1]
    assert result["total"] == import_module._BATCH_SIZE + 1
