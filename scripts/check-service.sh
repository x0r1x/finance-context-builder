#!/usr/bin/env bash
set -euo pipefail

# Probe GET /healthz and GET /readyz. Writes JSON into OUT_DIR (default ./out).
# Env: BASE_URL, OUT_DIR, RUN_DIR (reuse an existing run folder).

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
need_cmds
ensure_run_dir

fail=0
code="$(http_get /healthz "$RUN_DIR/healthz.json")"
if [[ "$code" != "200" ]]; then
  echo "healthz expected HTTP 200, got ${code}" >&2
  fail=1
elif [[ "$(json_field "$RUN_DIR/healthz.json" status)" != "ok" ]]; then
  echo "healthz body status is not ok" >&2
  fail=1
fi

code="$(http_get /readyz "$RUN_DIR/readyz.json")"
if [[ "$code" != "200" ]]; then
  echo "readyz expected HTTP 200, got ${code}" >&2
  fail=1
elif [[ "$(json_field "$RUN_DIR/readyz.json" status)" != "ready" ]]; then
  echo "readyz body status is not ready" >&2
  fail=1
fi

log_summary "run_dir=${RUN_DIR}"
if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "OK  wrote ${RUN_DIR}"
