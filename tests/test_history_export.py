"""Tests for tdnet/history_export.py, using fake async row sources -- no
real database connection."""
import datetime
import hashlib
import inspect
import json

import pytest

from tdnet.history_export import (
    ExportError,
    _normalize_database_url,
    _write_manifest,
    main,
    manifest_digest,
)
from tdnet.history_transform import TransformError, transform_report_tag


async def _rows(items):
    for item in items:
        yield item


_TAG_ROW = {
    "slug": "earnings_release", "label_ja": "決算発表", "label_en": "Earnings release",
    "description": "", "priority": 100, "active": True,
    "created_at": datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc),
    "updated_at": datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc),
}


@pytest.mark.asyncio
async def test_write_manifest_writes_one_line_per_row(tmp_path):
    output = tmp_path / "report_tag.ndjson"
    count = await _write_manifest(
        _rows([_TAG_ROW, {**_TAG_ROW, "slug": "forecast_revision"}]),
        transform_report_tag, output,
    )
    assert count == 2
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["slug"] == "earnings_release"
    assert json.loads(lines[1])["slug"] == "forecast_revision"


@pytest.mark.asyncio
async def test_write_manifest_refuses_to_overwrite_an_existing_file(tmp_path):
    output = tmp_path / "exists.ndjson"
    output.write_text("x")
    with pytest.raises(FileExistsError):
        await _write_manifest(_rows([]), transform_report_tag, output)


@pytest.mark.asyncio
async def test_write_manifest_propagates_transform_errors(tmp_path):
    output = tmp_path / "bad.ndjson"
    with pytest.raises(TransformError):
        await _write_manifest(_rows([{**_TAG_ROW, "slug": None}]), transform_report_tag, output)


def test_manifest_digest_is_sha256_of_file_bytes(tmp_path):
    path = tmp_path / "m.ndjson"
    path.write_bytes(b"hello\n")
    assert manifest_digest(path) == hashlib.sha256(b"hello\n").hexdigest()


@pytest.mark.asyncio
async def test_write_manifest_is_byte_identical_on_rerun(tmp_path):
    rows = [_TAG_ROW, {**_TAG_ROW, "slug": "forecast_revision", "active": False}]
    first = tmp_path / "first.ndjson"
    second = tmp_path / "second.ndjson"
    await _write_manifest(_rows(rows), transform_report_tag, first)
    await _write_manifest(_rows(rows), transform_report_tag, second)
    assert first.read_bytes() == second.read_bytes()
    assert manifest_digest(first) == manifest_digest(second)


def test_export_uses_repeatable_read_read_only_transaction():
    import tdnet.history_export as export_module
    source = inspect.getsource(export_module._export_all)
    assert 'isolation="repeatable_read"' in source
    assert "readonly=True" in source


def test_export_table_queries_order_by_natural_key():
    from tdnet.history_export import _TABLES
    expected_order_by = {
        "report_tag": "slug", "disclosure": "id", "file": "id", "parse_job": "id",
        "parse_text": "id", "analysis_result": "id", "report_tag_assignment": "id",
    }
    assert {stem: order_by for stem, _table, order_by, _cols, _t in _TABLES} == expected_order_by


def test_main_fails_closed_when_output_dir_missing(tmp_path, capsys):
    rc = main(["--output-dir", str(tmp_path / "missing")])
    assert rc == 1
    assert capsys.readouterr().err == "export_failed\n"


def test_normalize_database_url_strips_a_driver_suffix_from_the_scheme():
    assert (_normalize_database_url("postgresql+asyncpg://tdnet:tdnet@localhost:55432/tdnet")
            == "postgresql://tdnet:tdnet@localhost:55432/tdnet")


def test_normalize_database_url_passes_through_a_bare_scheme_unchanged():
    url = "postgresql://tdnet:tdnet@localhost:55432/tdnet"
    assert _normalize_database_url(url) == url


def test_normalize_database_url_passes_through_a_non_url_string_unchanged():
    # No "://" to split on -- returned as-is so the eventual asyncpg.connect()
    # failure (not this helper) reports the problem.
    assert _normalize_database_url("not-a-url") == "not-a-url"


def test_main_falls_back_to_tdnet_config_default_when_database_url_unset(tmp_path, monkeypatch):
    """DATABASE_URL unset must fall back to tdnet.config's own
    DEFAULT_DATABASE_URL (the same resolution tdnet.config.get_settings()
    performs elsewhere in this repo), normalized to a bare asyncpg scheme --
    never fail closed just because the env var wasn't exported. asyncpg.connect
    is faked throughout, so this never reaches a real socket."""
    import tdnet.history_export as export_module
    from tdnet.config import DEFAULT_DATABASE_URL

    monkeypatch.delenv("DATABASE_URL", raising=False)
    seen = {}

    class _FakeTransaction:
        def __init__(self, **kwargs):
            seen["transaction_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *unused):
            return False

    class _FakeConnection:
        async def set_type_codec(self, *args, **kwargs):
            return None

        def transaction(self, **kwargs):
            return _FakeTransaction(**kwargs)

        def cursor(self, query):
            async def _empty():
                return
                yield  # pragma: no cover -- makes this an async generator

            return _empty()

        async def close(self):
            return None

    async def _fake_connect(url):
        seen["connect_url"] = url
        return _FakeConnection()

    monkeypatch.setattr(export_module.asyncpg, "connect", _fake_connect)
    rc = main(["--output-dir", str(tmp_path)])
    assert rc == 0
    assert seen["connect_url"] == _normalize_database_url(DEFAULT_DATABASE_URL)
    assert "+asyncpg" not in seen["connect_url"]
    assert seen["transaction_kwargs"] == {"isolation": "repeatable_read", "readonly": True}


def test_export_error_is_a_value_error():
    assert issubclass(ExportError, ValueError)
