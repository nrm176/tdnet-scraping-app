"""Command-line interface for scraping and querying TDnet disclosures."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from datetime import date, datetime

from .artifacts import download_pending_files
from .database import SessionLocal, init_db
from .financial_facts import (
    FINANCIAL_FACTS_ANALYZER_VERSION,
    analyze_financial_facts,
)
from .ixbrl_text import (
    IXBRL_TEXT_PARSER_NAME,
    get_ixbrl_text_parser_version,
    parse_ixbrl_text_pending,
)
from .logging_config import configure_logging
from .models import TdnetDisclosure, TdnetScrapingResult
from .ocr import APPLE_VISION_OCR_NAME, get_apple_vision_parser_version, ocr_pending_files
from .parse_texts import backfill_parse_texts
from .parsers import PARSER_NAME, default_parse_workers, get_parser_version, parse_pending_files
from .pipeline_runs import (
    error_context_from_file,
    finish_pipeline_run,
    finish_pipeline_step,
    parse_step_metrics_file,
    skip_pipeline_step,
    start_pipeline_run,
    start_pipeline_step,
)
from .repository import count_disclosures, query_disclosures, upsert_disclosures
from .review import build_parse_review_report
from .services import scrape_tdnet_by_date
from .stats_logging import format_seconds
from .tagging import list_report_tag_summaries, tag_reports


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD format") from exc


def _print_disclosures(disclosures: list[TdnetDisclosure], *, as_json: bool) -> None:
    if as_json:
        print(
            "["
            + ",".join(
                disclosure.model_dump_json(exclude_none=True)
                for disclosure in disclosures
            )
            + "]"
        )
        return

    for i, disclosure in enumerate(disclosures, 1):
        print(f"\n{i}. Time: {disclosure.time}")
        print(f"   Code: {disclosure.code}")
        print(f"   Company: {disclosure.name}")
        print(f"   Title: {disclosure.title}")
        print(f"   Exchange: {disclosure.place}")
        print(f"   PDF URL: {disclosure.pdf_url}")
        if disclosure.xbrl_available:
            print("   XBRL: Available")
            if disclosure.xbrl_url:
                print(f"   XBRL URL: {disclosure.xbrl_url}")
        print("-" * 50)


def _print_scrape_result(result: TdnetScrapingResult, output_format: str, as_json: bool) -> None:
    if output_format in ["urls", "both"]:
        if result.pdf_urls:
            print("\n--- PDF URLs ---")
            print(f"Total unique URLs found: {len(result.pdf_urls)}")
            print("----------------")
            for url in result.pdf_urls:
                print(str(url))
        else:
            logging.info("No PDF URLs were found for the specified date.")

    if output_format in ["structured", "both"]:
        if result.disclosures:
            print("\n--- Structured Data ---")
            print(f"Total disclosures found: {result.total_disclosures}")
            print("-----------------------")
            if as_json:
                print(result.model_dump_json(indent=2, exclude_none=True))
            else:
                _print_disclosures(result.disclosures, as_json=False)
        else:
            logging.info("No structured data was found for the specified date.")


def _resolve_parser_version(parser_name: str, parser_version: str | None) -> str | None:
    if parser_version is not None:
        return parser_version
    if parser_name == APPLE_VISION_OCR_NAME:
        return get_apple_vision_parser_version()
    if parser_name == IXBRL_TEXT_PARSER_NAME:
        return get_ixbrl_text_parser_version()
    if parser_name == PARSER_NAME:
        return get_parser_version()
    return None


def _parse_optional_json(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("JSON payload must be an object")
    return parsed


def _parse_optional_date(value: str | None) -> date | None:
    return _parse_date(value) if value else None


def _parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


async def _persist_result(result: TdnetScrapingResult) -> int:
    await init_db()
    async with SessionLocal() as session:
        return await upsert_disclosures(session, result.disclosures)


async def _list_persisted(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        total = await count_disclosures(
            session,
            disclosure_date=args.date,
            date_from=args.date_from,
            date_to=args.date_to,
            code=args.code,
        )
        disclosures = await query_disclosures(
            session,
            disclosure_date=args.date,
            date_from=args.date_from,
            date_to=args.date_to,
            code=args.code,
            limit=args.limit,
            offset=args.offset,
        )

    if args.json:
        print(
            "{"
            f'"total":{total},"limit":{args.limit},"offset":{args.offset},"disclosures":'
            + "["
            + ",".join(
                disclosure.model_dump_json(exclude_none=True)
                for disclosure in disclosures
            )
            + "]}"
        )
    else:
        print(f"Total persisted disclosures: {total}")
        _print_disclosures(disclosures, as_json=False)
    return 0


async def _download_persisted(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting download job limit=%s concurrency=%s retry_failed=%s root=%s",
        args.limit,
        args.concurrency,
        args.retry_failed,
        args.root,
    )
    async with SessionLocal() as session:
        summary = await download_pending_files(
            session,
            root=Path(args.root) if args.root else None,
            limit=args.limit,
            retry_failed=args.retry_failed,
            concurrency=args.concurrency,
        )
    logging.info(
        "Finished download job candidates=%s downloaded=%s skipped=%s failed=%s",
        summary.candidates,
        summary.downloaded,
        summary.skipped,
        summary.failed,
    )
    print(f"Candidate disclosures: {summary.candidates}")
    print(f"Downloaded files: {summary.downloaded}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Downloaded bytes: {summary.total_bytes}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    return 1 if summary.failed else 0


async def _parse_downloaded(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting parse job limit=%s workers=%s retry_failed=%s",
        args.limit,
        args.workers,
        args.retry_failed,
    )
    async with SessionLocal() as session:
        summary = await parse_pending_files(
            session,
            limit=args.limit,
            retry_failed=args.retry_failed,
            workers=args.workers,
        )
    logging.info(
        "Finished parse job candidates=%s parsed=%s skipped=%s failed=%s elapsed_seconds=%.3f estimated_remaining=%s",
        summary.candidates,
        summary.parsed,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending files: {summary.total_pending}")
    print(f"Candidate files: {summary.candidates}")
    print(f"Parsed files: {summary.parsed}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


async def _ocr_downloaded(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting OCR job strategy=%s limit=%s file_id=%s workers=%s retry_failed=%s",
        args.strategy,
        args.limit,
        args.file_id,
        args.workers,
        args.retry_failed,
    )
    async with SessionLocal() as session:
        summary = await ocr_pending_files(
            session,
            strategy=args.strategy,
            limit=args.limit,
            file_id=args.file_id,
            retry_failed=args.retry_failed,
            source_parser_version=args.source_parser_version,
            parser_version=args.parser_version,
            workers=args.workers,
        )
    logging.info(
        "Finished OCR job candidates=%s completed=%s skipped=%s failed=%s elapsed_seconds=%.3f "
        "estimated_remaining=%s",
        summary.candidates,
        summary.ocr_completed,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending OCR candidates: {summary.total_pending}")
    print(f"Candidate files: {summary.candidates}")
    print(f"OCR completed files: {summary.ocr_completed}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


async def _parse_ixbrl_text(args: argparse.Namespace) -> int:
    await init_db()
    logging.info(
        "Starting iXBRL text job strategy=%s limit=%s file_id=%s retry_failed=%s",
        args.strategy,
        args.limit,
        args.file_id,
        args.retry_failed,
    )
    async with SessionLocal() as session:
        summary = await parse_ixbrl_text_pending(
            session,
            strategy=args.strategy,
            limit=args.limit,
            file_id=args.file_id,
            retry_failed=args.retry_failed,
            source_parser_version=args.source_parser_version,
            parser_version=args.parser_version,
        )
    logging.info(
        "Finished iXBRL text job candidates=%s parsed=%s skipped=%s failed=%s elapsed_seconds=%.3f "
        "estimated_remaining=%s",
        summary.candidates,
        summary.parsed,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending iXBRL candidates: {summary.total_pending}")
    print(f"Candidate files: {summary.candidates}")
    print(f"Parsed files: {summary.parsed}")
    print(f"Skipped files: {summary.skipped}")
    print(f"Failed files: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


async def _review_parses(args: argparse.Namespace) -> int:
    # review-parse reads the research DB's migrated TDnet history views as
    # research_reader (tdnet/research_reader.py), not the legacy local
    # Postgres the rest of this CLI's commands write to -- so there is no
    # init_db()/SessionLocal here any more. This means a report generated
    # right after a --legacy-local scrape/parse run only shows disclosures
    # already migrated into the research DB, not ones that landed in the
    # local legacy DB moments ago (Root spec 2026-10-24, research DB
    # migration slice 7, §3.4 phase B).
    parser_version = _resolve_parser_version(args.parser_name, args.parser_version)
    report = await build_parse_review_report(
        output_root=Path(args.output_root),
        strategy=args.strategy,
        limit=args.limit,
        pages_per_file=args.pages_per_file,
        parser_name=args.parser_name,
        parser_version=parser_version,
        open_report=args.open,
    )
    print(f"Review strategy: {report.strategy}")
    print(f"Reviewed documents: {report.reviewed_count}")
    print(f"Report: {report.index_path.resolve()}")
    return 0


async def _persist_parse_texts(args: argparse.Namespace) -> int:
    await init_db()
    parser_version = None if args.all_versions else _resolve_parser_version(args.parser_name, args.parser_version)
    logging.info(
        "Starting parse text backfill parser_name=%s parser_version=%s limit=%s all_versions=%s",
        args.parser_name,
        parser_version,
        args.limit,
        args.all_versions,
    )
    async with SessionLocal() as session:
        summary = await backfill_parse_texts(
            session,
            parser_name=args.parser_name,
            parser_version=parser_version,
            limit=args.limit,
        )
    logging.info(
        "Finished parse text backfill candidates=%s persisted=%s skipped=%s failed=%s elapsed_seconds=%.3f "
        "estimated_remaining=%s",
        summary.candidates,
        summary.persisted,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
        format_seconds(summary.estimated_remaining_seconds),
    )
    print(f"Total pending parse text rows: {summary.total_pending}")
    print(f"Candidate parse jobs: {summary.candidates}")
    print(f"Persisted rows: {summary.persisted}")
    print(f"Skipped parse jobs: {summary.skipped}")
    print(f"Failed parse jobs: {summary.failed}")
    print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
    print(f"Files per second: {summary.files_per_second:.3f}")
    print(f"Average file seconds: {summary.average_file_seconds:.3f}")
    print(f"Median file seconds: {summary.median_file_seconds:.3f}")
    print(f"Estimated total time: {format_seconds(summary.estimated_total_seconds)}")
    print(f"Estimated remaining time: {format_seconds(summary.estimated_remaining_seconds)}")
    return 1 if summary.failed else 0


async def _tag_reports(args: argparse.Namespace) -> int:
    await init_db()
    parser_version = _resolve_parser_version(args.parser_name, args.parser_version)
    logging.info(
        "Starting report tagging parser_name=%s parser_version=%s limit=%s force=%s",
        args.parser_name,
        parser_version,
        args.limit,
        args.force,
    )
    async with SessionLocal() as session:
        summary = await tag_reports(
            session,
            parser_name=args.parser_name,
            parser_version=parser_version,
            date_from=args.date_from,
            date_to=args.date_to,
            code=args.code,
            limit=args.limit,
            force=args.force,
        )
    logging.info(
        "Finished report tagging candidates=%s tagged=%s skipped=%s failed=%s elapsed_seconds=%.3f",
        summary.candidates,
        summary.tagged,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
    )
    payload = {
        "total_pending": summary.total_pending,
        "candidates": summary.candidates,
        "tagged": summary.tagged,
        "skipped": summary.skipped,
        "failed": summary.failed,
        "elapsed_seconds": summary.elapsed_seconds,
        "tag_counts": summary.tag_counts,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Total pending reports: {summary.total_pending}")
        print(f"Candidate reports: {summary.candidates}")
        print(f"Tagged reports: {summary.tagged}")
        print(f"Skipped reports: {summary.skipped}")
        print(f"Failed reports: {summary.failed}")
        print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
        if summary.tag_counts:
            print("Tag counts:")
            for tag_slug, count in summary.tag_counts.items():
                print(f"  {tag_slug}: {count}")
    return 1 if summary.failed else 0


async def _analyze_financials(args: argparse.Namespace) -> int:
    await init_db()
    parser_version = _resolve_parser_version(args.parser_name, args.parser_version)
    analyzer_version = args.analyzer_version or FINANCIAL_FACTS_ANALYZER_VERSION
    logging.info(
        "Starting financial fact analysis parser_name=%s parser_version=%s analyzer_version=%s "
        "limit=%s force=%s retry_failed=%s",
        args.parser_name,
        parser_version,
        analyzer_version,
        args.limit,
        args.force,
        args.retry_failed,
    )
    async with SessionLocal() as session:
        summary = await analyze_financial_facts(
            session,
            parser_name=args.parser_name,
            parser_version=parser_version,
            analyzer_version=analyzer_version,
            retry_failed=args.retry_failed,
            force=args.force,
            date_from=args.date_from,
            date_to=args.date_to,
            code=args.code,
            limit=args.limit,
        )
    logging.info(
        "Finished financial fact analysis candidates=%s analyzed=%s skipped=%s failed=%s elapsed_seconds=%.3f",
        summary.candidates,
        summary.analyzed,
        summary.skipped,
        summary.failed,
        summary.elapsed_seconds,
    )
    payload = {
        "total_pending": summary.total_pending,
        "candidates": summary.candidates,
        "analyzed": summary.analyzed,
        "skipped": summary.skipped,
        "failed": summary.failed,
        "elapsed_seconds": summary.elapsed_seconds,
        "fact_counts": summary.fact_counts,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Total pending analyses: {summary.total_pending}")
        print(f"Candidate parse jobs: {summary.candidates}")
        print(f"Analyzed parse jobs: {summary.analyzed}")
        print(f"Skipped parse jobs: {summary.skipped}")
        print(f"Failed parse jobs: {summary.failed}")
        print(f"Elapsed seconds: {summary.elapsed_seconds:.2f}")
        if summary.fact_counts:
            print("Fact counts:")
            for fact_name, count in summary.fact_counts.items():
                print(f"  {fact_name}: {count}")
    return 1 if summary.failed else 0


async def _list_report_tags(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        tags = await list_report_tag_summaries(session)
    payload = [
        {
            "slug": tag.slug,
            "label_ja": tag.label_ja,
            "label_en": tag.label_en,
            "description": tag.description,
            "priority": tag.priority,
            "active": tag.active,
            "assignment_count": tag.assignment_count,
            "primary_count": tag.primary_count,
        }
        for tag in tags
    ]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for tag in tags:
            count_text = f" assignments={tag.assignment_count} primary={tag.primary_count}" if args.counts else ""
            print(f"{tag.slug}\t{tag.label_ja}\t{tag.label_en}{count_text}")
    return 0


async def _pipeline_run_start(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        await start_pipeline_run(
            session,
            run_id=args.run_id,
            log_path=args.log_path,
            latest_log_path=args.latest_log_path,
            requested_start_date=_parse_optional_date(args.requested_start_date),
            effective_start_date=_parse_optional_date(args.effective_start_date),
            end_date=_parse_optional_date(args.end_date),
            date_count=args.date_count,
            checkpoint_latest_date=_parse_optional_date(args.checkpoint_latest_date),
            checkpoint_start_date=_parse_optional_date(args.checkpoint_start_date),
            checkpoint_applied=_parse_bool(args.checkpoint_applied),
            checkpoint_disabled_reason=args.checkpoint_disabled_reason or None,
            options=_parse_optional_json(args.options_json),
            limits=_parse_optional_json(args.limits_json),
            strategies=_parse_optional_json(args.strategies_json),
            skip_flags=_parse_optional_json(args.skip_flags_json),
        )
    return 0


async def _pipeline_run_finish(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        await finish_pipeline_run(
            session,
            run_id=args.run_id,
            status=args.status,
            elapsed_seconds=args.elapsed_seconds,
            failed_step=args.failed_step,
            exit_code=args.exit_code,
            last_error=args.last_error,
        )
    return 0


async def _pipeline_step_start(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        await start_pipeline_step(
            session,
            run_id=args.run_id,
            step_name=args.step_name,
            step_order=args.step_order,
        )
    return 0


async def _pipeline_step_finish(args: argparse.Namespace) -> int:
    await init_db()
    error_context = args.error_context
    if not error_context and args.status == "failed":
        error_context = error_context_from_file(args.metrics_file)
    async with SessionLocal() as session:
        await finish_pipeline_step(
            session,
            run_id=args.run_id,
            step_name=args.step_name,
            status=args.status,
            elapsed_seconds=args.elapsed_seconds,
            exit_code=args.exit_code,
            command=args.command,
            metrics=parse_step_metrics_file(args.metrics_file),
            error_context=error_context,
        )
    return 0


async def _pipeline_step_skip(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        await skip_pipeline_step(
            session,
            run_id=args.run_id,
            step_name=args.step_name,
            step_order=args.step_order,
            reason=args.reason,
        )
    return 0


def _run_scrape(args: argparse.Namespace) -> int:
    logging.info("Starting scrape date=%s persist=%s", args.date, args.persist)
    result = scrape_tdnet_by_date(args.date)
    _print_scrape_result(result, args.output_format, args.json)

    if args.persist:
        persisted_count = asyncio.run(_persist_result(result))
        print(f"\nPersisted disclosures: {persisted_count}")
        logging.info("Persisted disclosures date=%s count=%s", args.date, persisted_count)

    if not result.pdf_urls and not result.disclosures:
        logging.info("No data found for the specified date.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape, persist, and query TDnet disclosure data."
    )
    subparsers = parser.add_subparsers(dest="command")

    scrape_parser = subparsers.add_parser("scrape", help="Scrape TDnet for one date.")
    scrape_parser.add_argument("--date", required=True, type=_parse_date)
    scrape_parser.add_argument(
        "--output-format",
        choices=["urls", "structured", "both"],
        default="urls",
    )
    scrape_parser.add_argument("--json", action="store_true")
    scrape_parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist scraped disclosures to PostgreSQL.",
    )
    scrape_parser.set_defaults(handler=_run_scrape)

    list_parser = subparsers.add_parser("list", help="List persisted disclosures.")
    list_parser.add_argument("--date", type=_parse_date)
    list_parser.add_argument("--from", dest="date_from", type=_parse_date)
    list_parser.add_argument("--to", dest="date_to", type=_parse_date)
    list_parser.add_argument("--code")
    list_parser.add_argument("--limit", type=int, default=100)
    list_parser.add_argument("--offset", type=int, default=0)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=lambda args: asyncio.run(_list_persisted(args)))

    download_parser = subparsers.add_parser(
        "download",
        help="Download pending PDF/XBRL files for persisted disclosures.",
    )
    download_parser.add_argument("--limit", type=int, default=100)
    download_parser.add_argument("--concurrency", type=int, default=8)
    download_parser.add_argument(
        "--root",
        help="Override download root. Defaults to TDNET_DOWNLOAD_ROOT.",
    )
    download_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed.",
    )
    download_parser.set_defaults(handler=lambda args: asyncio.run(_download_persisted(args)))

    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse completed PDF downloads into markdown artifacts.",
    )
    parse_parser.add_argument("--limit", type=int, default=100)
    parse_parser.add_argument(
        "--workers",
        type=int,
        default=default_parse_workers(),
        help="Number of parser worker processes. Defaults to detected CPU cores.",
    )
    parse_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed for this parser version.",
    )
    parse_parser.set_defaults(handler=lambda args: asyncio.run(_parse_downloaded(args)))

    ocr_parser = subparsers.add_parser(
        "ocr",
        help="Run Apple Vision OCR for completed PDF parses with sparse extracted text.",
    )
    ocr_parser.add_argument("--limit", type=int, default=100)
    ocr_parser.add_argument("--file-id", type=int)
    ocr_parser.add_argument(
        "--strategy",
        choices=["low-text", "forecast-correction", "all"],
        default="low-text",
    )
    ocr_parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent OCR jobs. Apple Vision runs outside Python per PDF.",
    )
    ocr_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed for this OCR parser version.",
    )
    ocr_parser.add_argument(
        "--source-parser-version",
        help="PyMuPDF parser version to use as the low-text source. Defaults to current version.",
    )
    ocr_parser.add_argument(
        "--parser-version",
        help="Override the Apple Vision OCR parser version identity.",
    )
    ocr_parser.set_defaults(handler=lambda args: asyncio.run(_ocr_downloaded(args)))

    ixbrl_parser = subparsers.add_parser(
        "parse-ixbrl",
        help="Extract readable text from downloaded TDnet iXBRL ZIP sidecars.",
    )
    ixbrl_parser.add_argument("--limit", type=int, default=100)
    ixbrl_parser.add_argument("--file-id", type=int)
    ixbrl_parser.add_argument(
        "--strategy",
        choices=["garbled", "forecast-correction", "all"],
        default="garbled",
        help="Which PDF/XBRL pairs to parse. Defaults to PyMuPDF parses that look garbled.",
    )
    ixbrl_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry files previously marked failed for this iXBRL parser version.",
    )
    ixbrl_parser.add_argument(
        "--source-parser-version",
        help="PyMuPDF parser version to inspect for garbled text. Defaults to current version.",
    )
    ixbrl_parser.add_argument(
        "--parser-version",
        help="Override the iXBRL text parser version identity.",
    )
    ixbrl_parser.set_defaults(handler=lambda args: asyncio.run(_parse_ixbrl_text(args)))

    parse_text_parser = subparsers.add_parser(
        "persist-parse-text",
        help="Backfill searchable parse text rows from existing markdown artifacts.",
    )
    parse_text_parser.add_argument("--limit", type=int, default=1000)
    parse_text_parser.add_argument(
        "--parser-name",
        default=PARSER_NAME,
        help="Parser identity to backfill, for example pymupdf4llm or apple-vision-ocr.",
    )
    parse_text_parser.add_argument(
        "--parser-version",
        help="Parser version to backfill. Defaults to the current version for known parsers.",
    )
    parse_text_parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Backfill all versions for the selected parser name.",
    )
    parse_text_parser.set_defaults(handler=lambda args: asyncio.run(_persist_parse_texts(args)))

    tag_parser = subparsers.add_parser(
        "tag-reports",
        help="Classify persisted TDnet reports with deterministic content tags.",
    )
    tag_parser.add_argument("--limit", type=int, default=1000)
    tag_parser.add_argument("--from", dest="date_from", type=_parse_date)
    tag_parser.add_argument("--to", dest="date_to", type=_parse_date)
    tag_parser.add_argument("--code")
    tag_parser.add_argument(
        "--parser-name",
        default=PARSER_NAME,
        help="Parser identity to use for content cues. Defaults to the current PDF parser.",
    )
    tag_parser.add_argument(
        "--parser-version",
        help="Parser version to use for content cues. Defaults to the current version for known parsers.",
    )
    tag_parser.add_argument(
        "--force",
        action="store_true",
        help="Recalculate tags for reports already tagged by the current tagger.",
    )
    tag_parser.add_argument("--json", action="store_true")
    tag_parser.set_defaults(handler=lambda args: asyncio.run(_tag_reports(args)))

    financials_parser = subparsers.add_parser(
        "analyze-financials",
        help="Extract deterministic financial facts into document_analysis_results.",
    )
    financials_parser.add_argument("--limit", type=int, default=1000)
    financials_parser.add_argument("--from", dest="date_from", type=_parse_date)
    financials_parser.add_argument("--to", dest="date_to", type=_parse_date)
    financials_parser.add_argument("--code")
    financials_parser.add_argument(
        "--parser-name",
        default=PARSER_NAME,
        help="Parser identity to analyze. Defaults to the current PDF parser.",
    )
    financials_parser.add_argument(
        "--parser-version",
        help="Parser version to analyze. Defaults to the current version for known parsers.",
    )
    financials_parser.add_argument(
        "--analyzer-version",
        help="Override the financial facts analyzer version identity.",
    )
    financials_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry analyses previously marked failed for this analyzer version.",
    )
    financials_parser.add_argument(
        "--force",
        action="store_true",
        help="Recalculate analyses already completed by this analyzer version.",
    )
    financials_parser.add_argument("--json", action="store_true")
    financials_parser.set_defaults(handler=lambda args: asyncio.run(_analyze_financials(args)))

    list_tags_parser = subparsers.add_parser(
        "list-tags",
        help="List TDnet report tag definitions and optional assignment counts.",
    )
    list_tags_parser.add_argument("--counts", action="store_true")
    list_tags_parser.add_argument("--json", action="store_true")
    list_tags_parser.set_defaults(handler=lambda args: asyncio.run(_list_report_tags(args)))

    pipeline_parser = subparsers.add_parser(
        "pipeline-run",
        help=argparse.SUPPRESS,
    )
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="pipeline_action", required=True)

    pipeline_start = pipeline_subparsers.add_parser("start")
    pipeline_start.add_argument("--run-id", required=True)
    pipeline_start.add_argument("--log-path", required=True)
    pipeline_start.add_argument("--latest-log-path")
    pipeline_start.add_argument("--requested-start-date")
    pipeline_start.add_argument("--effective-start-date")
    pipeline_start.add_argument("--end-date")
    pipeline_start.add_argument("--date-count", type=int, default=0)
    pipeline_start.add_argument("--checkpoint-latest-date")
    pipeline_start.add_argument("--checkpoint-start-date")
    pipeline_start.add_argument("--checkpoint-applied", default="false")
    pipeline_start.add_argument("--checkpoint-disabled-reason")
    pipeline_start.add_argument("--options-json")
    pipeline_start.add_argument("--limits-json")
    pipeline_start.add_argument("--strategies-json")
    pipeline_start.add_argument("--skip-flags-json")
    pipeline_start.set_defaults(handler=lambda args: asyncio.run(_pipeline_run_start(args)))

    pipeline_finish = pipeline_subparsers.add_parser("finish")
    pipeline_finish.add_argument("--run-id", required=True)
    pipeline_finish.add_argument("--status", choices=["completed", "failed"], required=True)
    pipeline_finish.add_argument("--elapsed-seconds", type=float)
    pipeline_finish.add_argument("--failed-step")
    pipeline_finish.add_argument("--exit-code", type=int)
    pipeline_finish.add_argument("--last-error")
    pipeline_finish.set_defaults(handler=lambda args: asyncio.run(_pipeline_run_finish(args)))

    pipeline_step_start = pipeline_subparsers.add_parser("step-start")
    pipeline_step_start.add_argument("--run-id", required=True)
    pipeline_step_start.add_argument("--step-name", required=True)
    pipeline_step_start.add_argument("--step-order", type=int, required=True)
    pipeline_step_start.set_defaults(handler=lambda args: asyncio.run(_pipeline_step_start(args)))

    pipeline_step_finish = pipeline_subparsers.add_parser("step-finish")
    pipeline_step_finish.add_argument("--run-id", required=True)
    pipeline_step_finish.add_argument("--step-name", required=True)
    pipeline_step_finish.add_argument("--status", choices=["completed", "failed"], required=True)
    pipeline_step_finish.add_argument("--elapsed-seconds", type=float)
    pipeline_step_finish.add_argument("--exit-code", type=int)
    pipeline_step_finish.add_argument("--command")
    pipeline_step_finish.add_argument("--metrics-file")
    pipeline_step_finish.add_argument("--error-context")
    pipeline_step_finish.set_defaults(handler=lambda args: asyncio.run(_pipeline_step_finish(args)))

    pipeline_step_skip = pipeline_subparsers.add_parser("step-skip")
    pipeline_step_skip.add_argument("--run-id", required=True)
    pipeline_step_skip.add_argument("--step-name", required=True)
    pipeline_step_skip.add_argument("--step-order", type=int, required=True)
    pipeline_step_skip.add_argument("--reason", required=True)
    pipeline_step_skip.set_defaults(handler=lambda args: asyncio.run(_pipeline_step_skip(args)))

    review_parser = subparsers.add_parser(
        "review-parse",
        help="Create a visual HTML report for completed PDF parses.",
    )
    review_parser.add_argument("--limit", type=int, default=50)
    review_parser.add_argument(
        "--strategy",
        choices=["suspicious", "random", "recent", "forecast-correction"],
        default="suspicious",
    )
    review_parser.add_argument("--pages-per-file", type=int, default=2)
    review_parser.add_argument("--output-root", default="parse-reviews")
    review_parser.add_argument(
        "--parser-name",
        default="pymupdf4llm",
        help="Parser identity to review, for example pymupdf4llm or apple-vision-ocr.",
    )
    review_parser.add_argument(
        "--parser-version",
        help="Parser version to review. Defaults to the current version for known parsers.",
    )
    review_parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated report in the default browser.",
    )
    review_parser.set_defaults(handler=lambda args: asyncio.run(_review_parses(args)))

    # Backward-compatible legacy flags: tdnet --date ... --output-format ...
    parser.add_argument("--date", type=_parse_date)
    parser.add_argument(
        "--output-format",
        choices=["urls", "structured", "both"],
        default="urls",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--persist", action="store_true")

    return parser


def run(argv: list[str] | None = None) -> int:
    log_path = configure_logging()
    logging.info("Logging to %s", log_path)
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        if args.date is None:
            parser.error("Either use a subcommand or provide --date.")
        args.handler = _run_scrape

    try:
        return args.handler(args)
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}")
        return 1


def main() -> None:
    sys.exit(run())
