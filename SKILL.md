---
name: tdnet-scraping-app
description: Use when working in this TDnet codebase to scrape Japanese TDnet disclosures, persist records in async PostgreSQL, download PDF/XBRL artifacts, parse PDFs/OCR text, inspect processing state, troubleshoot scripts/tdnet_all_in_one.sh and tdnet CLI pipeline jobs, reconcile local artifacts, or run the FastAPI review/search app.
---

# TDnet Scraping App

## Scope

This is the canonical TDnet project. Ignore sibling legacy projects such as old fetchers, Azure Functions, and MongoDB-era code unless the user explicitly asks about them.

The active pipeline is:

1. Scrape TDnet disclosure metadata.
2. Persist disclosure rows in PostgreSQL.
3. Download PDF/XBRL source artifacts.
4. Parse PDFs into markdown/page metadata.
5. Persist searchable parse text.
6. Review/search parsed text through FastAPI and the React workbench.

Use this skill for code changes, operational runs, debugging pipeline state, or answering questions about where TDnet data lives.

## Project Map

- `tdnet/`: installable Python package and CLI implementation.
- `tdnet/services.py`, `tdnet/parsing.py`, `tdnet/models.py`: scraper, HTML parsing, Pydantic models.
- `tdnet/orm.py`, `tdnet/repository.py`, `tdnet/database.py`: async SQLAlchemy persistence.
- `tdnet/artifacts.py`: async PDF/XBRL downloader and artifact layout.
- `tdnet/parsers.py`, `tdnet/ocr.py`, `tdnet/parse_texts.py`: PDF parsing, Apple Vision OCR, and text backfill.
- `tdnet/api.py`: read-only disclosure/artifact API.
- `app/backend/`: review/search FastAPI backend.
- `app/frontend/`: Vite React review UI.
- `docs/architecture.md`: ingestion-to-review flow diagram.
- `docs/data-model.md`: PostgreSQL ER diagram, table guide, parser identities, and artifact lineage.
- `docs/feature-backlog.md`: prioritized feature backlog and linked GitHub issues.
- `tests/`: pytest suite.

## Environment

Use Python `>=3.12`. The local Postgres service is defined in `docker-compose.yml` and binds host port `55432` to avoid conflicts with local Postgres on `5432`.

Default environment values:

```text
DATABASE_URL=postgresql+asyncpg://tdnet:tdnet@localhost:55432/tdnet
TDNET_DOWNLOAD_ROOT=/Volumes/yakushimachi/Downloads
```

Start Postgres:

```bash
docker compose up -d postgres
```

Install for development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Some workflows use `uv run --extra layout ...` when the optional `pymupdf-layout` dependency is wanted.

## Core Commands

**Parked (2026-10-25):** the legacy tdnet-postgres database (`DATABASE_URL`
above) is retired. `scripts/tdnet_all_in_one.sh` (all variants below) and the
`tdnet` subcommands that write to it, plus the `app/backend` review/search
FastAPI backend, now refuse to run with a clear message -- see
`tdnet/database.py`. TDnet disclosure history now lives in the research
database's `raw_tdnet_history`; document download/parse will move there under
Root issue #50. The code is kept, not deleted
(`TDNET_LEGACY_LOCAL_ENABLED=1` re-enables the Python-side guard
deliberately; the shell script needs its early-exit block removed by hand).
Don't expect the commands below to persist data or start the review app.

Run the default end-to-end pipeline:

```bash
scripts/tdnet_all_in_one.sh
```

This defaults to the last 30 Asia/Tokyo days, starts Docker Postgres, prepares `.venv`, scrapes/persists, downloads, parses, and backfills parse text. It writes `logs/tdnet-all-in-one-<run_id>.log`. Add `--with-ocr` or `--with-review` only when those phases are needed.

Useful all-in-one variants:

```bash
scripts/tdnet_all_in_one.sh --days 7 --parse-workers 16
scripts/tdnet_all_in_one.sh --start-date 2026-05-01 --end-date 2026-05-15 --retry-failed
scripts/tdnet_all_in_one.sh --scrape-lookback-days 2
scripts/tdnet_all_in_one.sh --force-scrape
scripts/tdnet_all_in_one.sh --with-ocr --with-review
scripts/tdnet_all_in_one.sh --with-ixbrl --ixbrl-strategy garbled
scripts/tdnet_all_in_one.sh --days 7 --retag
```

Scrape and persist one date:

```bash
tdnet scrape --date 2026-05-15 --persist
```

List persisted records:

```bash
tdnet list --date 2026-05-15 --json
```

Download missing source artifacts:

```bash
tdnet download --limit 20000 --concurrency 8
tdnet download --retry-failed --concurrency 4
```

Parse downloaded PDFs:

```bash
tdnet parse --limit 100 --workers 16
tdnet parse --retry-failed --workers 16
```

Backfill searchable parse text:

```bash
tdnet persist-parse-text --limit 1000
```

Run OCR for sparse text extraction:

```bash
tdnet ocr --strategy low-text --limit 100 --workers 4
```

Extract text from downloaded XBRL/iXBRL sidecars:

```bash
tdnet parse-ixbrl --strategy garbled --limit 100
tdnet parse-ixbrl --strategy forecast-correction --limit 100
```

Extract deterministic financial facts into `document_analysis_results`:

```bash
tdnet analyze-financials --limit 1000
tdnet analyze-financials --retry-failed --limit 100
```

Create parse review reports:

```bash
tdnet review-parse --strategy suspicious --limit 50
```

Use `--dry-run` on the all-in-one script when checking command construction or date/checkpoint behavior without side effects.

## Parser Identities

Current parser names:

- `pymupdf4llm`: main PDF parser used by `tdnet parse`.
- `apple-vision-ocr`: macOS Apple Vision OCR fallback used by `tdnet ocr`.
- `tdnet-ixbrl-text`: XBRL/iXBRL sidecar text fallback used by `tdnet parse-ixbrl`.

Parser versions are part of the parse identity. PyMuPDF versions may differ when optional `pymupdf-layout` is installed. The review app lists completed parser options by parser name plus parser version through `/api/parsers` and parser health through `/api/parser-quality`.

The all-in-one script runs `tdnet parse` by default, OCR only with `--with-ocr`, and iXBRL only with `--with-ixbrl`.

## Pipeline Operations

Before running or debugging pipeline commands, inspect the local state:

```bash
docker compose ps postgres
test -x .venv/bin/python && .venv/bin/python --version || true
test -x .venv/bin/tdnet && .venv/bin/tdnet --help >/dev/null || true
```

During long runs, follow the latest all-in-one log:

```bash
tail -n 120 logs/tdnet-all-in-one-latest.log
rg -n "CHECKPOINT|STEP_START|STEP_FINISH|STEP_SKIP|COMMAND_FAIL|status=failed|status=success" logs/tdnet-all-in-one-latest.log
```

If a run fails, identify the failing step, command, exit code, and nearest error context. Prefer fixing the failing step and rerunning the narrowest safe command before rerunning the full pipeline.

Triage checklist:

- Scrape: check `CHECKPOINT` lines first; confirm requested dates, effective start/end, `--force-scrape`, and `--scrape-lookback-days`.
- Download: verify expected paths under the TDnet download buckets; non-empty local files should reconcile to completed without another HTTP request.
- Parse: verify the PDF row has `download_status=completed`; completed parse jobs for the current parser identity should be skipped.
- OCR: run only when needed for sparse text parses, using explicit limits and worker counts.
- iXBRL: run `tdnet parse-ixbrl` directly, or `scripts/tdnet_all_in_one.sh --with-ixbrl`, for completed PDF/XBRL pairs when PyMuPDF text is garbled or forecast-correction sidecar text is preferred.
- Backfill: use `tdnet persist-parse-text`; it should strip NUL characters before inserting text or JSONB.
- Schema: remember `init_db()` creates missing tables only and does not alter existing Docker-volume tables.

## APIs And Apps

Read-only TDnet API:

```bash
uvicorn tdnet.api:app --reload
```

Review backend:

```bash
uvicorn app.backend.main:app --host 127.0.0.1 --port 8000
```

Review API routes include `/api/health`, `/api/parsers`, `/api/parser-quality`, `/api/calendar`, `/api/search`, `/api/review-queue`, `/api/companies/{code}/timeline`, `/api/parse-jobs/{id}`, and `/api/parse-jobs/{id}/page-image`.

Review frontend:

```bash
cd app/frontend
npm install
npm run dev
```

Convenience launcher:

```bash
./app/dev.sh
```

## Artifact Layout

Downloaded files live outside the repo under `TDNET_DOWNLOAD_ROOT`.

Buckets:

```text
/Volumes/yakushimachi/Downloads/tdnet
/Volumes/yakushimachi/Downloads/tdnet-forecast-correction
```

One disclosure folder uses the PDF stem, with original TDnet file stems preserved:

```text
/Volumes/yakushimachi/Downloads/tdnet/140120260515538453/
  140120260515538453.pdf
  081220260515538453.zip
  parsed/
```

Forecast-correction bucket detection currently uses title keywords `業績予想` and `予想値`.

Download idempotency rule: if the expected local artifact path exists and has nonzero bytes, do not hit TDnet. Reconcile `disclosure_files` to `completed`, set size/hash/downloaded time, clear stale errors, and leave `download_attempts` unchanged. Empty files are incomplete and may be downloaded again.

## Persistence Model

Important tables:

- `tdnet_disclosures`: scraped disclosure metadata.
- `disclosure_files`: source artifact state, source URLs, storage paths, hash, size, download status.
- `document_parse_jobs`: parser status and parse artifact paths.
- `document_parse_texts`: searchable text cache.
- `document_analysis_results`: downstream analysis outputs, including deterministic financial facts from `tdnet analyze-financials`.

`init_db()` creates missing tables but does not alter existing tables. For schema changes against an existing local Postgres volume, add explicit migrations or apply careful additive SQL.

Keep analysis state separate from download/parse state. Use `document_analysis_results` joined through `file_id` and optionally `parse_job_id` to track later extraction or classification work.

## Validation

Run before handing back code changes:

```bash
python -m ruff check tdnet tests app
python -m pytest -q
```

For PR handoff, follow `AGENTS.md` Pull Request Handoff and verify GitHub mergeability.

For frontend changes:

```bash
cd app/frontend
npm run build
```

For package changes:

```bash
python -m build
```

## Operational Notes

- Logs are written to `logs/tdnet.log`; `logs/` is ignored by Git.
- `logs/tdnet.log` rotates at roughly 10 MB with 10 backups; all-in-one runs also write timestamped process logs.
- Download, parse, OCR, and parse-text jobs log timing/statistics for throughput analysis.
- Do not commit downloaded PDFs, XBRL zips, parse artifacts, logs, virtualenvs, caches, or `node_modules`.
- Prefer idempotent reruns. Completed records should be skipped; failed records should be retried only with explicit retry flags.
- Parse text persistence strips NUL characters from markdown and page JSON before writing to Postgres.
