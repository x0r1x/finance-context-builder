#!/usr/bin/env bash
set -euo pipefail

# Calls the other API scripts. Service must already be running.
# Usage: run.sh [path/to/model.xlsx]
# Env: BASE_URL, OUT_DIR, JOB_TIMEOUT_SEC

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPTS/common.sh"
need_cmds

export RUN_DIR="${RUN_DIR:-$OUT_DIR/$(date +%Y%m%d-%H%M%S)}"
ensure_run_dir

workbook="$(resolve_workbook "${1:-}")"

"$SCRIPTS/check-service.sh"
"$SCRIPTS/run-context-job.sh" "$workbook"

if [[ ! -f "$RUN_DIR/context.json" ]]; then
  echo "context.json missing; cannot extract unmapped rows" >&2
  exit 1
fi
python3 "$SCRIPTS/extract-unmapped.py" \
  "$RUN_DIR/context.json" \
  --output "$RUN_DIR/unmapped.json"
log_summary "unmapped=$RUN_DIR/unmapped.json"

log_summary "ok"
echo "OK  wrote ${RUN_DIR}"
