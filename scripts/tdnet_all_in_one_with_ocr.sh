#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# PARKED (2026-10-25): this wraps tdnet_all_in_one.sh --legacy-local, which
# now always refuses to run (see the matching comment and message there, and
# tdnet/database.py for the Python-side guard). Delegate so the one retire
# message stays in one place.
exec "${SCRIPT_DIR}/tdnet_all_in_one.sh" --legacy-local --with-ocr "$@"
