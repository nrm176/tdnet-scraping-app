# TDnet Scraping Application

Canonical TDnet service for scraping Japanese disclosure metadata, persisting it
to PostgreSQL, and serving it through a read-only FastAPI API.

![CI](https://github.com/nrm176/tdnet-scrapping-app/actions/workflows/ci.yml/badge.svg?branch=main)

## Quick install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Local Postgres

The bundled Compose file publishes Postgres on host port `55432` by default so
it does not collide with a local Postgres on `5432`.

```bash
cp .env.example .env
docker compose up -d postgres
```

The default application database URL is:

```text
postgresql+asyncpg://tdnet:tdnet@localhost:55432/tdnet
```

## Data model

See [`docs/data-model.md`](docs/data-model.md) for the PostgreSQL entity
relationship diagram, table guide, parser identities, and disk artifact
lineage.

See [`docs/feature-backlog.md`](docs/feature-backlog.md) for the prioritized
feature backlog and linked GitHub issues.

## API usage

`tdnet/api.py` reads the research DB's migrated TDnet history views
(`analysis.tdnet_disclosure_history_v1` / `tdnet_parse_text_history_v1` /
`tdnet_report_tag_history_v1`, SQL044) as `research_reader`, not the legacy
local Postgres `DATABASE_URL` connects to -- the legacy DB is no longer read
anywhere in this app (Root spec 2026-10-24, research DB migration slice 7,
§3.4 phase B). Set `RESEARCH_DATABASE_URL`, or leave it unset to build the
reader URL in memory from Root's session file; see `.env.example`.

Start the FastAPI app:

```bash
uvicorn tdnet.api:app --reload
```

Useful endpoints:

```text
GET  /health
GET  /disclosures?date=2026-05-15
GET  /disclosures?from=2026-05-01&to=2026-05-15
GET  /disclosures/{id}
GET  /disclosures/{id}/parser-status
GET  /disclosures/{id}/lineage
GET  /disclosure-files?download_status=completed
GET  /companies/{code}/disclosures
GET  /companies/{code}/timeline
GET  /parser-quality
GET  /tags
```

The API has no auth in v1 and is read-only. Use the CLI to ingest data into
PostgreSQL, download source files, and parse completed PDF downloads. The
lineage and summary endpoints connect disclosures to downloaded files, parser
jobs, persisted parse text, deterministic tags, and downstream analysis results.

Since the SQL044 views don't carry per-file identity, per-parse-job status,
or analysis results (only a pdf's hash/path, two existence booleans for the
pdf/xbrl files, and the parsed text itself), several endpoints degrade
rather than reading legacy: `/disclosure-files` always returns an empty
list; `/disclosures/{id}/lineage`, `/disclosures/{id}/parser-status`, and
`/companies/{code}/timeline` always return `files: []`; `/parser-quality`
only ever shows the `completed` status bucket (failed/running/pending parse
jobs were never migrated into a reader-exposed view); and `/tags` defaults
`active` to `true` and only lists tags with at least one assignment. See
`tdnet/history_queries.py`'s module docstring for the complete list.

## Review web app

The parsed-text review UI lives under `app/`:

```text
app/
  backend/   # FastAPI API for search, parser metadata, detail views, page images
  frontend/  # Vite React review workbench
```

Start the backend and frontend in separate terminals:

```bash
uv run --extra layout uvicorn app.backend.main:app --host 127.0.0.1 --port 8000
cd app/frontend
npm install
npm run dev
```

Or run both with the helper script:

```bash
./app/dev.sh
```

If `8000` or `5173` is already occupied, the script automatically chooses the
next open port and points the Vite `/api` proxy at the selected backend.

Open:

```text
http://127.0.0.1:5173/
```

The backend exposes `/api/search`, `/api/parsers`, `/api/parser-quality`,
`/api/companies/{code}/timeline`, `/api/parse-jobs/{id}`, and
`/api/parse-jobs/{id}/page-image`. It also accepts
`PUT /api/parse-jobs/{id}/review` from the review workbench to persist manual
parse decisions. On Postgres it prepares `pg_trgm` indexes for Japanese-friendly
substring search over `document_parse_texts.content_text`.

## CLI usage

The normal daily entry captures one date into Research PostgreSQL 18. It only
retrieves and stores validated TDnet metadata; document downloads and parsing
remain separate stages:

```bash
scripts/tdnet_daily_metadata.sh --date 2026-09-10
```

The following direct commands are legacy/component operations for source-local
maintenance and are not the normal daily entry:

```bash
tdnet scrape --date 2026-05-15 --output-format structured
tdnet scrape --date 2026-05-15 --persist
tdnet list --date 2026-05-15 --json
tdnet download --limit 100
tdnet parse --limit 100 --workers 16
```

The broad local pipeline is retained as an explicitly opt-in legacy workflow.
It starts source-local Postgres and may download, parse, OCR, and tag artifacts:

```bash
scripts/tdnet_all_in_one.sh --legacy-local
```

By default, the script reads the latest `tdnet_disclosures.disclosure_date`
from Postgres and uses it as the scrape checkpoint. It starts at the day after
that latest stored date, while still refreshing today when today's records are
already present. Use `--scrape-lookback-days N` to include overlap days, or
`--force-scrape` to ignore the checkpoint and scrape the full requested range.

Useful variants:

```bash
scripts/tdnet_all_in_one.sh --legacy-local --days 7 --parse-workers 16
scripts/tdnet_all_in_one.sh --legacy-local --start-date 2026-05-01 --end-date 2026-05-15 --retry-failed
scripts/tdnet_all_in_one.sh --legacy-local --days 30 --force-scrape
scripts/tdnet_all_in_one.sh --legacy-local --scrape-lookback-days 2
scripts/tdnet_all_in_one.sh --legacy-local --with-ocr --with-review
scripts/tdnet_all_in_one.sh --legacy-local --with-ixbrl --ixbrl-strategy garbled
scripts/tdnet_all_in_one.sh --legacy-local --days 7 --retag
scripts/tdnet_all_in_one_with_ocr.sh --legacy-local --days 7
```

The all-in-one script runs the main PDF parser by default and runs Apple Vision
OCR only when `--with-ocr` is passed. It runs the iXBRL text fallback when
`--with-ixbrl` is passed; use `--ixbrl-limit` and `--ixbrl-strategy` to control
that phase. The script avoids Bash 4-only builtins so it can run under macOS's
default `/bin/bash` 3.2 as well as newer Homebrew Bash.

Each execution writes a dedicated log file under `logs/` and updates a stable
pointer to the newest run:

```text
logs/tdnet-all-in-one-<timestamp>-<pid>.log
logs/tdnet-all-in-one-latest.log
```

The pipeline log records the selected options, each step start/finish, each
command, elapsed time, command output, and the failing step/exit code when a
run stops early. It also records scrape checkpoint decisions, for example:

```text
CHECKPOINT source=postgres latest_disclosure_date=2026-05-18 lookback_days=0 checkpoint_start=2026-05-19 requested_start=2026-04-20 effective_start=2026-05-19 end=2026-05-19 applied=true
```

The legacy form remains supported:

```bash
tdnet --date 2026-05-15 --output-format urls
```

Tip: If the `tdnet` command isn't found, ensure your virtual environment is activated, or run it directly via the venv path (for example on macOS/Linux: `.venv/bin/tdnet`).

Note: Local workflow via `python main.py` remains supported for backward compatibility and tests.

## Downloaded files and processing state

`tdnet download` reads persisted disclosure records from Postgres, downloads
missing PDF/XBRL files, and stores artifact state in `disclosure_files`. The
default root is:

```text
/Volumes/yakushimachi/Downloads
```

Files are bucketed under:

```text
/Volumes/yakushimachi/Downloads/tdnet
/Volumes/yakushimachi/Downloads/tdnet-forecast-correction
```

Each disclosure uses the PDF file stem as its folder, and each source artifact
keeps its original TDnet file stem as the filename:

```text
/Volumes/yakushimachi/Downloads/tdnet/140120260515538453/
  140120260515538453.pdf
  081220260515538453.zip
```

Forecast-correction records are detected from TDnet titles containing `業績予想`
or `予想値`.

`tdnet parse` reads completed PDF rows from `disclosure_files`, skips files that
already have a completed `document_parse_jobs` row for the current
`pymupdf4llm` parser version, and writes markdown/page artifacts next to the PDF:

```text
/Volumes/yakushimachi/Downloads/tdnet/140120260515538453/
  140120260515538453.pdf
  parsed/
    pymupdf4llm.<version>.md
    pymupdf4llm.<version>.pages.json
    pymupdf4llm.<version>.meta.json
```

Use `tdnet parse --retry-failed` to retry files with failed parse jobs.
Analysis state remains separate in `document_analysis_results`, so downstream
business extraction can evolve without overloading download or parse state.
After backfilling parse text, run `tdnet analyze-financials` to extract
deterministic financial facts such as sales, profit, EPS, dividends, and
forecast revision rows into that analysis table with parser lineage:

```bash
tdnet analyze-financials --limit 1000
tdnet analyze-financials --retry-failed --limit 100
```

The review app surfaces these persisted `financial_facts` rows in parse-job
detail, search results, and company timelines. Empty, pending/running, failed,
and completed analysis states remain read-only in the UI.

Manual parser review decisions are stored separately in
`document_parse_reviews`. The review workbench can mark a parse job as
`needs_review`, `accepted`, `bad_parse`, `prefer_ocr`, or `prefer_ixbrl`, save
reviewer notes, and filter search/calendar/timeline views by review state or
computed `unreviewed` rows. These decisions never delete parser artifacts; they
only annotate the preferred or rejected parser output and can influence
best-parser selection in the UI.

Parsing uses worker processes for CPU-bound PDF extraction. By default,
`tdnet parse` uses the detected CPU count; pass `--workers 16` on a 16-core
machine to be explicit, or lower it if `pymupdf-layout` memory use is high.

PyMuPDF4LLM may suggest installing `pymupdf-layout` for improved layout
analysis. It is available as an optional dependency because its license is
PolyForm Noncommercial or Artifex Commercial:

```bash
uv run --extra layout tdnet parse --limit 100 --workers 16
```

When layout is installed, its version is included in the parser identity, so
layout-enabled parses are tracked separately from earlier markdown artifacts.

### Parser identities

The current parser identities are:

| Parser name | Source | Command | Notes |
| --- | --- | --- | --- |
| `pymupdf4llm` | PDF | `tdnet parse` | Main parser for downloaded PDFs. The parser version includes the PyMuPDF4LLM version, optional `pymupdf-layout` version, and TDnet normalizer version. |
| `apple-vision-ocr` | PDF rendered to images | `tdnet ocr` | macOS Apple Vision fallback for sparse embedded text. Writes separate parse jobs and artifacts instead of overwriting PyMuPDF output. |
| `tdnet-ixbrl-text` | downloaded XBRL/iXBRL ZIP sidecar | `tdnet parse-ixbrl` | Text fallback for PDF/XBRL pairs, especially when PyMuPDF text is garbled. Writes one-page markdown/page JSON artifacts under the PDF disclosure folder. |

The review app exposes completed parser options through `/api/parsers`, groups
them by parser name plus parser version, and exposes parser health through
`/api/parser-quality`.

Completed parse jobs also persist searchable text into Postgres in
`document_parse_texts`. The markdown remains on disk as the durable artifact,
while Postgres stores:

- `content_text` as `TEXT`
- `pages_json` as `JSONB` on Postgres
- `page_count`, `char_count`, and `content_sha256`

Use the backfill command to populate this cache for artifacts created before
the table existed:

```bash
tdnet persist-parse-text --parser-name pymupdf4llm --limit 1000
tdnet persist-parse-text --parser-name apple-vision-ocr --limit 1000
tdnet persist-parse-text --parser-name pymupdf4llm --all-versions --limit 1000
```

Some TDnet PDFs are visually readable but contain no embedded text layer. On
macOS, run Apple Vision OCR as a separate parse identity for sparse PyMuPDF
outputs:

```bash
tdnet ocr --strategy low-text --limit 100 --workers 4
tdnet ocr --file-id 15820
```

OCR jobs write their own artifacts and `document_parse_jobs` rows instead of
overwriting PyMuPDF output:

```text
parsed/
  apple-vision-ocr.<version>.md
  apple-vision-ocr.<version>.pages.json
  apple-vision-ocr.<version>.meta.json
  ocr/apple-vision-ocr.<version>/page-001.png
```

Some disclosures include downloaded XBRL/iXBRL ZIP sidecars that contain cleaner
visible text than the PDF extraction. Run the iXBRL fallback as a separate parse
identity directly, or opt into it during all-in-one runs:

```bash
tdnet parse-ixbrl --strategy garbled --limit 100
tdnet parse-ixbrl --strategy forecast-correction --limit 100
tdnet parse-ixbrl --file-id 15820
scripts/tdnet_all_in_one.sh --legacy-local --with-ixbrl --ixbrl-strategy garbled --ixbrl-limit 100
```

iXBRL parse jobs use parser name `tdnet-ixbrl-text` and parser version
`tdnet-1`. They are selected from completed PDF/XBRL disclosure pairs and write
artifacts like:

```text
parsed/
  tdnet-ixbrl-text.tdnet-1.md
  tdnet-ixbrl-text.tdnet-1.pages.json
  tdnet-ixbrl-text.tdnet-1.meta.json
```

CLI jobs write runtime logs to:

```text
logs/tdnet.log
```

The file rotates at roughly 10 MB with 10 backups, and the `logs/` directory is
ignored by Git. Download jobs log per-file timing plus a final statistics line
with elapsed seconds, bytes downloaded, throughput, average file seconds, and
median file seconds.

Parse jobs log structured lifecycle lines such as `parse_start`,
`parse_item_completed`, `parse_progress`, and `parse_finished`. The final line
includes total pending files for the parser version, scheduled files in the
current batch, workers, elapsed seconds, files per second, average/median file
seconds, and estimated remaining time. The CLI prints the same summary after
each run, which makes short batches useful for estimating the full backfill.
OCR jobs emit the same style of statistics with `ocr_start`,
`ocr_item_completed`, `ocr_progress`, and `ocr_finished`.
Parse text backfills emit `parse_text_backfill_start`,
`parse_text_backfill_item_completed`, `parse_text_backfill_progress`, and
`parse_text_backfill_finished`.

## Visual parse review

Use `tdnet review-parse` to generate a static HTML report for developer QA. The
report renders selected PDF pages as images beside the parsed markdown so you
can quickly spot layout or extraction problems:

```bash
tdnet review-parse --strategy suspicious --limit 50 --pages-per-file 2
tdnet review-parse --strategy random --limit 25 --open
tdnet review-parse --strategy forecast-correction --limit 50
tdnet review-parse --parser-name apple-vision-ocr --strategy recent --limit 10
```

Reports are written under `parse-reviews/<timestamp>/index.html`, with PNG page
renders in the report's `assets/` directory. The `suspicious` strategy ranks
documents using lightweight quality hints such as low text volume, sparse
multi-page output, missing page JSON, and large PDFs with little extracted text.
The generated report remains useful for offline QA snapshots; durable decisions
should be saved through the review workbench so they persist in
`document_parse_reviews` and can be filtered later.

`review-parse` reads the research DB's migrated TDnet history views as
`research_reader`, the same as `tdnet/api.py` above -- it does not touch the
legacy local Postgres this CLI's scrape/download/parse commands write to. A
report generated right after a `--legacy-local` run only shows disclosures
already migrated into the research DB, not ones that just landed locally.
It also only has the concatenated parsed text (no per-page breakdown and no
file size), so each document renders as a single synthetic page.

## CI and distribution

This repo includes GitHub Actions to test and build distributable packages you can install locally:

- CI (`.github/workflows/ci.yml`):
    - Runs tests on Python 3.9–3.13 on pushes and PRs to `main`.
    - Builds sdist/wheel and uploads them as run artifacts.

- Release artifacts (`.github/workflows/publish.yml`):
    - On GitHub Release publish: builds and attaches `dist/*.whl` and `dist/*.tar.gz` to the Release.

- Tag-based releases (`.github/workflows/tag-release.yml`):
    - Pushing a tag like `v0.1.1` automatically builds and creates a GitHub Release with packaged artifacts attached.

### Install via Release assets

1) Go to the GitHub repository Releases page and download the wheel (e.g., `tdnet_scraper-<version>-py3-none-any.whl`).
2) Install it:

```bash
pip install /path/to/tdnet_scraper-<version>-py3-none-any.whl
```

### Create a new release (automatic)

1) Push a semantic version tag (replace with your version):

```bash
git tag v0.1.1
git push origin v0.1.1
```

2) The "Tag Release" workflow will create a GitHub Release named after the tag and attach the build artifacts.

### Install via CI artifact

1) Open the latest successful CI run on `main`.
2) Download the `dist` artifact.
3) Install the wheel or sdist:

```bash
pip install dist/tdnet_scraper-<version>-py3-none-any.whl
# or
pip install dist/tdnet_scraper-<version>.tar.gz
```

A modern, robust Python application for scraping Japanese corporate disclosure data from TDnet (Timely Disclosure Network). Built with cutting-edge **Pydantic V2+** patterns and comprehensive testing infrastructure.

## 🏗️ Architecture Overview

### Core Components
- **Modern Pydantic Models**: Type-safe data structures with advanced validation
- **Web Scraping Engine**: BeautifulSoup-based HTML parsing with robust error handling
- **CLI Interface**: Argparse-based command-line interface with multiple output formats
- **Testing Infrastructure**: 89% test coverage with comprehensive mock data
- **Hash ID System**: SHA-256 based unique identifiers for database integration

### Technology Stack
- **Python 3.9+** with modern type hints and Annotated types
- **Pydantic 2.12.3** with V2+ patterns (future V3-ready)
- **BeautifulSoup4** for HTML parsing with lxml backend
- **Requests** with session management and proper headers
- **Pytest** with coverage reporting and comprehensive mocking

## 🚀 Features

### Core Functionality
- **Date-based Scraping**: Extract disclosures for any specific date with pagination support
- **Advanced Data Validation**: Pydantic V2+ models with field validators and model validators
- **XBRL Support**: Automatic detection and handling of XBRL financial data files
- **Unique Hash IDs**: SHA-256 based identifiers ensuring data consistency across scraping runs
- **Multiple Output Formats**: JSON, structured text, or PDF URLs with consistent formatting

### Modern Pydantic Features
- **Annotated Types**: Type-safe field definitions with pattern validation
- **Field Validators**: Custom validation logic for data normalization and integrity
- **Model Validators**: Cross-field validation ensuring data consistency
- **Computed Fields**: Dynamic properties calculated from model data
- **JSON Schema Generation**: API-ready documentation with examples
- **Database Integration Ready**: Metadata for ORM integration

### Quality Assurance
- **89% Test Coverage**: Comprehensive test suite with realistic mock data
- **Type Safety**: Full type hints with mypy compatibility
- **Error Handling**: Robust exception handling with informative error messages
- **Data Integrity**: Multi-level validation preventing corrupted data
- **Performance Optimized**: Efficient parsing and minimal memory footprint

## 📋 Requirements

- Python 3.9+
- Virtual environment (recommended)

## 🛠️ Installation (from source)

1. **Clone or download the project**:
   ```bash
   cd /path/to/project/directory
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment**:
   ```bash
   # macOS/Linux
   source .venv/bin/activate
   
   # Windows
   .venv\Scripts\activate
   ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## 📖 Usage

### Basic Usage

```bash
python main.py --date=2025-10-21
```

### Command Line Options

| Option | Description | Example |
|--------|-------------|---------|
| `--date` | Target date (YYYY-MM-DD) | `--date=2025-10-21` |
| `--output-format` | Output format: `structured`, `json`, `urls` | `--output-format=json` |
| `--json` | Force JSON output (shorthand) | `--json` |

### Output Formats

#### 1. Structured Data (Default)
```bash
python main.py --date=2025-10-21 --output-format=structured
```

**Output**:
```
--- Structured Data ---
Total disclosures found: 103
-----------------------
Company: イオン九州 (26530)
Time: 19:00
Title: 完全子会社の吸収合併（簡易合併）及び債権放棄に関するお知らせ
PDF: https://www.release.tdnet.info/inbs/140120251020576212.pdf
XBRL Available: No
Place: 東
ID: 623174ebb73b3a3f
```

#### 2. JSON Output
```bash
python main.py --date=2025-10-21 --json
```

**Output**:
```json
{
  "scraping_date": "2025-10-21",
  "total_disclosures": 103,
  "unique_disclosure_count": 103,
  "disclosures": [
    {
      "time": "19:00",
      "code": "26530",
      "name": "イオン九州",
      "title": "完全子会社の吸収合併（簡易合併）及び債権放棄に関するお知らせ",
      "pdf_url": "https://www.release.tdnet.info/inbs/140120251020576212.pdf",
      "xbrl_available": false,
      "place": "東",
      "history": "",
      "disclosure_date": "2025-10-21",
      "id": "623174ebb73b3a3f"
    }
  ],
  "pdf_urls": ["..."]
}
```

#### 3. URLs Only
```bash
python main.py --date=2025-10-21 --output-format=urls
```

**Output**:
```
--- PDF URLs ---
Total URLs found: 103
---------------
https://www.release.tdnet.info/inbs/140120251020576212.pdf
https://www.release.tdnet.info/inbs/140120251021576595.pdf
...
```

## 📊 Data Architecture & Modern Pydantic Models

### Pydantic V2+ Type System

The application uses modern Pydantic patterns with Annotated types for enhanced type safety:

```python
# Type Aliases with Validation
CompanyCode = Annotated[str, Field(pattern=r'^[0-9A-Z]+$', description="Company code")]
DisclosureTime = Annotated[str, Field(pattern=r'^\d{1,2}:\d{2}$', description="Time in HH:MM format")]
ExchangeCode = Annotated[str, Field(min_length=1, max_length=10, description="Stock exchange")]
```

### TdnetDisclosure Model

**Modern Pydantic V2+ model with advanced validation:**

| Field | Annotated Type | Validation | Description | Example |
|-------|---------------|------------|-------------|---------|
| `id` | `computed_field` | SHA-256 hash | Unique identifier (16 chars) | `"623174ebb73b3a3f"` |
| `time` | `DisclosureTime` | HH:MM pattern | Disclosure time | `"19:00"` |
| `code` | `CompanyCode` | Alphanumeric pattern | Company code (normalized) | `"26530"` |
| `name` | `Annotated[str, Field(min_length=1)]` | Non-empty, trimmed | Company name | `"イオン九州"` |
| `title` | `Annotated[str, Field(min_length=1)]` | Non-empty, trimmed | Document title | `"完全子会社の吸収合併..."` |
| `pdf_url` | `Annotated[HttpUrl, Field()]` | URL validation | PDF document URL | `"https://www.release..."` |
| `xbrl_available` | `Annotated[bool, Field(default=False)]` | Boolean | XBRL data availability | `true` |
| `xbrl_url` | `Annotated[Optional[HttpUrl], Field(default=None)]` | URL validation | XBRL file URL | `"https://www.release...zip"` |
| `place` | `ExchangeCode` | Length validation | Stock exchange | `"東"` |
| `history` | `Annotated[str, Field(default="")]` | Default empty | Update history | `""` |
| `disclosure_date` | `Annotated[date, Field()]` | Date validation | Disclosure date | `"2025-10-21"` |

### Advanced Validation Features

#### Field Validators
```python
@field_validator('code')
@classmethod
def validate_company_code(cls, v: str) -> str:
    """Validates and normalizes company codes (uppercase, trimmed)."""
    
@field_validator('name', 'title')  
@classmethod
def validate_non_empty_strings(cls, v: str) -> str:
    """Ensures strings are non-empty and properly trimmed."""
```

#### Model Validators
```python
@model_validator(mode='after')
def validate_xbrl_consistency(self):
    """Ensures XBRL URL is provided when XBRL is available, and vice versa."""
```

### TdnetScrapingResult Model

**Container model with comprehensive data validation:**

| Field | Type | Validation | Description |
|-------|------|------------|-------------|
| `scraping_date` | `Annotated[date, Field()]` | Date validation | Target scraping date |
| `total_disclosures` | `Annotated[int, Field(ge=0)]` | Non-negative | Count of disclosures |
| `disclosures` | `List[TdnetDisclosure]` | Model validation | List of disclosure objects |
| `pdf_urls` | `List[HttpUrl]` | URL + uniqueness | All PDF URLs found |

#### Computed Properties
```python
@computed_field
@property
def unique_disclosure_count(self) -> int:
    """Count of unique disclosure IDs."""

@computed_field  
@property
def companies_by_exchange(self) -> Dict[str, int]:
    """Statistics by stock exchange."""
```

#### Data Integrity Validation
- **Count Consistency**: `total_disclosures` must match actual disclosure count
- **URL Uniqueness**: All PDF URLs must be unique
- **Reference Integrity**: All disclosure PDF URLs must be in the main PDF URLs list

### Modern Configuration

```python
model_config = ConfigDict(
    str_strip_whitespace=True,      # Automatic whitespace trimming
    validate_assignment=True,        # Validation on field assignment
    extra='forbid',                 # Prevent unknown fields
    table_name="tdnet_disclosures", # Database integration metadata
    json_schema_extra={             # API documentation examples
        "example": { /* complete example */ }
    }
)
```

## 🔧 Advanced Usage

### Save JSON to File

```bash
python main.py --date=2025-10-21 --json > tdnet_data_20251021.json
```

### Filter by Exchange

```python
from main import scrape_tdnet_by_date
from datetime import date

result = scrape_tdnet_by_date(date(2025, 10, 21))

# Get only Tokyo Stock Exchange disclosures
tokyo_disclosures = result.get_companies_by_exchange("東")
print(f"Tokyo disclosures: {len(tokyo_disclosures)}")
```

### Find Specific Disclosure

```python
# Find by hash ID
disclosure = result.get_disclosure_by_id("623174ebb73b3a3f")
if disclosure:
    print(f"Found: {disclosure.name} - {disclosure.title}")
```

### Check for XBRL Data

```python
xbrl_disclosures = [d for d in result.disclosures if d.xbrl_available]
print(f"Disclosures with XBRL: {len(xbrl_disclosures)}")
```

## 🔍 Hash ID System

Each disclosure gets a unique 16-character hash ID based on:
- Company code
- Disclosure date  
- PDF URL (contains unique document ID)
- Time

**Benefits**:
- ✅ Guaranteed uniqueness
- ✅ Consistent across scraping runs
- ✅ Perfect for database primary keys
- ✅ Enables efficient deduplication

## 📁 Project Architecture & Structure

The project is organized as a proper Python package with a small back-compat shim at the repository root.

```
tdnet-scraping-app/
├── pyproject.toml                 # Packaging (PEP 621), console script entry point
├── README.md                      # Project documentation and usage
├── LICENSE                        # MIT license
├── requirements.txt               # Local/dev dependencies
├── HASH_ID_DOCUMENTATION.md       # Hash ID system notes
├── main.py                        # Backward-compatible CLI shim delegating to tdnet.*
├── tdnet/
│   ├── __init__.py                # Public API re-exports, __version__
│   ├── cli.py                     # CLI: run(argv)->int and main()
│   ├── constants.py               # BASE_URL, HEADERS
│   ├── models.py                  # Pydantic models and type aliases
│   ├── parsing.py                 # HTML parsing utilities
│   └── services.py                # scrape_tdnet_by_date orchestrator
├── tests/
│   ├── test_main.py               # Models, parsing, integration, CLI
│   ├── test_hash_ids.py
│   ├── test_hash_consistency.py
│   ├── mock_tdnet_page.html
│   └── mock_empty_page.html
└── .github/workflows/
        ├── ci.yml                     # Test + build artifacts on push/PR
        ├── publish.yml                # Attach artifacts on Release
        └── tag-release.yml            # Auto create Release from v* tags
```

### Module responsibilities

- `tdnet.constants` — Constants used across scraping (e.g., `BASE_URL`, `HEADERS`).
- `tdnet.models` — Pydantic v2 models and type aliases:
    - `TdnetDisclosure` (validation + computed `id`)
    - `TdnetScrapingResult` (container with helpers and computed stats)
- `tdnet.parsing` — HTML parsing utilities:
    - `extract_pdf_urls_from_page`, `extract_structured_data_from_page`, `has_next_page`
- `tdnet.services` — Orchestration:
    - `scrape_tdnet_by_date(date)`: drives pagination, network calls, and parsing
- `tdnet.cli` — Command-line interface:
    - `run(argv)->int` for programmatic use and `main()` for the console script
- `tdnet.__init__` — Public API re-exports for ergonomic imports and `__version__`.
- `main.py` — Compatibility layer re-exporting symbols under `main.*` used in tests and providing a CLI.

### Data flow (high level)
1) CLI or caller invokes `scrape_tdnet_by_date(date)`
2) Service constructs per-page URLs and fetches HTML
3) `parsing` extracts PDF URLs and structured records per page
4) `models` validate and normalize data; results are aggregated
5) Result returned as `TdnetScrapingResult` (printable, JSON-serializable)

### Entry points
- Console script: `tdnet` (installed via `pyproject.toml`)
- Back-compat: `python main.py --date YYYY-MM-DD ...`

### Public API (import examples)
```python
from tdnet import (
        BASE_URL, HEADERS,
        TdnetDisclosure, TdnetScrapingResult,
        CompanyCode, ExchangeCode, DisclosureTime,
        extract_structured_data_from_page, extract_pdf_urls_from_page, has_next_page,
        scrape_tdnet_by_date,
)
```

### Testing overview
- Unit tests cover models, parsing helpers, and error handling
- Integration tests mock network via `requests.Session`
- CLI tests validate argument parsing and output formatting
## 🧪 Testing Infrastructure (89% Coverage)

### Test Coverage Breakdown
```
Name      Stmts   Miss  Cover   Missing Lines
main.py   279     34    88%     [Detailed coverage report]
Total:    279     34    88%
```

### Testing Categories

#### 1. **Unit Tests** (18 methods)
- **Pydantic Model Validation**: Field validators, model validators, computed fields
- **Utility Functions**: HTML parsing, URL construction, data extraction
- **Hash ID System**: Uniqueness, consistency, collision detection

#### 2. **Integration Tests** (7 methods)
- **HTTP Session Management**: requests.Session context manager mocking
- **End-to-End Scraping**: Complete workflow with realistic mock data
- **Error Handling**: Network failures, malformed HTML, invalid responses

#### 3. **CLI Interface Tests** (4 methods)  
- **Argument Validation**: Date formats, output options, error cases
- **Output Formatting**: JSON serialization, text formatting, error messages
- **System Integration**: sys.argv mocking, exception handling

### Mock Data Quality
- **Realistic HTML Structure**: Based on actual TDnet page analysis
- **Edge Cases**: Empty pages, malformed data, missing elements
- **XBRL Integration**: Complex scenarios with supplementary documents
- **Pagination**: Multi-page scenarios with navigation controls

### Testing Best Practices
```python
# Modern pytest patterns with fixtures
@pytest.fixture
def sample_disclosure():
    """Reusable test data fixture."""

# Comprehensive mocking with unittest.mock
@patch('main.requests.Session')
def test_integration(self, mock_session):
    """Integration test with HTTP mocking."""

# Parameterized tests for edge cases  
@pytest.mark.parametrize("input,expected", test_cases)
def test_validation(self, input, expected):
    """Data-driven validation testing."""
```

## 🗓️ Date Format Requirements

- **Format**: `YYYY-MM-DD`
- **Examples**: 
  - `2025-10-21` ✅
  - `2025-1-21` ❌ (use zero-padding)
  - `21-10-2025` ❌ (wrong order)

## ⚠️ Important Notes

1. **Rate Limiting**: The script includes reasonable delays between requests
2. **Data Availability**: TDnet may not have data for all dates (weekends, holidays)
3. **XBRL Files**: Available only for certain types of disclosures
4. **Time Zone**: All times are in Japan Standard Time (JST)

## 🛠️ Troubleshooting

### Common Issues

**"No data found for date"**
- Check if the date is a business day in Japan
- Verify the date format (YYYY-MM-DD)
- Some dates may genuinely have no disclosures

**"Connection error"**
- Check internet connection
- TDnet server may be temporarily unavailable
- Try again after a few minutes

**"Invalid date format"**
- Ensure date is in YYYY-MM-DD format
- Use zero-padding for single-digit months/days

### Debug Mode

Enable debug logging by modifying the logging level in `main.py`:

```python
logging.basicConfig(level=logging.DEBUG)
```

## 📊 Example Output Summary

For a typical business day, you might see:
- **100-150 disclosures** from major companies
- **20-30% with XBRL data** (financial reports)
- **Multiple exchanges**: 東 (Tokyo), 東名 (Tokyo/Nagoya), etc.
- **Various times**: 8:00, 15:30, 16:00, 19:00 (common disclosure times)

## � Technical Implementation Details

### HTML Parsing Strategy

#### TDnet Page Structure Analysis
```html
<table id="main-list-table">
  <tbody>
    <tr>
      <td class="kjTime">15:30</td>           <!-- Disclosure time -->
      <td class="kjCode">12345</td>           <!-- Company code -->
      <td class="kjCompany">テスト株式会社</td>    <!-- Company name -->
      <td class="kjTitle">                   <!-- Title with links -->
        <a href="*.pdf">Document Title</a>    <!-- Main PDF -->
        <a href="*.zip"><img src="xbrl.gif"></a>  <!-- XBRL data -->
      </td>
      <td class="kjPlace">東</td>             <!-- Stock exchange -->
      <td class="kjHistory">訂正</td>          <!-- Update history -->
    </tr>
  </tbody>
</table>
```

#### Parsing Logic Flow
```python
def extract_structured_data_from_page(soup, disclosure_date):
    """
    1. Find main-list-table by ID
    2. Iterate through <tr> elements  
    3. Extract data from cells with 'kj*' CSS classes
    4. Handle special cases:
       - kjTitle: Extract first PDF link as main disclosure
       - XBRL detection: Look for .zip links with XBRL images
       - Data normalization: Trim whitespace, validate formats
    5. Create TdnetDisclosure objects with validation
    """
```

### URL Construction Patterns

#### TDnet URL Format
```python
# Page 1: I_list_001_YYYYMMDD.html
# Page N: I_list_00N_YYYYMMDD.html (zero-padded to 3 digits)

BASE_URL = "https://www.release.tdnet.info"
page_url = f"{BASE_URL}/inbs/I_list_{page_num:03d}_{date_str}.html"
```

#### PDF URL Handling
```python
# PDF URLs are relative, need BASE_URL joining
pdf_url = urljoin(BASE_URL, href)  # Handles relative/absolute URLs properly
```

### Hash ID Algorithm

#### Implementation Details
```python
def generate_hash_id(self) -> str:
    """
    Creates SHA-256 hash from identifying attributes:
    - Company code (normalized)
    - Disclosure date (ISO format)  
    - PDF URL (contains unique document ID)
    - Time (handles multiple disclosures per day)
    
    Returns first 16 characters for readability while maintaining
    extremely low collision probability (1 in 18 quintillion).
    """
    unique_string = f"{self.code}_{self.disclosure_date}_{str(self.pdf_url)}_{self.time}"
    hash_object = hashlib.sha256(unique_string.encode('utf-8'))
    return hash_object.hexdigest()[:16]
```

### Request Management

#### Session Configuration
```python
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

with requests.Session() as session:
    session.headers.update(HEADERS)  # Persistent headers
    # Automatic cookie management
    # Connection pooling for efficiency
```

#### Error Handling Strategy
```python
try:
    response = session.get(page_url)
    if response.status_code == 404:
        # End of pagination reached
        break
    response.raise_for_status()  # HTTP error handling
    response.encoding = 'utf-8'  # Explicit encoding
except requests.exceptions.RequestException as e:
    # Network error handling with logging
    logging.error(f"Request failed: {e}")
```

## 🚀 Advanced Integration Examples

### Database Integration with Modern Pydantic

#### SQLAlchemy Integration
```python
from sqlalchemy import create_engine, Column, String, Boolean, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class TdnetDisclosureORM(Base):
    """SQLAlchemy model matching Pydantic structure."""
    __tablename__ = 'tdnet_disclosures'  # Matches Pydantic metadata
    
    id = Column(String(16), primary_key=True)
    time = Column(String(5))  # HH:MM format
    code = Column(String(10), index=True)  # Company code index
    name = Column(String(200))
    title = Column(String(500))
    pdf_url = Column(String(200), unique=True)
    xbrl_available = Column(Boolean, default=False)
    xbrl_url = Column(String(200), nullable=True)
    place = Column(String(10), index=True)  # Exchange index
    history = Column(String(100))
    disclosure_date = Column(Date, index=True)  # Date index for queries

# Integration function
def save_to_database(result: TdnetScrapingResult, db_url: str):
    """Save scraping result to database with Pydantic validation."""
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    with Session() as session:
        for disclosure in result.disclosures:
            # Pydantic model_dump() provides validated data
            db_record = TdnetDisclosureORM(**disclosure.model_dump(exclude={'id'}))
            db_record.id = disclosure.id  # Use computed hash ID
            session.merge(db_record)  # Upsert behavior
        session.commit()
```

#### FastAPI Integration
```python
from fastapi import FastAPI, HTTPException
from datetime import date
from typing import List

app = FastAPI(title="TDnet API", version="1.0.0")

@app.get("/disclosures/{target_date}", response_model=TdnetScrapingResult)
async def get_disclosures(target_date: date) -> TdnetScrapingResult:
    """
    Get TDnet disclosures for a specific date.
    
    - **target_date**: Date in YYYY-MM-DD format
    - **returns**: Complete scraping result with validation
    """
    try:
        result = scrape_tdnet_by_date(target_date)
        return result  # Automatic Pydantic serialization
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/disclosures/{target_date}/exchange/{exchange_code}")
async def get_disclosures_by_exchange(
    target_date: date, 
    exchange_code: str
) -> List[TdnetDisclosure]:
    """Filter disclosures by stock exchange."""
    result = scrape_tdnet_by_date(target_date)
    return result.get_companies_by_exchange(exchange_code)
```

### Data Analysis with Pandas

#### Advanced Analytics
```python
import pandas as pd
from datetime import date, timedelta

def analyze_disclosure_trends(start_date: date, end_date: date) -> pd.DataFrame:
    """Comprehensive disclosure analysis across date range."""
    all_data = []
    
    current_date = start_date
    while current_date <= end_date:
        try:
            result = scrape_tdnet_by_date(current_date)
            # Convert Pydantic models to dictionaries
            daily_data = [d.model_dump() for d in result.disclosures]
            all_data.extend(daily_data)
        except Exception as e:
            logging.warning(f"Failed to scrape {current_date}: {e}")
        current_date += timedelta(days=1)
    
    df = pd.DataFrame(all_data)
    
    # Analysis examples
    analytics = {
        'total_disclosures': len(df),
        'unique_companies': df['code'].nunique(),
        'xbrl_percentage': (df['xbrl_available'].sum() / len(df)) * 100,
        'top_exchanges': df['place'].value_counts().head(),
        'peak_hours': df['time'].value_counts().head(),
        'most_active_companies': df.groupby(['code', 'name']).size().head(10)
    }
    
    return df, analytics
```

### Machine Learning Integration

#### Feature Engineering
```python
def prepare_ml_features(disclosures: List[TdnetDisclosure]) -> pd.DataFrame:
    """Extract features for ML analysis."""
    features = []
    
    for disclosure in disclosures:
        feature_row = {
            'company_code': disclosure.code,
            'hour': int(disclosure.time.split(':')[0]),
            'exchange': disclosure.place,
            'has_xbrl': int(disclosure.xbrl_available),
            'title_length': len(disclosure.title),
            'has_correction': int('訂正' in disclosure.history),
            'is_financial_report': int('決算' in disclosure.title),
            'is_merger': int('合併' in disclosure.title),
            'disclosure_date': disclosure.disclosure_date
        }
        features.append(feature_row)
    
    return pd.DataFrame(features)
```

## 🤖 LLM Development Guide

### For AI Models Working with This Codebase

#### Code Analysis Checklist
- ✅ **Pydantic V2+ Patterns**: Modern validation with Annotated types
- ✅ **Type Safety**: Comprehensive type hints with `typing` module
- ✅ **Error Handling**: Structured exception handling with logging
- ✅ **Testing Coverage**: 89% coverage with realistic mock data
- ✅ **Documentation**: Inline docstrings and type annotations

#### Key Implementation Patterns to Understand

**1. Modern Pydantic Model Structure**
```python
class TdnetDisclosure(BaseModel):
    # Annotated types with validation
    code: CompanyCode  # Custom type alias with regex pattern
    time: DisclosureTime  # Time format validation
    
    # Field validators for data normalization
    @field_validator('code')
    @classmethod
    def validate_company_code(cls, v: str) -> str:
        return v.strip().upper()  # Normalize format
    
    # Model validators for cross-field validation  
    @model_validator(mode='after')
    def validate_xbrl_consistency(self):
        # Business logic validation
        if self.xbrl_available and self.xbrl_url is None:
            raise ValueError("XBRL URL required when available")
        return self
    
    # Computed properties
    @computed_field
    @property  
    def id(self) -> str:
        return self._generate_hash()  # Dynamic hash generation
```

**2. HTML Parsing Architecture**
```python
def extract_structured_data_from_page(soup, date):
    # Defensive parsing with fallbacks
    main_table = soup.find('table', id='main-list-table')
    if not main_table:
        return []  # Graceful degradation
    
    # CSS class-based extraction
    for cell in cells:
        if 'kjTitle' in cell.get('class', []):
            # Complex link extraction logic
            # XBRL detection with image analysis
            # Multiple PDF handling
```

**3. Session Management Pattern**  
```python
with requests.Session() as session:
    session.headers.update(HEADERS)  # Persistent configuration
    
    while True:  # Pagination loop
        response = session.get(url)  # Connection reuse
        # Error handling with specific status codes
        # Encoding management for Japanese text
```

#### Testing Strategy Understanding

**Mock Data Design**
- `mock_tdnet_page.html`: Realistic HTML structure mimicking actual TDnet pages
- Multiple disclosure types: Regular, XBRL, corrections, different exchanges
- Edge cases: Empty pages, malformed data, missing elements

**Test Categories**
```python
# Unit Tests - Isolated component testing
def test_disclosure_creation_valid(self):
    # Pydantic model validation testing
    
# Integration Tests - Component interaction  
@patch('main.requests.Session')
def test_scrape_tdnet_by_date_success(self, mock_session):
    # HTTP mocking with realistic responses
    
# CLI Tests - User interface testing
def test_main_structured_output(self):
    # sys.argv mocking and output verification
```

#### Extension Points for Development

**1. Data Storage Extensions**
```python
# Add new storage backends
def save_to_elasticsearch(result: TdnetScrapingResult):
    # Elasticsearch integration using model_dump()
    
def save_to_mongodb(result: TdnetScrapingResult):  
    # MongoDB integration with Pydantic serialization
```

**2. Analysis Extensions**
```python  
# Add computed fields to models
@computed_field
@property
def sentiment_score(self) -> float:
    # NLP analysis of disclosure titles
    
@computed_field
@property  
def market_impact_level(self) -> str:
    # Business logic for impact assessment
```

**3. API Extensions**
```python
# FastAPI endpoints using existing models
@app.get("/disclosures/{date}/analytics")
async def get_analytics(date: date) -> Dict[str, Any]:
    result = scrape_tdnet_by_date(date)
    return {
        'companies_by_exchange': result.companies_by_exchange,
        'xbrl_percentage': len(result.get_disclosures_with_xbrl()) / len(result.disclosures)
    }
```

### Development Workflow

#### 1. **Setup & Testing**
```bash
# Environment setup
python -m venv .venv
source .venv/bin/activate  
pip install -r requirements.txt

# Run tests with coverage
python -m pytest tests/ --cov=main --cov-report=html

# Manual testing
python main.py --date=2025-10-22 --json
```

#### 2. **Code Quality Checks**
```bash
# Type checking (if mypy installed)
mypy main.py

# Linting (if flake8 installed)  
flake8 main.py --max-line-length=100

# Testing specific components
python -m pytest tests/test_main.py::TestTdnetDisclosureModel -v
```

#### 3. **Debugging Strategies**
```python
# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Use debug parsing utility
from tests.debug_parsing import debug_html_structure
debug_html_structure("path/to/html/file")

# Interactive testing
from main import *
result = scrape_tdnet_by_date(date(2025, 10, 22))
result.disclosures[0].model_dump()  # Inspect data
```

## 📄 License & Usage Guidelines

### Educational & Research Use
This project is designed for:
- ✅ Educational purposes and learning modern Python/Pydantic patterns
- ✅ Research into Japanese corporate disclosure patterns  
- ✅ Data analysis and academic studies
- ✅ Building larger financial analysis systems

### Responsible Usage
- 📋 **Respect TDnet Terms**: Follow TDnet's robots.txt and terms of service
- 📋 **Rate Limiting**: Built-in delays prevent server overload
- 📋 **Data Privacy**: Public disclosure data only, no personal information
- 📋 **Attribution**: Credit original data source (TDnet) in derivative works

### Technical Support
- 📧 **Issues**: Use GitHub issues for bug reports and feature requests
- 📚 **Documentation**: Comprehensive inline documentation and type hints
- 🧪 **Testing**: 89% test coverage ensures reliability
- 🔄 **Updates**: Modern Pydantic patterns ensure future compatibility

## 🤝 Contributing Guidelines

### Code Standards
- **Type Hints**: All functions and methods must have complete type annotations
- **Pydantic Models**: Use modern V2+ patterns with Annotated types
- **Testing**: Maintain 85%+ test coverage for all new code
- **Documentation**: Comprehensive docstrings with examples

### Pull Request Process
1. **Fork & Branch**: Create feature branch from main
2. **Implement**: Follow existing patterns and conventions  
3. **Test**: Add tests for new functionality
4. **Document**: Update README and inline documentation
5. **Coverage**: Verify test coverage meets requirements

---

**Happy Scraping! 🎯**

### Research Summary financial extraction

After applying the additive Research migrations and storing an `xbrl_zip` artifact, extract the four supported TDnet Summary financial concepts with:

```bash
HFT_RESEARCH_DATABASE_URL='postgresql://tdnet_ingest:...@localhost:54790/hft_research' \
python -m tdnet.research_summary --source-id 'https://www.release.tdnet.info/inbs/1401....pdf' \
  --content-hash '<64-lowercase-hex-sha256>'
```

The command reads the exact stored artifact version, writes an idempotent raw extraction and typed fact projection, and leaves every row `ready_for_analysis=false`.

*Built with modern Python patterns and comprehensive testing for reliable corporate disclosure data extraction.*
