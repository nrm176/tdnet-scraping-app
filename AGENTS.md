# Agent Instructions

## Orientation

This repository is the canonical TDnet application. Work here, not in sibling legacy projects. The app scrapes Japanese TDnet disclosures, stores metadata in async PostgreSQL, downloads PDF/XBRL artifacts, parses/OCRs PDFs, persists searchable text, and serves read-only APIs plus a review UI.

## Guardrails

- Preserve user data under `/Volumes/yakushimachi/Downloads`; do not delete downloaded artifacts unless explicitly asked.
- Do not reset or truncate Postgres tables unless the user explicitly asks and you have stated the impact.
- Treat `docker-compose.yml` Postgres on host port `55432` as the local database.
- Keep the FastAPI disclosure API read-only unless the user asks to change that contract.
- Keep long-running jobs idempotent: skip completed rows, retry failed rows only when an explicit retry flag is used.
- For downloads, a non-empty expected local PDF/XBRL file is authoritative. Reconcile the `disclosure_files` row as completed and do not hit TDnet again.
- Do not increment `download_attempts` when reconciling an already-present local file; attempts count actual HTTP download attempts.
- Do not commit or rely on runtime outputs: `logs/`, `.venv/`, `dist/`, caches, downloaded files, parse reviews, and `node_modules/`.

## Pull Request Handoff

Before telling the user a PR is ready for review:

- Fetch the latest `origin/main`.
- Rebase or merge the feature branch onto current `origin/main`, unless the PR is intentionally stacked.
- Resolve conflicts locally and preserve already-merged related work.
- Rerun relevant validation.
- Push the updated branch.
- Confirm GitHub reports the PR as mergeable, for example with `gh pr view <number> --json mergeable`.
- If the PR intentionally depends on another unmerged PR, state that dependency clearly instead of calling it ready.

## Common Setup

```bash
docker compose up -d postgres
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Default connection and artifact root:

```text
DATABASE_URL=postgresql+asyncpg://tdnet:tdnet@localhost:55432/tdnet
TDNET_DOWNLOAD_ROOT=/Volumes/yakushimachi/Downloads
```

## CLI Workflows

**Parked (2026-10-25):** the legacy tdnet-postgres database this section's
commands write to is retired. `scripts/tdnet_all_in_one.sh` (with or without
`--legacy-local`) and every `tdnet` subcommand below that writes the legacy
DB now refuse to run with a clear message instead of doing anything --
see `tdnet/database.py`. TDnet disclosure history now lives in the research
database's `raw_tdnet_history`; document download/parse will move there
under Root issue #50. The code is kept, not deleted (`TDNET_LEGACY_LOCAL_ENABLED=1`
re-enables the Python-side guard deliberately; the shell script needs its
early-exit block removed by hand). Do not try to run these commands expecting
them to persist data -- the examples below are historical/reference only.

```bash
scripts/tdnet_all_in_one.sh
```

The all-in-one script defaults to the last 30 Asia/Tokyo days, starts Docker Postgres, ensures `.venv`, scrapes/persists, downloads, parses, and backfills parse text. It writes timestamped logs under `logs/tdnet-all-in-one-<run_id>.log`. OCR and parse review are opt-in with `--with-ocr` and `--with-review`.

Scrape and persist:

```bash
tdnet scrape --date 2026-05-15 --persist
```

Download source artifacts:

```bash
tdnet download --limit 20000 --concurrency 8
tdnet download --retry-failed --concurrency 4
```

Parse PDFs:

```bash
tdnet parse --limit 100 --workers 16
tdnet parse --retry-failed --workers 16
```

OCR sparse parses:

```bash
tdnet ocr --strategy low-text --limit 100 --workers 4
```

Backfill searchable text:

```bash
tdnet persist-parse-text --limit 1000
```

Generate parse review report:

```bash
tdnet review-parse --strategy suspicious --limit 50
```

## App Workflows

Read-only TDnet API:

```bash
uvicorn tdnet.api:app --reload
```

Review/search app (**parked 2026-10-25** -- `app/backend` reads the retired
legacy DB and now refuses to start; see `app/backend/main.py` and
`tdnet/database.py`):

```bash
./app/dev.sh
```

Manual frontend/backend:

```bash
uvicorn app.backend.main:app --host 127.0.0.1 --port 8000
cd app/frontend
npm install
npm run dev
```

## Code Map

- `tdnet/cli.py`: CLI command surface.
- `tdnet/services.py`: TDnet scrape orchestration.
- `tdnet/parsing.py`: TDnet HTML table parsing; file URLs must resolve under `/inbs/`.
- `tdnet/models.py`: Pydantic disclosure/result models.
- `tdnet/orm.py`: SQLAlchemy ORM tables.
- `tdnet/repository.py`: async persistence/query helpers.
- `tdnet/artifacts.py`: async PDF/XBRL downloader.
- `tdnet/parsers.py`: PDF parsing with PyMuPDF4LLM.
- `tdnet/ocr.py`: Apple Vision OCR workflow.
- `tdnet/parse_texts.py`: parse text backfill into Postgres; strips NUL characters before inserting text/JSONB.
- `tdnet/api.py`: read-only metadata/artifact API.
- `app/backend/`: review/search API.
- `app/frontend/`: React/Vite review workbench.

## Artifact Layout

Downloaded files are not stored in this repo. The downloader writes one folder per disclosure, using the PDF stem as the folder and preserving original TDnet file stems:

```text
/Volumes/yakushimachi/Downloads/tdnet/140120260515538453/
  140120260515538453.pdf
  081220260515538453.zip
  parsed/
```

Forecast-correction files route to:

```text
/Volumes/yakushimachi/Downloads/tdnet-forecast-correction
```

Current forecast-correction keywords are `業績予想` and `予想値`.

Before scheduling HTTP work, `tdnet download` checks the expected local path. A non-empty file updates `download_status=completed`, file size, SHA-256, and `downloaded_at`, clears stale errors, and is counted as skipped for the run. Empty files are treated as incomplete and scheduled for download.

## Database Notes

`init_db()` creates missing tables only. It does not alter existing tables. If changing `tdnet/orm.py`, add tests and use explicit additive SQL or a migration plan for existing Docker volumes.

Key tables:

- `tdnet_disclosures`
- `disclosure_files`
- `document_parse_jobs`
- `document_parse_texts`
- `document_analysis_results`

`document_analysis_results` is for downstream text/business analysis. Keep it separate from download and parse history; joins through `file_id` and `parse_job_id` provide the lineage.

## Logging

CLI jobs write rotating logs to:

```text
logs/tdnet.log
```

Download/parse/OCR/parse-text jobs should log job-level statistics and enough per-file failure context to support reruns. Download logs include per-file seconds plus final throughput; parse and backfill logs include progress, ETA, average, and median timings.

## Validation

Run relevant checks before final response:

```bash
python -m ruff check tdnet tests app
python -m pytest -q
python -m build
```

For frontend edits:

```bash
cd app/frontend
npm run build
```

If Docker/Postgres behavior changes, smoke test against the local Compose service and clearly report any skipped checks.
