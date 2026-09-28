# finance-context-builder

[Русский](README.ru.md) · **English**

An Excel document parser. This read-only service reads a cash-flow workbook (`.xlsx` or `.xlsm`) and transforms it into JSON and Markdown: `context.json`, `context.md`, `graph.json`, and `graph.md`. Formulas are kept. Numbers come from the Excel cache and are not recalculated.

`context` holds block rows, periods, and values. `graph` holds formula links. A trace of one cell reads that graph. A row's concept comes from the cascade: structure, labels, embeddings, then an optional model call. A row without a concept stays `abstained`.

Guides: [overview](docs/en/overview.md), [layout](docs/en/layout.md), [mapping](docs/en/mapping.md), [taxonomy](docs/en/taxonomy.md), [graph](docs/en/graph.md), [LLM slice](docs/en/llm.md), [unmapped review](docs/en/review.md), [architecture](docs/en/architecture.md).

Adapted from [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). See `NOTICE`.

## Input

- `.xlsx` and `.xlsm` are accepted.
- `.xls`, `.xlsb`, and encrypted workbooks are rejected.
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
`source .venv/bin/activate`.

LLM and embeddings are optional: without them mapping uses structure + labels, then `unknown`.
Fill `LLM_API_KEY` / `EMBEDDING_API_KEY` (LM Studio token) and model ids if you use a local OpenAI-compatible server. Default URLs are `http://127.0.0.1:1234/v1`. `.env` is gitignored.

## Local run

### CLI

```bash
uv run finance-context build path/to/model.xlsx -o ./out
```

Writes `context.json`, `context.md`, `graph.json`, and `graph.md`. If those documents are already in the output directory and `meta.json` carries the same `publisher` hash, the command prints `reused` and does not run the pipeline. A missing `publisher`, or a stamp with no `stages` map, deletes layout, mapping, and the published documents, then builds them again; parse and formula IR stay. `meta.stages` names the first changed stage (`compile`, `layout`, `mapping`, `graph`, `publish`), and only that stage and everything after it are deleted. A `publish` change rewrites context and leaves mapping and graph. A `compile` change also deletes `raw/` and formula IR.

To extract rows that the mapping stage left without a concept, run:

```bash
uv run python scripts/extract-unmapped.py data/<job-id>/mapping.json
```

The script also accepts a generated `context.json` and reads `disposition=abstained` from `blocks[].rows`. By default it writes `unmapped.json`
next to the input file. Use `-o path/to/file.json` to choose another location. The output has the form
`{"count": <number>, "rows": [<unmapped attributes without period values>]}`.
Excluded rows and time-series `values` are omitted.

### HTTP API

Keep this process running in its own terminal. Client scripts only call HTTP; they do not start or stop the server.

```bash
uv run finance-context serve --host 127.0.0.1 --port 8080
```

Check:

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/readyz
```

Submit a workbook. `POST` returns **202** and the API starts a process for that book. A repeated POST while the process is alive does not start another, unless `meta.json` has a `publisher` stamp and the code on disk no longer matches it. A missing stamp does not stop the live process. A repeated POST of a workbook whose snapshot was built by this same code returns that snapshot and does not rebuild. A missing or different `publisher` rebuilds from the first stale stage. With no `stages` map that is layout, mapping, and context, and parse plus formula IR stay when `ir/compile.json` matches. Embeddings and LLM run again only when mapping itself is rebuilt, and only for rows that structure and labels did not resolve. After an API restart, a job still `queued` or `running` becomes `failed` with `error` `process_lost`. `GET` does the same when that process is already gone. Poll until `status` is terminal:

| status | Meaning |
| --- | --- |
| `queued` / `running` | Still working |
| `succeeded` | Documents are ready. A fact without a concept stays `abstained` on the row. |
| `failed` | Pipeline error, the book's process was stopped after the server `JOB_TIMEOUT_SEC` (`error` is `TimeoutError`), or the API restarted while the job was still `queued` or `running` (`error` is `process_lost`). Submit the workbook again. |

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

Endpoints:

- `POST /v1/context-jobs` — upload workbook
- `GET /v1/context-jobs/{id}` — status (`context_json_url`, `context_md_url`, `graph_json_url`, `graph_md_url`)
- `GET /v1/context-jobs/{id}/context.json`
- `GET /v1/context-jobs/{id}/context.md`
- `GET /v1/context-jobs/{id}/graph.json`
- `GET /v1/context-jobs/{id}/graph.md`
- `GET /v1/context-jobs/{id}/graph/trace?from=&direction=precedents&depth=8`
- `GET /v1/context-jobs/{id}/graph/trace.md?from=&direction=precedents&depth=8`
- `GET /healthz`, `GET /readyz`

The live route schema is generated from these handlers: [Swagger UI](http://127.0.0.1:8080/docs), [ReDoc](http://127.0.0.1:8080/redoc), and `GET /openapi.json`. `context.json`, `graph.json`, and trace use the same models that write those files.

HTTP `error` codes:

| code | HTTP |
| --- | --- |
| `unsupported_media_type` | 400 |
| `empty_file` | 400 |
| `file_too_large` | 413 |
| `encrypted_workbook` | 422 |
| `zip_rejected` | 422 |
| `not_found` | 404 |
| `report_not_ready` | 409 |
| `too_many_jobs` | 429 |

`report_not_ready` is an artifact or trace fetched before the job has written that file.

Environment: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (process default `qwen3.6-27b-fp8` when unset), `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE` (32), `EMBEDDING_CONCURRENCY` (1), `LLM_CONCURRENCY` (1, per book), `MAX_CONCURRENT_JOBS` (2), `DATA_DIR`, `JOB_TIMEOUT_SEC` (server default 3600). See `.env.example`. A `POST` above `MAX_CONCURRENT_JOBS` returns 429 `too_many_jobs`. `GET /readyz` reports `queue: in_process` and `jobs`, the number of live child processes. If `DATA_DIR` cannot be created or written, the response is 503.

Learned high-confidence mappings persist in `$DATA_DIR/sessions/{session}/glossary.json` and are reused on later jobs in that session only. Another session does not read them. Taxonomy lives in `src/finance_context/ontology/taxonomy.yaml`. How to add a concept versus an alias, and how the cascade uses those fields: [docs/en/taxonomy.md](docs/en/taxonomy.md) and [docs/en/mapping.md](docs/en/mapping.md). Check/helper/flag rows stay in the block with `disposition=excluded`. Unmapped business rows stay `abstained` with candidates instead of taking a nearest guess.

## Data

`data/shared/books/{sha256}/` holds the workbook, raw extract, formula IR, and layout. Those files are shared across sessions. `data/sessions/{session}/jobs/{sha256}/` holds mapping, context, graph, and that session's `glossary.json`. The session defaults to `local`.

At most `MAX_CONCURRENT_JOBS` workbooks run at once, default 2. `finance-context serve` starts one worker. Several API replicas on one data directory are not supported.

## Docker

```bash
cp .env.example .env   # optional; LLM/embeddings may stay unset
docker compose up --build
```

Leave Compose running. The API is published only on `127.0.0.1:8080`. Shared book cache and per-session glossary, mapping, and publications go to `./data` on the host. Compose mounts **`./data` only**, not `src/`: taxonomy and mapping code are whatever was baked into the image — rebuild after ontology changes.

Loopback LLM URLs in `.env` (`http://127.0.0.1:1234/v1`) are rewritten to `host.docker.internal` inside the container so LM Studio on the host stays reachable. Keep the model server listening on all interfaces or on the host gateway, not only inside another isolated network.

API container without Compose. The image command is `finance-context serve`:

```bash
docker build -t finance-context-builder .
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
healthz.json  readyz.json  post-job.json  job-status.json  summary.txt  unmapped.json
```

The client checks the four document URLs, that `context.json` has no second row catalog and no AST, that `graph.json` is schema `1.7` and `context.json` is schema `1.13` with `links` (a range stays one ref), and that the Markdown twins repeat the same blocks and links, then smokes `GET .../graph/trace` and `.../graph/trace.md`. The script exiting with `OK` means the client finished; the server should still be listening on 8080. A client timeout does not stop the book's process.

```bash
bash scripts/run.sh path/to/model.xlsx
```

If no path is given, it looks for `resources/cashflow.xlsx`. Override `BASE_URL` (default `http://127.0.0.1:8080`), `OUT_DIR` (default `./out`), and `JOB_TIMEOUT_SEC` (client poll, default `300` when unset). The server's `JOB_TIMEOUT_SEC` in `.env` is a different clock: it stops the process. Export the same value before `run.sh` if the client should wait that long.
