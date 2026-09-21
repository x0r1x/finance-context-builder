#!/usr/bin/env bash
set -euo pipefail

# Upload a workbook, poll the job, download context.json, graph.json,
# graph-edges.json, graph-dangling.json, formulas.json, context.md,
# and a smoke graph/trace into OUT_DIR.
# Usage: run-context-job.sh [path/to/model.xlsx]
# Env: BASE_URL, OUT_DIR, RUN_DIR, JOB_TIMEOUT_SEC

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
need_cmds
ensure_run_dir

workbook="$(resolve_workbook "${1:-}")"
log_summary "workbook=${workbook}"

code="$(http_post_file /v1/context-jobs "$RUN_DIR/post-job.json" "$workbook")"
if [[ "$code" != "200" && "$code" != "202" ]]; then
  echo "POST /v1/context-jobs expected 200 or 202, got ${code}" >&2
  exit 1
fi

job_id="$(json_field "$RUN_DIR/post-job.json" job_id)"
if [[ -z "$job_id" ]]; then
  echo "POST response missing job_id" >&2
  exit 1
fi
log_summary "job_id=${job_id}"

job_status="$(poll_job "$job_id" "$RUN_DIR/job-status.json")"
log_summary "job_status=${job_status}"

if [[ "$job_status" == "failed" ]]; then
  echo "job ${job_id} failed; status saved to ${RUN_DIR}/job-status.json" >&2
  exit 1
fi

graph_url="$(json_field "$RUN_DIR/job-status.json" graph_json_url)"
if [[ "$graph_url" != "/v1/context-jobs/${job_id}/graph.json" ]]; then
  echo "job status missing graph_json_url for ${job_id}" >&2
  exit 1
fi
edges_url="$(json_field "$RUN_DIR/job-status.json" graph_edges_url)"
if [[ "$edges_url" != "/v1/context-jobs/${job_id}/graph/edges" ]]; then
  echo "job status missing graph_edges_url for ${job_id}" >&2
  exit 1
fi
dangling_url="$(json_field "$RUN_DIR/job-status.json" graph_dangling_url)"
if [[ "$dangling_url" != "/v1/context-jobs/${job_id}/graph-dangling.json" ]]; then
  echo "job status missing graph_dangling_url for ${job_id}" >&2
  exit 1
fi
formulas_url="$(json_field "$RUN_DIR/job-status.json" formulas_json_url)"
if [[ "$formulas_url" != "/v1/context-jobs/${job_id}/formulas.json" ]]; then
  echo "job status missing formulas_json_url for ${job_id}" >&2
  exit 1
fi

require_get "/v1/context-jobs/${job_id}/context.json" "$RUN_DIR/context.json"
require_get "/v1/context-jobs/${job_id}/graph.json" "$RUN_DIR/graph.json"
require_get "/v1/context-jobs/${job_id}/graph/edges" "$RUN_DIR/graph-edges.json"
require_get "/v1/context-jobs/${job_id}/graph-dangling.json" "$RUN_DIR/graph-dangling.json"
require_get "/v1/context-jobs/${job_id}/formulas.json" "$RUN_DIR/formulas.json"
require_get "/v1/context-jobs/${job_id}/context.md" "$RUN_DIR/context.md"

origin="$(
  python3 "$_COMMON_DIR/check-graph.py" \
    "$RUN_DIR/context.json" \
    "$RUN_DIR/graph.json" \
    --edges "$RUN_DIR/graph-edges.json" \
    --dangling "$RUN_DIR/graph-dangling.json" \
    --formulas "$RUN_DIR/formulas.json" \
    --print-origin
)"
if [[ -n "$origin" ]]; then
  encoded="$(urlencode "$origin")"
  require_get \
    "/v1/context-jobs/${job_id}/graph/trace?from=${encoded}&direction=precedents&depth=6" \
    "$RUN_DIR/graph-trace.json"
  python3 "$_COMMON_DIR/check-graph.py" \
    --trace "$RUN_DIR/graph-trace.json" \
    --origin "$origin"
  log_summary "graph_trace_from=${origin}"
else
  log_summary "graph_trace skipped (no formula cell, concept_id, or row_key)"
fi

echo "OK  job ${job_id} (${job_status})  wrote ${RUN_DIR}"
