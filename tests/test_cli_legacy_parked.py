"""Every `tdnet` CLI command that writes the legacy tdnet-postgres database
must refuse to run while it is parked (2026-10-25) -- see tdnet/database.py.

These tests exercise the CLI argument parser and handlers directly (never a
real scrape/download/DB connection): each legacy-DB command's handler calls
`tdnet.database.init_db()`/`SessionLocal()` as its first action, which raises
`LegacyLocalDbParkedError` before any network or database I/O happens.
"""
from __future__ import annotations

import logging
from datetime import date
from unittest.mock import patch

import pytest

from tdnet import cli
from tdnet.database import LegacyLocalDbParkedError
from tdnet.models import TdnetScrapingResult


@pytest.fixture(autouse=True)
def _parked_by_default(monkeypatch):
    monkeypatch.delenv("TDNET_LEGACY_LOCAL_ENABLED", raising=False)


# Minimal argv for every subcommand whose handler touches the legacy DB.
LEGACY_DB_SUBCOMMANDS = [
    ["list"],
    ["download"],
    ["parse"],
    ["ocr"],
    ["parse-ixbrl"],
    ["persist-parse-text"],
    ["tag-reports"],
    ["analyze-financials"],
    ["list-tags"],
    ["pipeline-run", "start", "--run-id", "r1", "--log-path", "p.log"],
    ["pipeline-run", "finish", "--run-id", "r1", "--status", "completed"],
    ["pipeline-run", "step-start", "--run-id", "r1", "--step-name", "s", "--step-order", "1"],
    ["pipeline-run", "step-finish", "--run-id", "r1", "--step-name", "s", "--status", "completed"],
    ["pipeline-run", "step-skip", "--run-id", "r1", "--step-name", "s", "--step-order", "1", "--reason", "x"],
]


@pytest.mark.parametrize("argv", LEGACY_DB_SUBCOMMANDS, ids=lambda argv: " ".join(argv))
def test_legacy_db_subcommand_refuses_without_touching_a_real_engine(argv):
    parser = cli._build_parser()
    args = parser.parse_args(argv)
    with patch("tdnet.database.create_async_engine") as mock_create:
        with pytest.raises(LegacyLocalDbParkedError):
            args.handler(args)
        mock_create.assert_not_called()


def test_run_wrapper_surfaces_the_parked_message_and_exits_nonzero(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.CRITICAL):
        exit_code = cli.run(["list"])
    assert exit_code == 1
    combined = "\n".join(record.message for record in caplog.records)
    assert "retired" in combined
    assert "raw_tdnet_history" in combined
    assert "Root issue #50" in combined


@pytest.mark.parametrize(
    "argv",
    [
        ["list", "--date", "2026-09-10"],
        ["download", "--limit", "5"],
    ],
    ids=lambda argv: " ".join(argv),
)
def test_run_wrapper_refuses_for_other_legacy_subcommands(tmp_path, monkeypatch, argv):
    monkeypatch.chdir(tmp_path)
    assert cli.run(argv) == 1


def test_scrape_without_persist_does_not_touch_the_legacy_db(monkeypatch):
    """scrape (no --persist) only hits the network layer, which this test
    mocks out entirely -- it must not raise the legacy-DB guard."""
    fake_result = TdnetScrapingResult(
        scraping_date=date(2026, 9, 10),
        total_disclosures=0,
        disclosures=[],
        pdf_urls=[],
    )
    with patch("tdnet.cli.scrape_tdnet_by_date", return_value=fake_result) as mock_scrape:
        exit_code = cli.run(["scrape", "--date", "2026-09-10"])
    mock_scrape.assert_called_once()
    assert exit_code == 0


def test_scrape_with_persist_refuses_the_legacy_db_write(monkeypatch):
    """scrape --persist must still refuse at the persist step; the network
    layer is mocked out so no real scrape happens either way."""
    fake_result = TdnetScrapingResult(
        scraping_date=date(2026, 9, 10),
        total_disclosures=0,
        disclosures=[],
        pdf_urls=[],
    )
    with patch("tdnet.cli.scrape_tdnet_by_date", return_value=fake_result) as mock_scrape:
        exit_code = cli.run(["scrape", "--date", "2026-09-10", "--persist"])
    mock_scrape.assert_called_once()
    assert exit_code == 1
