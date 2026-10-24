# TDnet research ingest

This adapter captures TDnet list pages and writes only validated metadata to the
PostgreSQL 18 research boundary. It does not download disclosure documents and
does not mark records ready for downstream analysis.

Capture a date and validate it without writing to PostgreSQL:

```sh
cd "$(git rev-parse --show-toplevel)"
/Users/nrm176p/GitHub2/tdnet-api/tdnet-scraping-app/.venv/bin/python \
  -m tdnet.research_ingest --date 2026-09-04 --dry-run --output capture.json
```

Replay an existing capture without network access:

```sh
cd "$(git rev-parse --show-toplevel)"
/Users/nrm176p/GitHub2/tdnet-api/tdnet-scraping-app/.venv/bin/python \
  -m tdnet.research_ingest --input capture.json --dry-run
```

## Manual daily capture

Run the date-bounded module through the controller-owned credential wrapper.
The canonical daily entry point is:

```sh
scripts/tdnet_daily_metadata.sh --date 2026-09-10
```

The broad `tdnet_all_in_one.sh` workflow is legacy and parked (2026-10-25): it
now refuses to run regardless of the `--legacy-local` opt-in (see
`tdnet/database.py`); it was never part of the daily metadata path.

```sh
cd "$(git rev-parse --show-toplevel)"
PYTHONPATH=/Users/nrm176p/GitHub2/hedge-fund-takedo/src \
  /Users/nrm176p/GitHub2/hedge-fund-takedo/.venv/bin/python \
  -m hedge_research.disclosure_environment exec tdnet -- \
  /Users/nrm176p/GitHub2/tdnet-api/tdnet-scraping-app/.venv/bin/python \
  -m tdnet.research_ingest --date 2026-09-09 \
  --output /Users/nrm176p/GitHub2/hedge-fund-takedo/.research-runtime/disclosure-captures/tdnet-20260909-run1.json
```

Use a new `--output` suffix for each live rerun because capture files are
created exclusively and are never overwritten. To replay the same evidence
without HTTP, replace `--date ... --output ...` with `--input` and the saved
capture's absolute path. Upsert semantics make a same-content rerun update only
its observation interval: it does not duplicate the snapshot or documents.

The wrapper injects only the approved local `tdnet_ingest` PostgreSQL URL. The
command refuses other roles, hosts, ports, databases, PostgreSQL major versions,
and instance markers. Do not copy the connection URL into shell history or
project files.

The migration is `sql/research/001_tdnet_metadata.sql`. The controller applies
it as the research owner. Runtime code performs no DDL. The writer receives only
`SELECT`, `INSERT`, and `UPDATE`; `research_reader` receives only `SELECT`.

The snapshot hash covers canonical JSON containing `target_date` and the decoded
HTML page payload. This is decoded response content, not wire-byte evidence.
Within one transaction, replay updates observation extrema, newer observations
replace current metadata, stale observations cannot demote it, and an equal-time
content conflict rolls back. `ready_for_analysis` remains false by constraint.
Document downloads, OCR, scheduled execution, and automatic retry/resume remain
separate later stages.
