# finance-context-builder

[Русский](README.ru.md) · **English**

An Excel document parser. This read-only service reads a cash-flow workbook (`.xlsx`, `.xlsm`, or legacy `.xls`) and transforms it into JSON and Markdown: `context.json`, `context.md`, `graph.json`, and `graph.md`. Formulas are kept. Numbers come from the Excel cache and are not recalculated.

`context` holds block rows, periods, and values. `graph` holds formula links. A trace of one cell reads that graph.

Guides: [overview](docs/en/overview.md), [layout](docs/en/layout.md), [graph](docs/en/graph.md), [slices](docs/en/slices.md), [architecture](docs/en/architecture.md).

Adapted from [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). See `NOTICE`.

## Input

- `.xlsx` and `.xlsm` are accepted.
- Legacy `.xls` (BIFF8, OLE2) is accepted when LibreOffice is installed: the job converts it once with `soffice --headless --convert-to xlsx` (fresh profile, `XLS_CONVERT_TIMEOUT_SEC`), then copies cached values from the `.xls` with `xlrd` so a LibreOffice recalculation does not replace the Excel cache. The Docker image ships headless LibreOffice, so `soffice` is on `PATH` in the container. A machine without `soffice` on `PATH` or in `SOFFICE_BIN` rejects the upload as `unsupported_media_type`.
- `.xlsb` and encrypted workbooks are rejected. An encrypted `.xlsx` is an OLE2 file too; it is `encrypted_workbook`, not a legacy `.xls`.
- Cached formula values must already be in the file.

## Environment setup

The project needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). On macOS, install
`uv`, provision Python, and create the project virtual environment with:

```bash
brew install uv
uv python install 3.12
uv sync
cp .env.example .env
```

`uv sync` creates `.venv` and installs the versions pinned in `uv.lock`. Commands below use
`uv run`, so activating the virtual environment is not required. To activate it manually, run
`source .venv/bin/activate`. `.env` is gitignored.

## Local run

### CLI

```bash
uv run finance-context build path/to/model.xlsx -o ./out
```

Writes `context.json`, `context.md`, `graph.json`, and `graph.md`. If those documents are already in the output directory and `meta.json` carries the same `publisher` hash, the command prints `reused` and does not run the pipeline. A missing `publisher`, or a stamp with no `stages` map, deletes layout and the published documents, then builds them again; parse and formula IR stay. `meta.stages` names the first changed stage (`compile`, `layout`, `graph`, `publish`), and only that stage and everything after it are deleted. A `publish` change rewrites context and leaves the graph. A `compile` change also deletes `raw/` and formula IR.

### HTTP API

`POST` only enqueues. Redis has to be reachable. `finance-context serve` starts the RQ worker as a child process (`EMBEDDED_WORKER`, default true). The default `REDIS_URL` is `redis://redis:6379/0`. Locally, Redis comes from `deploy/docker-compose.override.yml` (`redis:8.6.7-alpine` on `127.0.0.1:6380`). A process on the host uses that published port:

```bash
REDIS_URL=redis://127.0.0.1:6380/0 uv run finance-context serve --host 127.0.0.1 --port 8080
```

`EMBEDDED_WORKER=false` is API-only. The standalone pool is still `uv run finance-context worker`. With the local Compose Redis, the `finance-context` container uses `redis://redis:6379/0`. Production leaves the override out and sets an external URL (see Docker). Client scripts only call HTTP; they do not start or stop the server.

Check:

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/readyz
```

Submit a workbook. `POST` returns **202** and enqueues the book on the shared RQ queue. The worker horse starts the pipeline child. A repeated POST while that job is queued or started does not enqueue another, unless `meta.json` has a `publisher` stamp and the code on disk no longer matches it. A missing stamp does not cancel the live job. A repeated POST of a workbook whose snapshot was built by this same code returns that snapshot and does not rebuild. A missing or different `publisher` rebuilds from the first stale stage. With no `stages` map that is layout, graph, and context, and parse plus formula IR stay when `ir/compile.json` matches. Startup does not scan jobs. `GET` writes `failed` with `error` `queue_lost` when `meta.json` is still `queued` or `running`, the run lock is free, and the RQ job is missing or failed, canceled, or finished. The file name, content hash, and warnings stay; `publisher` and `stages` are dropped, and submitting the workbook again starts a run whose generation is above the stored one. Poll until `status` is terminal:

| status | Meaning |
| --- | --- |
| `queued` / `running` | Still working |
| `succeeded` | Documents are ready. |
| `failed` | Pipeline error with its code in `error` (`zip_rejected`, `encrypted_workbook`, `unsupported_media_type`, `conversion_failed`, `workbook_too_large`), the book's process was stopped after the server `JOB_TIMEOUT_SEC` (`error` is `TimeoutError`), the worker died (`error` is `process_lost`) or was OOM-killed (`error` is `oom_killed`), or GET found the RQ job gone (`error` is `queue_lost`). Submit the workbook again. |

Older snapshots may still say `needs_input` or `degraded`. Those jobs are finished, and the same four documents are served.

```bash
JOB=$(curl -sS -F "file=@path/to/model.xlsx" http://127.0.0.1:8080/v1/context-jobs)
echo "$JOB"
ID=$(python -c "import json,sys; print(json.loads(sys.argv[1])['job_id'])" "$JOB")

curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/context.json" -o context.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/context.md" -o context.md
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.json" -o graph.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.md" -o graph.md
```

Live progress is a Redis stream, then `GET /v1/context-jobs/$ID/events` (`text/event-stream`). The first SSE event is the current `meta.json`. Later events are `queued`, `running`, stage start and done (`compile`, `layout`, `graph`, `publish`), `succeeded`, `failed`, `queue_lost`, and a shared-cache `retry`. `meta.json` stays the source of truth. A Redis error while publishing does not fail the job. The stream closes after a terminal event. Resume with `Last-Event-ID`.

```bash
curl -N "http://127.0.0.1:8080/v1/context-jobs/$ID/events"
```

Endpoints:

- `POST /v1/context-jobs` — upload workbook
- `GET /v1/context-jobs/{id}` — status (`context_json_url`, `context_md_url`, `graph_json_url`, `graph_md_url`)
- `GET /v1/context-jobs/{id}/events` — SSE. Snapshot from `meta.json`, then the Redis stream. `Last-Event-ID` resumes. A comment line is the heartbeat.
- `GET /v1/context-jobs/{id}/context.json`
- `GET /v1/context-jobs/{id}/context.md`
- `GET /v1/context-jobs/{id}/graph.json`
- `GET /v1/context-jobs/{id}/graph.md`
- `GET /v1/context-jobs/{id}/graph/trace?from=&direction=precedents&depth=8`
- `GET /v1/context-jobs/{id}/graph/trace.md?from=&direction=precedents&depth=8`
- `GET /v1/context-jobs?status=&q=` — session jobs from `meta.json` only (`job_id`, `status`, `stage`, `source_filename`, `content_sha256`, context `schema_version`). `q` is a case-insensitive piece of the file name
- `GET /v1/context-jobs/{id}/summary` — passport of a finished job: workbook counters, graph counters, and warnings (`unresolved`, `external`, `dangling`, cycles). No `links` and no cell cache. It does not replace polling `GET /v1/context-jobs/{id}` while the job is still running
- `GET /v1/context-jobs/{id}/catalog` — rows and axes without numbers. A period also carries `flags` and `group_key`, and `start_date` / `end_date` when the axis has them. Filters: `q` (case-insensitive substring of the row's own label; `label_path` is not searched), `sheet`, `limit`, `offset`
- `GET /v1/context-jobs/{id}/observations` — the only route that returns a cell cache. Requires repeatable `row_key` or `q` (the same own-label substring). Without a selector the response is 400 `selector_required`. `limit` defaults to 24 and stops at 48 (`truncated`). Precedents are not cut by the row limit. Completeness is `precedents_total`. `precedent_depth` defaults to 0 and stops at 3
- `GET /healthz`, `GET /readyz`

The live route schema is generated from these handlers: [Swagger UI](http://127.0.0.1:8080/docs), [ReDoc](http://127.0.0.1:8080/redoc), and `GET /openapi.json`. `context.json`, `graph.json`, and trace use the same models that write those files. Catalog, summary, and observations are response models. They are not files on disk.

HTTP `error` codes:

| code | HTTP |
| --- | --- |
| `unsupported_media_type` | 400 |
| `empty_file` | 400 |
| `file_too_large` | 413 |
| `workbook_too_large` | job `error` only |
| `encrypted_workbook` | 422 |
| `zip_rejected` | 422 |
| `conversion_failed` | job `error` only |
| `selector_required` | 400 |
| `not_found` | 404 |
| `report_not_ready` | 409 |
| `job_still_running` | 409 |
| `too_many_jobs` | 429 |
| `queue_unavailable` | 503 |
| `shared_cache_busy` | job `error` only |

`report_not_ready` is an artifact or trace fetched before the job has written that file. Catalog needs `context.json`. Summary and observations also need `graph.json`. Observations without `row_key` or `q` are `selector_required`, not the whole book. `workbook_too_large` is a finished job: the workbook's formula and range estimate, or its resident memory, is over the per-job budget. `error` is the code and `detail` is the short reason. There is no traceback.

Environment: `DATA_DIR`, `DATA_DIR_ALLOW_NETWORK_FS` (false; startup refuses nfs, cifs/smb, sshfs, afp, and webdav, and logs and continues when the filesystem type cannot be detected), `SESSION_ID`, `SESSION_ID_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9_-]*$`), `SESSION_ID_MAX_LENGTH` (64), `MAX_UPLOAD_BYTES`, `MAX_CONCURRENT_JOBS` (4), `JOB_MEMORY_BUDGET_BYTES` (RSS cap for one job; unset uses the lesser of the cgroup remainder after `JOB_MEMORY_API_RESERVE_FRACTION` and `JOB_MEMORY_DEFAULT_BUDGET_BYTES`, default 1536 MiB; an explicit value is not clipped; the slot count does not divide it), `JOB_MEMORY_API_RESERVE_FRACTION` (0.25), `JOB_MEMORY_DEFAULT_BUDGET_BYTES` (1610612736), `JOB_MEMORY_WATCH_INTERVAL_SEC` (0.2), `RANGE_EXPAND_CAP` (2000; only ranges expanded per formula, that is up to `RANGE_NODE_MIN_CELLS`), `RANGE_NODE_MIN_CELLS` (64; a larger rectangle or a whole column or row is one range node, see docs/en/graph.md; a change rebuilds cached IR), `TRACE_RANGE_MEMBERS` (200; cells of one range node a trace follows, 0 for all), `JOB_OOM_SCORE_ADJ` (1000), `JOB_TIMEOUT_SEC` (server default 3600), `SOFFICE_BIN` (default `soffice` / `libreoffice` on `PATH`), `XLS_CONVERT_TIMEOUT_SEC` (300), `LOG_LEVEL`, `LOG_JSON`. See `.env.example`. A `POST` above `JOB_QUEUE_MAX_PER_SESSION` returns 429 `too_many_jobs` with `Retry-After`. Redis down is 503 `queue_unavailable` with `Retry-After`. A duplicate POST does not wait on the pipeline lock. If the previous id is still in flight after the terminate and kill grace, POST returns 409 `job_still_running` with `Retry-After` and does not enqueue over it. `process_lost` is written only after `JOB_RETRY_ON_LOST` is spent. A `started` job is `queue_lost` when its run lock is free and the horse is not holding `.horse.lock` for that generation. That lock is on the shared data volume, so the API and the worker do not need a shared PID namespace. `GET /healthz` does no I/O. `GET /readyz` checks that `DATA_DIR` is a writable directory and that Redis answers ping, on the API thread pool, and reuses that result for `READYZ_CACHE_TTL_SEC` (default 10). The usual probe opens an unnamed inode (`O_TMPFILE`) and leaves no directory entry. A filesystem that rejects that flag creates a short-lived `.ready-` file, writes one byte, and unlinks it. `queue` is the queue name. `jobs` is this session's inflight count, or 0 when Redis does not answer. A failed ping, an unwritable directory, or a supervised embedded worker that is down is 503. The worker check is not held for `READYZ_CACHE_TTL_SEC`. `API_THREAD_LIMIT` (default 40, AnyIO's default) caps the threads used for blocking API work. Flag, helper, and check rows stay in the block with their `kind`. The worker horse watches each child's resident memory and kills it when it passes the budget. The child sets `oom_score_adj` to `JOB_OOM_SCORE_ADJ` (default 1000) when the kernel allows it. Each `stage_done` line carries `duration_ms`, `rss_in_mb`, `rss_out_mb`, `rss_peak_mb`, and the stage's counts. `LOG_LEVEL=DEBUG` adds `memory_budget` (estimate against the budget, including the streaming switches below) and `memory_refused` when a book is turned away.

Memory switches, all on by default: `PARSE_STREAMING` (iterparse, raw parquet values match the DOM parse), `JSON_STREAM` (`json.dump`, documents byte-identical), `PARQUET_ROW_GROUP_ROWS` (65536; `0` is one row group and byte-identical raw/IR/graph parquet; a smaller group changes bytes only), `COMPILE_STRING_POOL` (compile-local, not `sys.intern`), `COMPILE_AST_ZLIB` (compressed until relations; published JSON unchanged), `GRAPH_STREAM` (column batches, no second cell-edge list), `LAYOUT_COLUMNAR_FACTS` (Arrow indent/outline; `layout_cells_read` stays). `JOB_MALLOC_ARENA_MAX` (2; `0` or blank leaves an inherited value) applies to the spawned job child. `JOB_MALLOC_TRIM` (true) runs after each stage and is a no-op without `malloc_trim`. Set any of them false, or the row-group size to 0, to take that path off. See `.env.example`.

Queue settings, used by the API and by `finance-context worker`: `REDIS_URL` (default `redis://redis:6379/0`, no credentials), `REDIS_USERNAME` (empty; the default user), `REDIS_PASSWORD` (empty; no AUTH), `REDIS_CONNECT_TIMEOUT_SEC` (2; the API client and `worker --check` only), `QUEUE_NAME` (`finance-context`), `JOB_QUEUE_MAX_PER_SESSION` (3), `QUEUE_JOB_TIMEOUT_MARGIN_SEC` (120), `QUEUE_JOB_TTL_SEC` (0 means a queued job can wait), `QUEUE_RESULT_TTL_SEC` (3600), `QUEUE_FAILURE_TTL_SEC` (604800), `JOB_RETRY_ON_LOST` (1), `SHARED_CACHE_BUSY_RETRY_SEC` (30), `SHARED_CACHE_BUSY_MAX_RETRIES` (120), `JOB_ADMISSION_CGROUP_CHECK` (true), `JOB_ADMISSION_RETRY_SEC` (30), `JOB_PARENT_WATCH_INTERVAL_SEC` (1), `JOB_RETRY_AFTER_SEC` (30), `JOB_TERMINATE_GRACE_SEC` (5), `JOB_KILL_GRACE_SEC` (5), `EMBEDDED_WORKER` (true), `EMBEDDED_WORKER_RESTART_BACKOFF_SEC` (1). `finance-context serve` starts a pool of `MAX_CONCURRENT_JOBS` workers with the scheduler as a child process. `finance-context worker` is that same pool when `EMBEDDED_WORKER` is false. A dead child is started again after the backoff, and `/readyz` is not ready while it is down, even inside `READYZ_CACHE_TTL_SEC`. The per-job RSS cap is the lesser of the cgroup limit minus `JOB_MEMORY_API_RESERVE_FRACTION` and `JOB_MEMORY_DEFAULT_BUDGET_BYTES` (1536 MiB). The slot count does not divide that cap. That reserve covers the API process and the embedded worker in the same cgroup. An explicit `JOB_MEMORY_BUDGET_BYTES` is the cap and is not clipped. Leave `REDIS_USERNAME` and `REDIS_PASSWORD` unset to send no AUTH: the client receives the credential-less URL and does not pass `username` or `password`. A non-empty value overrides that one field of the URL userinfo. A username without a password, in the variable or the URL, is rejected. The password is not logged, URL userinfo is omitted from `queue_config`, and `/readyz` does not include it. Startup of the worker logs `queue_config` through the JSON formatter. API startup logs `api_config` with `thread_limit` (`API_THREAD_LIMIT`, default 40) and `readyz_ttl_sec` (`READYZ_CACHE_TTL_SEC`, default 10). See `.env.example`.

## Data

`data/shared/books/{job_id}/` holds `source/source.xlsx`, `raw/`, formula IR under `ir/`, and `layout/layout.json`. Those files are shared across sessions. POST does not delete them. The job child holds `shared/books/{job_id}/.lock` with `LOCK_NB` and drops a stale tail there, then publishes each file with fsync and `os.replace`. `stages.json` is the stage-fingerprint sidecar and is not a published artifact. `.lock` is never removed. When that lock is already held, the horse returns RQ `Retry(interval=SHARED_CACHE_BUSY_RETRY_SEC)` (default 30) and releases the worker slot, at most `SHARED_CACHE_BUSY_MAX_RETRIES` extra times (default 120). `meta.json` stays `queued` during that wait, and GET does not mark the job `queue_lost`. The last attempt fails with `shared_cache_busy`. CLI `build` still waits in-process and records the same error if the next wait would reach `JOB_TIMEOUT_SEC`. See `.env.example`. `data/sessions/{session}/jobs/{job_id}/` holds that session's `context/`, `graph/`, and `meta/meta.json`. `job_id` is the SHA-256 of the string `context:` plus the workbook `content_sha256`, so the directory name is not the file hash. The session defaults to `local`. The id must match `SESSION_ID_PATTERN` and fit `SESSION_ID_MAX_LENGTH`; startup rejects a separator or `..`. GET looks up a job only inside that session. A network filesystem under `DATA_DIR` refuses startup unless `DATA_DIR_ALLOW_NETWORK_FS=true`.

Container memory is `mem_limit`. One job is stopped at 1536 MiB RSS, the headroom over the known large book, not its usual use. The slot count does not divide that cap. The default is 4 concurrent books. There is no workbook size estimate. The `JOB_MEMORY_*` names stay in `.env.example`. At most `MAX_CONCURRENT_JOBS` workbooks run at once, in the worker child that `finance-context serve` starts. One container is that one process plus Redis. They share one `DATA_DIR` and one queue. `EMBEDDED_WORKER=false` keeps the API without the child.

## Docker

```bash
cp .env.example .env   # set REDIS_URL; see below
mkdir -p data && sudo chown 1000:1000 data
# Production. Redis comes from outside. The override file is not used.
docker compose -f deploy/docker-compose.yml up --build
```

Leave Compose running. Run commands from the repo root unless noted. `deploy/docker-compose.yml` starts one container, `finance-context`. Production leaves `deploy/docker-compose.override.yml` out. Redis then comes from outside, for example the user's finance-stack compose. Set `REDIS_URL` in `.env`, for example `redis://host.docker.internal:6379/0` (`host.docker.internal` is mapped in the compose file). On Linux that name is the host bridge address. Redis has to listen on that bridge address, or on another non-public address the containers can reach. A listener bound only to `127.0.0.1` is not reachable from these containers.

Local testing adds Redis from the override (`redis:8.6.7-alpine` on `127.0.0.1:6380`, append-only, named volume). The project is that one `finance-context` container plus Redis:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.override.yml up --build
```

`cd deploy && docker compose up` merges that override on its own. The override sets `REDIS_URL=redis://redis:6379/0` on the container, so a host `.env` that points at `localhost` stays on the host. A process on the host, such as `scripts/run.sh` against the API, uses `redis://127.0.0.1:6380/0`. That loopback address is the host's published port. It is not reachable from the container.

Empty `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` keep DuckDB on `DATA_DIR`. When all four are set, DuckDB still writes `DATA_DIR`, then copies the same bytes to `books/{job_id}/` in the bucket (`raw/cells.parquet`, `context.json`, `context.md`, and the other book files). The local override starts MinIO and sets those four variables. Its server image is `alpine/minio:RELEASE.2025-10-15T17-29-55Z`, because `minio/minio` is no longer on Docker Hub. Production compose does not start MinIO and does not set them. Host pytest stays on `DATA_DIR` unless its own environment sets the four.

The API is published only on `127.0.0.1:8080`. The shared book cache and session publications go to `./data` on the host. Create that directory and give it to uid 1000 before `up` (the commands above). The container runs as that user, and on a fresh Linux checkout Docker would otherwise create a root-owned bind source. Compose mounts **`./data` only**, not `src/`. The service is `mem_limit: 4g`. `DATA_DIR` has to be a local filesystem on one node. Startup refuses a network filesystem unless `DATA_DIR_ALLOW_NETWORK_FS=true`.

The image runs as `app` (uid 1000) with `HOME=/app` and includes `libreoffice-calc-nogui`. A `.xls` upload converts inside the container. Conversion writes a temporary profile under the book directory, not into the image home. `XLS_CONVERT_TIMEOUT_SEC` (default 300) stays below `JOB_TIMEOUT_SEC` (default 3600) so a hung conversion fails the job before the server stops the process. `.env` can still set `SOFFICE_BIN` and `XLS_CONVERT_TIMEOUT_SEC`; Compose does not override them. `scripts/docker-xls-smoke.sh` checks that path against a real `.xls`. It needs Docker and is not part of `uv run pytest`.

API container without Compose. The image command is `finance-context serve`:

```bash
docker build -f deploy/Dockerfile -t finance-context-builder .
docker run --rm -v "$PWD/data:/app/data" --env-file .env -p 127.0.0.1:8080:8080 \
  --add-host=host.docker.internal:host-gateway finance-context-builder
```

One-shot CLI build, overriding that command:

```bash
docker run --rm -v "$PWD/data:/app/data" --env-file .env \
  --add-host=host.docker.internal:host-gateway \
  finance-context-builder \
  finance-context build /app/data/model.xlsx -o /app/data/out
```

## Tests

```bash
uv run pytest
uv run ruff check src tests
```

With the HTTP server **already running** in another terminal, `scripts/run.sh` calls `check-service.sh` then `run-context-job.sh` and writes the run under `out/<timestamp>/`:

```text
json/context.json  json/graph.json  json/trace.json
md/context.md      md/graph.md      md/trace.md
healthz.json  readyz.json  post-job.json  job-status.json  summary.txt
```

The client checks the four document URLs, that `context.json` has no second row catalog and no AST, that `graph.json` is schema `1.7` and `context.json` is schema `1.14` with `links` (a range stays one ref), and that the Markdown twins repeat the same blocks and links, then smokes `GET .../graph/trace` and `.../graph/trace.md`. The script exiting with `OK` means the client finished; the server should still be listening on 8080. A client timeout does not stop the book's process.

```bash
bash scripts/run.sh path/to/model.xlsx
```

If no path is given, it looks for `resources/cashflow.xlsx`. Override `BASE_URL` (default `http://127.0.0.1:8080`), `OUT_DIR` (default `./out`), and `JOB_TIMEOUT_SEC` (client poll, default `300` when unset). The server's `JOB_TIMEOUT_SEC` in `.env` is a different clock: it stops the process. Export the same value before `run.sh` if the client should wait that long.
