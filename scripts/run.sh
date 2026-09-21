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
if [[ ! -f "$RUN_DIR/graph.json" ]]; then
  echo "graph.json missing; job did not publish the formula-graph sidecar" >&2
  exit 1
fi
for name in graph-edges.json graph-dangling.json formulas.json; do
  if [[ ! -f "$RUN_DIR/$name" ]]; then
    echo "${name} missing; job did not publish the JSON graph audit sidecar" >&2
    exit 1
  fi
done
python3 "$SCRIPTS/extract-unmapped.py" \
  "$RUN_DIR/context.json" \
  --output "$RUN_DIR/unmapped.json"
log_summary "unmapped=$RUN_DIR/unmapped.json"
log_summary "graph=$RUN_DIR/graph.json"
log_summary "graph_edges=$RUN_DIR/graph-edges.json"
log_summary "graph_dangling=$RUN_DIR/graph-dangling.json"
log_summary "formulas=$RUN_DIR/formulas.json"
if [[ -f "$RUN_DIR/graph-trace.json" ]]; then
  log_summary "graph_trace=$RUN_DIR/graph-trace.json"
fi

log_summary "ok"
echo "OK  wrote ${RUN_DIR}"
