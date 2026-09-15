#!/usr/bin/env bash
set -euo pipefail

# Upload a workbook, poll the job, download context.json and context.md into OUT_DIR.
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

code="$(http_get "/v1/context-jobs/${job_id}/context.json" "$RUN_DIR/context.json")"
if [[ "$code" != "200" ]]; then
  echo "context.json expected HTTP 200, got ${code}" >&2
  exit 1
fi

code="$(http_get "/v1/context-jobs/${job_id}/context.md" "$RUN_DIR/context.md")"
if [[ "$code" != "200" ]]; then
  echo "context.md expected HTTP 200, got ${code}" >&2
  exit 1
fi

echo "OK  job ${job_id} (${job_status})  wrote ${RUN_DIR}"
