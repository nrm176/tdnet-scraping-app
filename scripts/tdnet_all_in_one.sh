#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# PARKED (2026-10-25): this --legacy-local pipeline is disabled, not deleted.
# It always refuses to run now, whether or not --legacy-local is passed --
# see tdnet/database.py for the matching Python-side guard
# (TDNET_LEGACY_LOCAL_ENABLED) that this script would otherwise hit anyway
# once it reached the first `tdnet` subcommand.
cat >&2 <<'EOF'
The legacy tdnet-postgres pipeline is retired (2026-10-25). TDnet disclosure
history now lives in the research database's raw_tdnet_history (see
docs/runbooks/tdnet-history.md in the hedge-fund-takedo repo). Document
download/parse against this local database will move to the research DB
under Root issue #50.

This script is parked, not deleted -- set TDNET_LEGACY_LOCAL_ENABLED=1 and
re-enable the guard in tdnet/database.py to run it deliberately.

For the normal daily TDnet entry, use scripts/tdnet_daily_metadata.sh instead.
EOF
exit 2

DAYS="30"
START_DATE=""
END_DATE=""
DOWNLOAD_LIMIT="20000"
DOWNLOAD_CONCURRENCY="8"
PARSE_LIMIT="20000"
PARSE_WORKERS=""
PARSE_TEXT_LIMIT="20000"
TAG_LIMIT="20000"
WITH_OCR="false"
OCR_LIMIT="100"
OCR_WORKERS="4"
WITH_IXBRL="false"
IXBRL_LIMIT="100"
IXBRL_STRATEGY="garbled"
WITH_REVIEW="false"
REVIEW_LIMIT="50"
REVIEW_STRATEGY="suspicious"
FORCE_SCRAPE="false"
SCRAPE_LOOKBACK_DAYS="0"
RETRY_FAILED="false"
RETAG="false"
SKIP_INSTALL="false"
SKIP_POSTGRES="false"
SKIP_SCRAPE="false"
SKIP_DOWNLOAD="false"
SKIP_PARSE="false"
SKIP_PARSE_TEXT="false"
SKIP_TAG="false"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Run the legacy TDnet pipeline end to end (explicit opt-in required).

Default pipeline:
  1. Start Docker Postgres
  2. Ensure local Python environment
  3. Scrape/persist the last 30 days
  4. Download missing PDF/XBRL artifacts
  5. Parse downloaded PDFs
  6. Backfill searchable parse text
  7. Classify reports with deterministic tags

Usage:
  scripts/tdnet_all_in_one.sh --legacy-local [options]

Date options:
  --days N                 Number of days through --end-date. Default: 30
  --start-date YYYY-MM-DD  Explicit start date. Overrides --days
  --end-date YYYY-MM-DD    End date. Default: today in Asia/Tokyo

Pipeline options:
  --download-limit N       Download candidate disclosure limit. Default: 20000
  --download-concurrency N Async download concurrency. Default: 8
  --parse-limit N          Parse candidate file limit. Default: 20000
  --parse-workers N        Parser worker processes. Default: tdnet CLI default
  --parse-text-limit N     Parse text backfill limit. Default: 20000
  --tag-limit N            Report tagging candidate limit. Default: 20000
  --force-scrape           Ignore the Postgres checkpoint and scrape the full requested range
  --scrape-lookback-days N  Overlap days before the next unsynced date to include. Default: 0
  --retry-failed           Retry failed download/parse/parse-text/OCR rows
  --retag                  Recalculate existing report tags in the date range
  --with-ocr               Also run Apple Vision OCR for sparse parses
  --ocr-limit N            OCR candidate limit. Default: 100
  --ocr-workers N          OCR workers. Default: 4
  --with-ixbrl             Also extract text from downloaded iXBRL ZIP sidecars
  --ixbrl-limit N          iXBRL candidate limit. Default: 100
  --ixbrl-strategy NAME    garbled|forecast-correction|all. Default: garbled
  --with-review            Generate a parse review report
  --review-limit N         Review report document limit. Default: 50
  --review-strategy NAME   suspicious|random|recent|forecast-correction. Default: suspicious

Skip options:
  --skip-install
  --skip-postgres
  --skip-scrape
  --skip-download
  --skip-parse
  --skip-parse-text
  --skip-tag

Other:
  --dry-run                Print commands without executing them
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --start-date) START_DATE="$2"; shift 2 ;;
    --end-date) END_DATE="$2"; shift 2 ;;
    --download-limit) DOWNLOAD_LIMIT="$2"; shift 2 ;;
    --download-concurrency) DOWNLOAD_CONCURRENCY="$2"; shift 2 ;;
    --parse-limit) PARSE_LIMIT="$2"; shift 2 ;;
    --parse-workers) PARSE_WORKERS="$2"; shift 2 ;;
    --parse-text-limit) PARSE_TEXT_LIMIT="$2"; shift 2 ;;
    --tag-limit) TAG_LIMIT="$2"; shift 2 ;;
    --force-scrape) FORCE_SCRAPE="true"; shift ;;
    --scrape-lookback-days) SCRAPE_LOOKBACK_DAYS="$2"; shift 2 ;;
    --retry-failed) RETRY_FAILED="true"; shift ;;
    --retag) RETAG="true"; shift ;;
    --with-ocr) WITH_OCR="true"; shift ;;
    --ocr-limit) OCR_LIMIT="$2"; shift 2 ;;
    --ocr-workers) OCR_WORKERS="$2"; shift 2 ;;
    --with-ixbrl) WITH_IXBRL="true"; shift ;;
    --ixbrl-limit) IXBRL_LIMIT="$2"; shift 2 ;;
    --ixbrl-strategy) IXBRL_STRATEGY="$2"; shift 2 ;;
    --with-review) WITH_REVIEW="true"; shift ;;
    --review-limit) REVIEW_LIMIT="$2"; shift 2 ;;
    --review-strategy) REVIEW_STRATEGY="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL="true"; shift ;;
    --skip-postgres) SKIP_POSTGRES="true"; shift ;;
    --skip-scrape) SKIP_SCRAPE="true"; shift ;;
    --skip-download) SKIP_DOWNLOAD="true"; shift ;;
    --skip-parse) SKIP_PARSE="true"; shift ;;
    --skip-parse-text) SKIP_PARSE_TEXT="true"; shift ;;
    --skip-tag) SKIP_TAG="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

cd "${REPO_ROOT}"
mkdir -p logs
RUN_ID="$(date '+%Y%m%d-%H%M%S')-$$"
PIPELINE_LOG="logs/tdnet-all-in-one-${RUN_ID}.log"
LATEST_LOG="logs/tdnet-all-in-one-latest.log"
PIPELINE_STARTED_EPOCH="$(date '+%s')"
STEP_STARTED_EPOCH="${PIPELINE_STARTED_EPOCH}"
CURRENT_STEP="initializing"
CURRENT_STEP_ORDER=""
PIPELINE_STEP_ORDER="0"
PIPELINE_HISTORY_ENABLED="false"
LAST_COMMAND_TEXT=""
LAST_COMMAND_EXIT_CODE=""
LAST_COMMAND_METRICS_FILE=""

ln -sfn "$(basename "${PIPELINE_LOG}")" "${LATEST_LOG}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "${PIPELINE_LOG}"
}

format_duration() {
  local seconds="$1"
  printf '%02d:%02d:%02d' "$((seconds / 3600))" "$(((seconds % 3600) / 60))" "$((seconds % 60))"
}

record_pipeline_history() {
  local history_exit
  if [[ "${DRY_RUN}" == "true" ]]; then
    return 0
  fi
  if [[ ! -x "${TDNET:-}" ]]; then
    return 0
  fi
  set +e
  "${TDNET}" pipeline-run "$@" 2>>"${PIPELINE_LOG}"
  history_exit="$?"
  set -e
  if [[ "${history_exit}" -ne 0 ]]; then
    log "PIPELINE_HISTORY_FAIL action=$1 exit_code=${history_exit}"
  fi
}

finish_pipeline() {
  local exit_code=$?
  local finished_epoch
  local elapsed_seconds
  local failed_step
  finished_epoch="$(date '+%s')"
  elapsed_seconds="$((finished_epoch - PIPELINE_STARTED_EPOCH))"
  if [[ "${exit_code}" -eq 0 ]]; then
    log "TDnet all-in-one pipeline finished run_id=${RUN_ID} status=success elapsed_seconds=${elapsed_seconds} elapsed=$(format_duration "${elapsed_seconds}")"
    if [[ "${PIPELINE_HISTORY_ENABLED}" == "true" ]]; then
      record_pipeline_history finish --run-id "${RUN_ID}" --status completed --elapsed-seconds "${elapsed_seconds}" --exit-code "${exit_code}"
    fi
  else
    log "TDnet all-in-one pipeline failed run_id=${RUN_ID} status=failed step=${CURRENT_STEP} exit_code=${exit_code} elapsed_seconds=${elapsed_seconds} elapsed=$(format_duration "${elapsed_seconds}")"
    if [[ "${PIPELINE_HISTORY_ENABLED}" == "true" ]]; then
      failed_step="${CURRENT_STEP}"
      record_pipeline_history finish --run-id "${RUN_ID}" --status failed --elapsed-seconds "${elapsed_seconds}" --failed-step "${failed_step}" --exit-code "${exit_code}"
    fi
  fi
  log "Pipeline log: ${PIPELINE_LOG}"
  log "Latest log: ${LATEST_LOG}"
}

trap finish_pipeline EXIT

start_step() {
  CURRENT_STEP="$1"
  CURRENT_STEP_ORDER=""
  LAST_COMMAND_TEXT=""
  LAST_COMMAND_EXIT_CODE=""
  LAST_COMMAND_METRICS_FILE=""
  STEP_STARTED_EPOCH="$(date '+%s')"
  log "STEP_START name=${CURRENT_STEP}"
  if [[ "${PIPELINE_HISTORY_ENABLED}" == "true" ]]; then
    PIPELINE_STEP_ORDER="$((PIPELINE_STEP_ORDER + 1))"
    CURRENT_STEP_ORDER="${PIPELINE_STEP_ORDER}"
    record_pipeline_history step-start --run-id "${RUN_ID}" --step-name "${CURRENT_STEP}" --step-order "${CURRENT_STEP_ORDER}"
  fi
}

finish_step() {
  local finished_epoch
  local elapsed_seconds
  finished_epoch="$(date '+%s')"
  elapsed_seconds="$((finished_epoch - STEP_STARTED_EPOCH))"
  log "STEP_FINISH name=${CURRENT_STEP} elapsed_seconds=${elapsed_seconds} elapsed=$(format_duration "${elapsed_seconds}")"
  if [[ "${PIPELINE_HISTORY_ENABLED}" == "true" && -n "${CURRENT_STEP_ORDER}" ]]; then
    record_pipeline_history step-finish \
      --run-id "${RUN_ID}" \
      --step-name "${CURRENT_STEP}" \
      --status completed \
      --elapsed-seconds "${elapsed_seconds}" \
      --exit-code "${LAST_COMMAND_EXIT_CODE:-0}" \
      --command "${LAST_COMMAND_TEXT}" \
      --metrics-file "${LAST_COMMAND_METRICS_FILE}"
  fi
  if [[ -n "${LAST_COMMAND_METRICS_FILE}" ]]; then
    rm -f "${LAST_COMMAND_METRICS_FILE}"
  fi
  CURRENT_STEP="idle"
  CURRENT_STEP_ORDER=""
}

skip_step() {
  log "STEP_SKIP name=$1 reason=$2"
  if [[ "${PIPELINE_HISTORY_ENABLED}" == "true" ]]; then
    PIPELINE_STEP_ORDER="$((PIPELINE_STEP_ORDER + 1))"
    record_pipeline_history step-skip --run-id "${RUN_ID}" --step-name "$1" --step-order "${PIPELINE_STEP_ORDER}" --reason "$2"
  fi
}

run() {
  local command_text
  local command_started_epoch
  local command_finished_epoch
  local elapsed_seconds
  local step_elapsed_seconds
  local exit_code
  local command_output
  printf -v command_text '%q ' "$@"
  command_text="${command_text% }"
  LAST_COMMAND_TEXT="${command_text}"
  LAST_COMMAND_EXIT_CODE=""
  LAST_COMMAND_METRICS_FILE=""
  command_started_epoch="$(date '+%s')"
  log "COMMAND_START step=${CURRENT_STEP} command=${command_text}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "COMMAND_DRY_RUN step=${CURRENT_STEP} command=${command_text}"
    LAST_COMMAND_EXIT_CODE="0"
    return 0
  fi
  command_output="$(mktemp "${TMPDIR:-/tmp}/tdnet-pipeline-step.XXXXXX")"
  LAST_COMMAND_METRICS_FILE="${command_output}"
  set +e
  "$@" 2>&1 | tee -a "${PIPELINE_LOG}" | tee "${command_output}"
  exit_code="${PIPESTATUS[0]}"
  set -e
  LAST_COMMAND_EXIT_CODE="${exit_code}"
  command_finished_epoch="$(date '+%s')"
  elapsed_seconds="$((command_finished_epoch - command_started_epoch))"
  if [[ "${exit_code}" -ne 0 ]]; then
    log "COMMAND_FAIL step=${CURRENT_STEP} exit_code=${exit_code} elapsed_seconds=${elapsed_seconds} elapsed=$(format_duration "${elapsed_seconds}") command=${command_text}"
    if [[ "${PIPELINE_HISTORY_ENABLED}" == "true" && -n "${CURRENT_STEP_ORDER}" ]]; then
      step_elapsed_seconds="$((command_finished_epoch - STEP_STARTED_EPOCH))"
      record_pipeline_history step-finish \
        --run-id "${RUN_ID}" \
        --step-name "${CURRENT_STEP}" \
        --status failed \
        --elapsed-seconds "${step_elapsed_seconds}" \
        --exit-code "${exit_code}" \
        --command "${command_text}" \
        --metrics-file "${command_output}"
    fi
    rm -f "${command_output}"
    return "${exit_code}"
  fi
  log "COMMAND_FINISH step=${CURRENT_STEP} exit_code=0 elapsed_seconds=${elapsed_seconds} elapsed=$(format_duration "${elapsed_seconds}") command=${command_text}"
}

find_python() {
  if command -v python3.13 >/dev/null 2>&1; then
    echo "python3.13"
  elif command -v python3.12 >/dev/null 2>&1; then
    echo "python3.12"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  else
    echo "python"
  fi
}

wait_for_postgres() {
  local attempts=40
  local i
  for ((i = 1; i <= attempts; i++)); do
    if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-tdnet}" -d "${POSTGRES_DB:-tdnet}" >/dev/null 2>&1; then
      log "Postgres is ready."
      return 0
    fi
    sleep 1
  done
  log "Postgres did not become ready after ${attempts}s."
  return 1
}

latest_disclosure_date() {
  "${PYTHON}" - 2> >(tee -a "${PIPELINE_LOG}" >&2) <<'PY'
import asyncio

from tdnet.database import SessionLocal, init_db
from tdnet.repository import get_latest_disclosure_date


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        latest = await get_latest_disclosure_date(session)
    if latest is not None:
        print(latest.isoformat())


asyncio.run(main())
PY
}

SYSTEM_PYTHON="$(find_python)"

log "TDnet all-in-one pipeline started run_id=${RUN_ID}"
log "Pipeline log: ${PIPELINE_LOG}"
log "Latest log: ${LATEST_LOG}"
log "Repository root: ${REPO_ROOT}"
log "Options: days=${DAYS} start_date=${START_DATE:-auto} end_date=${END_DATE:-auto} force_scrape=${FORCE_SCRAPE} scrape_lookback_days=${SCRAPE_LOOKBACK_DAYS} retry_failed=${RETRY_FAILED} retag=${RETAG} with_ocr=${WITH_OCR} with_ixbrl=${WITH_IXBRL} with_review=${WITH_REVIEW} dry_run=${DRY_RUN}"
log "Limits: download=${DOWNLOAD_LIMIT} parse=${PARSE_LIMIT} parse_text=${PARSE_TEXT_LIMIT} tag=${TAG_LIMIT} ocr=${OCR_LIMIT} ixbrl=${IXBRL_LIMIT} review=${REVIEW_LIMIT}"
log "Strategies: ixbrl=${IXBRL_STRATEGY} review=${REVIEW_STRATEGY}"
log "Skip flags: install=${SKIP_INSTALL} postgres=${SKIP_POSTGRES} scrape=${SKIP_SCRAPE} download=${SKIP_DOWNLOAD} parse=${SKIP_PARSE} parse_text=${SKIP_PARSE_TEXT} tag=${SKIP_TAG}"

if [[ "${SKIP_POSTGRES}" != "true" ]]; then
  start_step "postgres"
  run docker compose up -d postgres
  if [[ "${DRY_RUN}" != "true" ]]; then
    wait_for_postgres
  fi
  finish_step
else
  skip_step "postgres" "--skip-postgres"
fi

if [[ ! -x ".venv/bin/python" || "${SKIP_INSTALL}" != "true" ]]; then
  if [[ ! -x ".venv/bin/python" ]]; then
    start_step "create-venv"
    run "${SYSTEM_PYTHON}" -m venv .venv
    finish_step
  fi
fi

PYTHON=".venv/bin/python"
TDNET=".venv/bin/tdnet"

if [[ "${SKIP_INSTALL}" != "true" ]]; then
  start_step "install"
  run "${PYTHON}" -m pip install --upgrade pip
  run "${PYTHON}" -m pip install -e ".[dev]"
  finish_step
else
  skip_step "install" "--skip-install"
fi

if [[ ! -x "${TDNET}" ]]; then
  log "tdnet CLI was not found at ${TDNET}. Run without --skip-install first."
  exit 1
fi

CURRENT_STEP="date-range"
REQUESTED_START_DATE="${START_DATE}"
REQUESTED_END_DATE="${END_DATE}"
CHECKPOINT_LATEST_DATE=""
CHECKPOINT_DISABLED_REASON=""

if [[ -n "${REQUESTED_START_DATE}" ]]; then
  CHECKPOINT_DISABLED_REASON="explicit_start_date"
elif [[ "${FORCE_SCRAPE}" == "true" ]]; then
  CHECKPOINT_DISABLED_REASON="force_scrape"
elif [[ "${SKIP_SCRAPE}" == "true" ]]; then
  CHECKPOINT_DISABLED_REASON="skip_scrape"
elif [[ "${DRY_RUN}" == "true" ]]; then
  CHECKPOINT_DISABLED_REASON="dry_run"
else
  CHECKPOINT_LATEST_DATE="$(latest_disclosure_date)"
fi

DATE_JSON="$("${PYTHON}" - "$DAYS" "$REQUESTED_START_DATE" "$REQUESTED_END_DATE" "$CHECKPOINT_LATEST_DATE" "$SCRAPE_LOOKBACK_DAYS" 2> >(tee -a "${PIPELINE_LOG}" >&2) <<'PY'
import json
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

days = int(sys.argv[1])
start_arg = sys.argv[2] or None
end_arg = sys.argv[3] or None
latest_arg = sys.argv[4] or None
lookback_days = int(sys.argv[5])

if days < 1:
    raise SystemExit("--days must be greater than or equal to 1")
if lookback_days < 0:
    raise SystemExit("--scrape-lookback-days must be greater than or equal to 0")

today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
end = datetime.strptime(end_arg, "%Y-%m-%d").date() if end_arg else today
requested_start = datetime.strptime(start_arg, "%Y-%m-%d").date() if start_arg else end - timedelta(days=days - 1)
if requested_start > end:
    raise SystemExit("start date must be before or equal to end date")

start = requested_start
checkpoint_start = None
checkpoint_applied = False
if latest_arg:
    latest = datetime.strptime(latest_arg, "%Y-%m-%d").date()
    checkpoint_start = latest + timedelta(days=1 - lookback_days)
    if checkpoint_start > end and end == today:
        checkpoint_start = end
    if checkpoint_start > start:
        start = checkpoint_start
        checkpoint_applied = True

dates = []
current = start
while current <= end:
    dates.append(current.isoformat())
    current += timedelta(days=1)

print(
    json.dumps(
        {
            "requested_start": requested_start.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "checkpoint_start": checkpoint_start.isoformat() if checkpoint_start else None,
            "checkpoint_applied": checkpoint_applied,
            "dates": dates,
        }
    )
)
PY
)"

REQUESTED_START_DATE="$("${PYTHON}" -c 'import json,sys; print(json.loads(sys.argv[1])["requested_start"])' "${DATE_JSON}")"
START_DATE="$("${PYTHON}" -c 'import json,sys; print(json.loads(sys.argv[1])["start"])' "${DATE_JSON}")"
END_DATE="$("${PYTHON}" -c 'import json,sys; print(json.loads(sys.argv[1])["end"])' "${DATE_JSON}")"
CHECKPOINT_START_DATE="$("${PYTHON}" -c 'import json,sys; print(json.loads(sys.argv[1])["checkpoint_start"] or "")' "${DATE_JSON}")"
CHECKPOINT_APPLIED="$("${PYTHON}" -c 'import json,sys; print(str(json.loads(sys.argv[1])["checkpoint_applied"]).lower())' "${DATE_JSON}")"

DATES=()
while IFS= read -r scrape_date || [[ -n "${scrape_date}" ]]; do
  DATES+=("${scrape_date}")
done < <("${PYTHON}" -c 'import json,sys; sys.stdout.write("\n".join(json.loads(sys.argv[1])["dates"]))' "${DATE_JSON}")

if [[ -n "${CHECKPOINT_LATEST_DATE}" ]]; then
  log "CHECKPOINT source=postgres latest_disclosure_date=${CHECKPOINT_LATEST_DATE} lookback_days=${SCRAPE_LOOKBACK_DAYS} checkpoint_start=${CHECKPOINT_START_DATE} requested_start=${REQUESTED_START_DATE} effective_start=${START_DATE} end=${END_DATE} applied=${CHECKPOINT_APPLIED}"
elif [[ -n "${CHECKPOINT_DISABLED_REASON}" ]]; then
  log "CHECKPOINT_SKIP reason=${CHECKPOINT_DISABLED_REASON} requested_start=${REQUESTED_START_DATE} end=${END_DATE}"
else
  log "CHECKPOINT source=postgres latest_disclosure_date=none requested_start=${REQUESTED_START_DATE} end=${END_DATE} applied=false"
fi

if ((${#DATES[@]} > 0)); then
  log "Date range: ${START_DATE} through ${END_DATE} (${#DATES[@]} days)"
else
  log "Date range: no dates to scrape after checkpoint requested_start=${REQUESTED_START_DATE} effective_start=${START_DATE} end=${END_DATE}"
fi

PIPELINE_OPTIONS_JSON="$("${PYTHON}" - \
  "${DAYS}" \
  "${FORCE_SCRAPE}" \
  "${SCRAPE_LOOKBACK_DAYS}" \
  "${RETRY_FAILED}" \
  "${RETAG}" \
  "${WITH_OCR}" \
  "${WITH_IXBRL}" \
  "${WITH_REVIEW}" \
  "${DRY_RUN}" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "days": int(sys.argv[1]),
            "force_scrape": sys.argv[2] == "true",
            "scrape_lookback_days": int(sys.argv[3]),
            "retry_failed": sys.argv[4] == "true",
            "retag": sys.argv[5] == "true",
            "with_ocr": sys.argv[6] == "true",
            "with_ixbrl": sys.argv[7] == "true",
            "with_review": sys.argv[8] == "true",
            "dry_run": sys.argv[9] == "true",
        },
        sort_keys=True,
    )
)
PY
)"
PIPELINE_LIMITS_JSON="$("${PYTHON}" - \
  "${DOWNLOAD_LIMIT}" \
  "${DOWNLOAD_CONCURRENCY}" \
  "${PARSE_LIMIT}" \
  "${PARSE_WORKERS}" \
  "${PARSE_TEXT_LIMIT}" \
  "${TAG_LIMIT}" \
  "${OCR_LIMIT}" \
  "${OCR_WORKERS}" \
  "${IXBRL_LIMIT}" \
  "${REVIEW_LIMIT}" <<'PY'
import json
import sys


def optional_int(value: str) -> int | None:
    return int(value) if value else None


print(
    json.dumps(
        {
            "download": int(sys.argv[1]),
            "download_concurrency": int(sys.argv[2]),
            "parse": int(sys.argv[3]),
            "parse_workers": optional_int(sys.argv[4]),
            "parse_text": int(sys.argv[5]),
            "tag": int(sys.argv[6]),
            "ocr": int(sys.argv[7]),
            "ocr_workers": int(sys.argv[8]),
            "ixbrl": int(sys.argv[9]),
            "review": int(sys.argv[10]),
        },
        sort_keys=True,
    )
)
PY
)"
PIPELINE_STRATEGIES_JSON="$("${PYTHON}" - "${IXBRL_STRATEGY}" "${REVIEW_STRATEGY}" <<'PY'
import json
import sys

print(json.dumps({"ixbrl": sys.argv[1], "review": sys.argv[2]}, sort_keys=True))
PY
)"
PIPELINE_SKIP_FLAGS_JSON="$("${PYTHON}" - \
  "${SKIP_INSTALL}" \
  "${SKIP_POSTGRES}" \
  "${SKIP_SCRAPE}" \
  "${SKIP_DOWNLOAD}" \
  "${SKIP_PARSE}" \
  "${SKIP_PARSE_TEXT}" \
  "${SKIP_TAG}" <<'PY'
import json
import sys

names = ["install", "postgres", "scrape", "download", "parse", "parse_text", "tag"]
print(json.dumps({name: value == "true" for name, value in zip(names, sys.argv[1:])}, sort_keys=True))
PY
)"

record_pipeline_history start \
  --run-id "${RUN_ID}" \
  --log-path "${PIPELINE_LOG}" \
  --latest-log-path "${LATEST_LOG}" \
  --requested-start-date "${REQUESTED_START_DATE}" \
  --effective-start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --date-count "${#DATES[@]}" \
  --checkpoint-latest-date "${CHECKPOINT_LATEST_DATE}" \
  --checkpoint-start-date "${CHECKPOINT_START_DATE}" \
  --checkpoint-applied "${CHECKPOINT_APPLIED}" \
  --checkpoint-disabled-reason "${CHECKPOINT_DISABLED_REASON}" \
  --options-json "${PIPELINE_OPTIONS_JSON}" \
  --limits-json "${PIPELINE_LIMITS_JSON}" \
  --strategies-json "${PIPELINE_STRATEGIES_JSON}" \
  --skip-flags-json "${PIPELINE_SKIP_FLAGS_JSON}"
PIPELINE_HISTORY_ENABLED="true"
CURRENT_STEP="idle"

if [[ "${SKIP_SCRAPE}" != "true" ]]; then
  if ((${#DATES[@]} == 0)); then
    skip_step "scrape" "checkpoint produced no dates"
  else
    for scrape_date in "${DATES[@]}"; do
      start_step "scrape:${scrape_date}"
      run "${TDNET}" scrape --date "${scrape_date}" --persist --output-format urls
      finish_step
    done
  fi
else
  skip_step "scrape" "--skip-scrape"
fi

if [[ "${SKIP_DOWNLOAD}" != "true" ]]; then
  start_step "download"
  DOWNLOAD_ARGS=(download --limit "${DOWNLOAD_LIMIT}" --concurrency "${DOWNLOAD_CONCURRENCY}")
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    DOWNLOAD_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${DOWNLOAD_ARGS[@]}"
  finish_step
else
  skip_step "download" "--skip-download"
fi

if [[ "${SKIP_PARSE}" != "true" ]]; then
  start_step "parse"
  PARSE_ARGS=(parse --limit "${PARSE_LIMIT}")
  if [[ -n "${PARSE_WORKERS}" ]]; then
    PARSE_ARGS+=(--workers "${PARSE_WORKERS}")
  fi
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    PARSE_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${PARSE_ARGS[@]}"
  finish_step
else
  skip_step "parse" "--skip-parse"
fi

if [[ "${SKIP_PARSE_TEXT}" != "true" ]]; then
  start_step "persist-parse-text"
  run "${TDNET}" persist-parse-text --limit "${PARSE_TEXT_LIMIT}"
  finish_step
else
  skip_step "persist-parse-text" "--skip-parse-text"
fi

if [[ "${WITH_OCR}" == "true" ]]; then
  start_step "ocr"
  OCR_ARGS=(ocr --strategy low-text --limit "${OCR_LIMIT}" --workers "${OCR_WORKERS}")
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    OCR_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${OCR_ARGS[@]}"
  finish_step
else
  skip_step "ocr" "not requested"
fi

if [[ "${WITH_IXBRL}" == "true" ]]; then
  start_step "parse-ixbrl"
  IXBRL_ARGS=(parse-ixbrl --strategy "${IXBRL_STRATEGY}" --limit "${IXBRL_LIMIT}")
  if [[ "${RETRY_FAILED}" == "true" ]]; then
    IXBRL_ARGS+=(--retry-failed)
  fi
  run "${TDNET}" "${IXBRL_ARGS[@]}"
  finish_step
else
  skip_step "parse-ixbrl" "not requested"
fi

if [[ "${SKIP_TAG}" != "true" ]]; then
  if ((${#DATES[@]} == 0)); then
    skip_step "tag-reports" "checkpoint produced no dates"
  else
    start_step "tag-reports"
    TAG_ARGS=(tag-reports --limit "${TAG_LIMIT}" --from "${START_DATE}" --to "${END_DATE}")
    if [[ "${RETAG}" == "true" ]]; then
      TAG_ARGS+=(--force)
    fi
    run "${TDNET}" "${TAG_ARGS[@]}"
    finish_step
  fi
else
  skip_step "tag-reports" "--skip-tag"
fi

if [[ "${WITH_REVIEW}" == "true" ]]; then
  start_step "review-parse"
  run "${TDNET}" review-parse --strategy "${REVIEW_STRATEGY}" --limit "${REVIEW_LIMIT}"
  finish_step
else
  skip_step "review-parse" "not requested"
fi
