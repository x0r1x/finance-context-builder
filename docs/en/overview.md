# Project overview

[Русский](../ru/overview.md) · **English**

**finance-context-builder** is a read-only service that turns Excel cash-flow models (`.xlsx` / `.xlsm`, legacy `.xls` through LibreOffice) into versioned JSON and Markdown.

Formulas are not recalculated: artifacts keep Excel's cached values. Content is complete: every layout row lives once inside its block. JSON and Markdown are the same facts. Cells, AST, and edges are not merged into one document: they stay in `raw/` and `ir/`, while the public `context` and `graph` describe the report row and formula links.

How to run, Docker, and the API: [README](../../README.md). The layer snapshot is [architecture.md](architecture.md).

| Topic | Document |
| --- | --- |
| Blocks, period axes, row labels | [layout.md](layout.md) |
| Formula graph, cycles, trace | [graph.md](graph.md) |
| Catalog, summary, and observations | [slices.md](slices.md) |

## Input and output

The input is a workbook whose formulas are already calculated. `.xlsb` and encrypted files are rejected. A legacy `.xls` book is accepted when LibreOffice can convert it.

A job writes two trees. `raw/`, formula IR (`cells`, `edges`, `cell_edges`, `compile.json`), and `layout.json` live in `shared/books/{job_id}/` and are the same for every session. Context, graph, `meta.json`, `ir/graph_edges.parquet`, and `ir/graph_index.parquet` live in `sessions/{session}/jobs/{job_id}/`. `job_id` is the SHA-256 of the string `context:` plus the workbook `content_sha256`, so the directory name is not the file hash.

| Artifact | Contents |
| --- | --- |
| `raw/` | Cells, formulas, cache, formats, `cell_presence.parquet` (`populated` / `styled_blank`) |
| `ir/` | Templates, AST, `edges.parquet` (as written in the formula), `cell_edges.parquet` (formula expansion, lag empty), `graph_edges.parquet` (`col_offset` / `period_lag`), and `compile.json` (IR schema stamp) |
| `layout.json` | Report blocks, period axes, row kinds |
| `graph.json` / `graph.md` | Schema `1.7.0`: cell-level parquet counts, `iterate`, cycles (`breakers`) / `circularity_hints`, `dangling_classes`, and `links[]` (cell, A1 formula, `formula_class`, `row_key`, `period_id`, inputs; a range is not expanded). Markdown links have a Class column. AST and cell-edges stay in `ir/` |
| `context.json` / `context.md` | Schema `1.14.0`: passport, `axes` (the only place for flags and `group_key`), every block and every row (`row_key`, one formula, cached series per `axis_ids`, `value_statuses`, `scale_factor`, `normalized_values`). A year repeated over months is `group_key`, not a second axis. A year and a month in one header band are one table. The same header repeated in the next section points at the same axis. Markdown `## Axes` prints each axis once, periods as columns: the header is the axis id and the period keys (`\| PF Model!r9 \| 2021 \| 2022 \| 2032 \|`), and under it `Start` / `End` (ISO dates when the book has a Start/End ruler), `Group` / `Calendar` / `Flags`. A block table adds `Path` (`label_path`) and `Cells` (`L total: -86400`, `G Start: 01.01.2024`). A role cell has `header`. A `total` / `stub` column is not part of the axis. Empty attribute rows are hidden: a year with no dates, group, or flags is the header alone. A regular month grid collapses to one column per year (`\| Output!r6 \| 2020 \| 2021 \|` and a row `\| Periods \| 2020-01 .. 2020-12 \| ... \|`). JSON omits empty `flags` and a `calendar_year` that repeats the period key. The block table has `Axes: ...` and one grid per series. An empty cell is `empty`, a non-applicable cell is `n/a`, thousands render as `k£ ×1000` and `1.5 (1500)`. A period header is `period_key` plus the column letter (`Y23 (AA)`). A period value shows the cache and `[Sheet!A1]`. Under the block, `relations` use the same `row_key`. There is no second row catalog and no cell registry. The header line is content completeness. `graph` is a pointer |

Catalog, summary, and observations are HTTP projections, not files. Schemas: `catalog-2`, `summary-2`, `observation-2`. Details: [slices.md](slices.md).

Formula IR and layout for the same workbook are shared: `$DATA_DIR/shared/books/{job_id}/`. A POST does not delete that directory. The job child invalidates a stale `layout.json`, `raw/`, or formula IR under `shared/books/{job_id}/.lock`, and publishes each file by writing a temp file, fsyncing it, `os.replace`, and fsyncing the directory.

## Pipeline

`parse → compile → layout → graph → build → render`.

The passport, the catalog, and observations are not in that sequence. They are read from `context.json` and `graph.json` that the job has already written.

POST enqueues the book on the shared RQ queue. `finance-context serve` starts the worker as a child; the horse spawns the pipeline child, and the API then reads `meta.json`. CLI `build` stays one process. PyArrow writes parquet: it is the compile cache and the trace input after the child exits. Inside one run, stages pass the row lists they already built. A repeat POST of a finished book returns the snapshot when `meta.json` `publisher` matches the code on disk. A different `publisher`, while the job is queued or started, cancels that job and enqueues another; a missing stamp does not. If that stop does not clear the id within `JOB_TERMINATE_GRACE_SEC` and `JOB_KILL_GRACE_SEC`, POST returns 409 `job_still_running` with `Retry-After` and does not enqueue a replacement. POST records `rebuild_from` on the session `meta.json`, keeps the stage map, and does not delete `shared/books/{job_id}`. The child takes `shared/books/{job_id}/.lock` with `LOCK_EX|LOCK_NB`. A held lock raises `SharedCacheBusy`. The horse returns `Retry(interval=SHARED_CACHE_BUSY_RETRY_SEC)` and frees the worker slot, at most `SHARED_CACHE_BUSY_MAX_RETRIES` extra times. `meta.json` stays `queued` while that job is scheduled, and GET does not mark it `queue_lost`. After the last retry the job is `failed` with `error` `shared_cache_busy`. CLI `build` still waits in-process and records the same error if the next wait would reach `JOB_TIMEOUT_SEC`. Under the lock the child compares `stages.json` and deletes a stale `layout.json`, `raw/`, and formula IR. Each shared artifact is published through a temp file, fsync, `os.replace`, and a directory fsync. `.lock` is never removed. Startup does not scan jobs. `GET /v1/context-jobs/{id}` reads `meta.json`. When that status is `queued` or `running`, the run lock is free, and the RQ job is missing, failed, canceled, finished, or still `started` when the horse is not holding `.horse.lock` for that generation, GET writes `failed` with `error` `queue_lost`. A `scheduled` or `deferred` retry is still in flight. An unreadable `meta.json` while that job is in flight is the live run, not an HTTP 500. `source_filename`, the content hash, and warnings stay; `publisher` and `stages` are removed so the next POST rebuilds from layout. Redis being down does not mark the job lost. Another session is not changed. That POST enqueues a generation above the one stored in `meta.json`. `meta.json` is the status. `scripts/run.sh` only polls HTTP. Its `JOB_TIMEOUT_SEC` defaults to 300 seconds and does not stop the process; the server stops the process at its own `JOB_TIMEOUT_SEC` (3600 in `.env.example`). `finance-context serve` starts that RQ pool as a child (`EMBEDDED_WORKER`, default true, restart backoff `EMBEDDED_WORKER_RESTART_BACKOFF_SEC`). `finance-context worker` is the same pool when the embedded worker is off. Queue settings (`REDIS_URL`, `REDIS_USERNAME`, `REDIS_PASSWORD`, `QUEUE_NAME`, `JOB_QUEUE_MAX_PER_SESSION`, the `QUEUE_*` TTLs and margin, `JOB_RETRY_ON_LOST`, `SHARED_CACHE_BUSY_*`, `JOB_ADMISSION_*`, `JOB_PARENT_WATCH_INTERVAL_SEC`, `JOB_RETRY_AFTER_SEC`, `JOB_TERMINATE_GRACE_SEC`, `JOB_KILL_GRACE_SEC`, `API_THREAD_LIMIT`, `READYZ_CACHE_TTL_SEC`, `EMBEDDED_WORKER`, `EMBEDDED_WORKER_RESTART_BACKOFF_SEC`) live in `.env.example`. `REDIS_URL` is credential-less unless it already has userinfo. Empty `REDIS_USERNAME` and `REDIS_PASSWORD` send no AUTH. A non-empty value overrides that field of the URL. A username without a password is rejected. The password is not logged. `/readyz` caches the disk and Redis ping for `READYZ_CACHE_TTL_SEC` and still reports not ready at once when the embedded worker is down. Job progress is also a Redis stream (`JOB_EVENTS_MAXLEN`, `JOB_EVENTS_TTL_SEC`, `JOB_EVENTS_HEARTBEAT_SEC`, `JOB_EVENTS_MAX_CONNECTIONS`). `GET /v1/context-jobs/{id}/events` sends the `meta.json` snapshot, then new events.

If the detector built neither a timeline nor params (prose / navigation) or found no article labels, there are no fact rows: the status can be `succeeded` with an empty context. That is layout. Details: [layout.md](layout.md).

Section headers (`abstract`) and counters (`index`) are **not dropped**: they are rows of their block. Values along an axis are an array on a `fact` / `flag` / `helper` row; params rows have column roles and values only in value columns. A row has one formula. Cell-level adjacency and AST live in IR / [graph.md](graph.md). Downstream sees the label, `label_path`, neighbors from formula row refs (a large range read by the row is one id in `neighbor_ranges`, described once in the top-level `ranges` block with its bounds, cell count and reading rows; rows inside it get no back-reference), the formula, hints, and the cache.

Markdown reports **content completeness** as `1.00 (rows/rows layout rows)`: every layout row of a published block is in the document.

## Job statuses

| Status | Meaning |
| --- | --- |
| `queued` / `running` | Still working |
| `succeeded` | Documents are ready |
| `failed` | Pipeline error, the book's process was stopped by the server `JOB_TIMEOUT_SEC` (`error` = `TimeoutError`), the worker died (`error` = `process_lost`) or was OOM-killed (`error` = `oom_killed`), or GET found the RQ job gone (`error` = `queue_lost`). No usable context |

Older snapshots may still say `needs_input` or `degraded`. Those jobs are finished, and the same four documents are served.

## What a row records

Hints come from the label and the unit cell: `unit` (`money` / `count` / `rate` / `ratio` / `years` / `price` / `date`), `unit_per`, `currency` (GBP/EUR/USD/RUB), and `scale` (`unit` / `k` / `m` / `bn`). `k£` in a label or Units cell is money, GBP, and `k`. `тыс. руб.` / `млн ₽` / `млрд руб.` is money, RUB, and `k` / `m` / `bn`; `руб.` alone is `unit`. `RURm` / `mRUB` / `RUB mn` is money, RUB, and `m`; `RUR` alone is RUB at `unit`; `USD bn`, `EURk`, and `RUB'000` carry `bn` / `k`. A unit caption in the period-header label applies to the block. A unit row inside a section applies until the next section. A unit cell on the row itself wins. A currency known only from a number format (`[$₽-419]`) leaves `scale` empty; `[$-419]` names no currency.

Formula-shape relations on the block are `alias`, `aggregate`, `difference`, and `roll_forward`. They are not a second catalog of the row. `roll_forward` is a reference to the previous column on the same sheet; the previous column of another sheet is an `alias` (sheets often shift the period grid by one column).
