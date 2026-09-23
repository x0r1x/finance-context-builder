# Shared helpers for API scripts. Source from the other scripts; do not run directly.
# JOB_TIMEOUT_SEC is how long this client polls. It does not stop the book's process.
# The server stops that process after its own JOB_TIMEOUT_SEC (Settings default 3600).
# When this variable is unset here, the client default below is 300, even if the
# server was started from .env with 3600.

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_COMMON_DIR/.." && pwd)"

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
OUT_DIR="${OUT_DIR:-$ROOT/out}"
JOB_TIMEOUT_SEC="${JOB_TIMEOUT_SEC:-300}"

need_cmds() {
  local missing=0
  local cmd
  for cmd in curl python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "missing required command: $cmd" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    exit 1
  fi
}

ensure_run_dir() {
  if [[ -z "${RUN_DIR:-}" ]]; then
    RUN_DIR="$OUT_DIR/$(date +%Y%m%d-%H%M%S)"
  fi
  mkdir -p "$RUN_DIR"
  SUMMARY="${SUMMARY:-$RUN_DIR/summary.txt}"
  touch "$SUMMARY"
}

log_summary() {
  printf '%s\n' "$*" | tee -a "$SUMMARY" >&2
}

json_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],"") or "")' "$1" "$2"
}

# Usage: require_get PATH OUTFILE
# GET and fail the script unless HTTP 200.
require_get() {
  local path="$1"
  local outfile="$2"
  local code
  code="$(http_get "$path" "$outfile")"
  if [[ "$code" != "200" ]]; then
    echo "${path} expected HTTP 200, got ${code}" >&2
    exit 1
  fi
}

urlencode() {
  python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

# Usage: http_get PATH OUTFILE [quiet]
# Prints HTTP status code. Body is written to OUTFILE.
http_get() {
  local path="$1"
  local outfile="$2"
  local quiet="${3:-}"
  local code
  mkdir -p "$(dirname "$outfile")"
  code="$(curl -sS -o "$outfile" -w "%{http_code}" "${BASE_URL}${path}")"
  if [[ "$quiet" != "quiet" ]]; then
    log_summary "GET ${path} -> ${code} (${outfile#"$ROOT/"})"
  fi
  printf '%s\n' "$code"
}

# Usage: http_post_file PATH OUTFILE FILE [FILENAME]
http_post_file() {
  local path="$1"
  local outfile="$2"
  local file="$3"
  local filename="${4:-$(basename "$file")}"
  local code
  mkdir -p "$(dirname "$outfile")"
  code="$(curl -sS -o "$outfile" -w "%{http_code}" -F "file=@${file};filename=${filename}" "${BASE_URL}${path}")"
  log_summary "POST ${path} file=${filename} -> ${code} (${outfile#"$ROOT/"})"
  printf '%s\n' "$code"
}

resolve_workbook() {
  local given="${1:-}"
  if [[ -n "$given" ]]; then
    if [[ ! -f "$given" ]]; then
      echo "workbook not found: $given" >&2
      exit 1
    fi
    printf '%s\n' "$given"
    return
  fi
  local candidate
  for candidate in \
    "$ROOT/resources/cashflow.xlsx" \
    "$ROOT/../cashflow-audit/resources/Примеры excel/sample_full_model.xlsx" \
    "$ROOT/../cashflow-audit/resources/Примеры excel/sample_3stmt.xlsx"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  echo "no workbook given and none found at resources/cashflow.xlsx (or cashflow-audit samples)" >&2
  exit 1
}

poll_job() {
  local job_id="$1"
  local status_file="$2"
  local deadline=$((SECONDS + JOB_TIMEOUT_SEC))
  local code status
  while true; do
    code="$(http_get "/v1/context-jobs/${job_id}" "$status_file" quiet)"
    if [[ "$code" != "200" && "$code" != "202" ]]; then
      echo "job status request failed with HTTP ${code}" >&2
      return 1
    fi
    status="$(json_field "$status_file" status)"
    case "$status" in
      queued | running)
        if ((SECONDS >= deadline)); then
          echo "client stopped polling job ${job_id} after ${JOB_TIMEOUT_SEC}s (status=${status}); the book's process keeps running until it finishes or the server JOB_TIMEOUT_SEC stops it" >&2
          return 1
        fi
        sleep 1
        ;;
      succeeded | degraded | needs_input | failed)
        printf '%s\n' "$status"
        return 0
        ;;
      *)
        echo "unexpected job status: ${status:-<empty>}" >&2
        return 1
        ;;
    esac
  done
}
