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

if [[ ! -f "$RUN_DIR/json/context.json" ]]; then
  echo "json/context.json missing; cannot extract unmapped rows" >&2
  exit 1
fi
if [[ ! -f "$RUN_DIR/json/graph.json" ]]; then
  echo "json/graph.json missing; job did not publish the formula graph" >&2
  exit 1
fi
for name in context.md graph.md; do
  if [[ ! -f "$RUN_DIR/md/$name" ]]; then
    echo "md/${name} missing; job did not publish the markdown twin" >&2
    exit 1
  fi
done
python3 "$SCRIPTS/extract-unmapped.py" \
  "$RUN_DIR/json/context.json" \
  --output "$RUN_DIR/unmapped.json"
log_summary "unmapped=$RUN_DIR/unmapped.json"
if axes="$(python3 "$SCRIPTS/check-graph.py" "$RUN_DIR/json/context.json" \
  "$RUN_DIR/json/graph.json" --axes-summary)"; then
  log_summary "$axes"
else
  echo "warning: check-graph.py rejected json/context.json or json/graph.json" >&2
fi
log_summary "context_json=$RUN_DIR/json/context.json"
log_summary "context_md=$RUN_DIR/md/context.md"
log_summary "graph_json=$RUN_DIR/json/graph.json"
log_summary "graph_md=$RUN_DIR/md/graph.md"
if [[ -f "$RUN_DIR/json/trace.json" ]]; then
  log_summary "trace_json=$RUN_DIR/json/trace.json"
  log_summary "trace_md=$RUN_DIR/md/trace.md"
fi

log_summary "ok"
echo "OK  wrote ${RUN_DIR}"
