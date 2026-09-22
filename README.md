# finance-context-builder

Read-only service that turns Excel cash-flow workbooks (`.xlsx` / `.xlsm`) into versioned JSON and Markdown context. Formulas are preserved; values come from Excel cached results and are not recalculated.

Every layout row is kept once inside its block in `context.json` (schema `1.11.0`), including assumption tables without a period axis (`params`) and left-of-timeline scalars with roles (`value` / `unit` / `total`). A row carries flat `values`, aligned `value_statuses` and `normalized_values`, plus `scale_factor`, `period_position`, and `aggregation`. `context.md` repeats the same blocks, rows, `row_key`, time profile, and values; an empty cell is `empty` and a period outside the line's phase is `n/a`. Period headers include the period key and column letter. Cells, formula AST, and the dependency graph stay in `raw/` and `ir/` — they are not merged into one JSON or one Markdown file. The accepted `concept_id` is the reporting slot; `semantic_identity`, `reporting_roles`, and `cash_semantics` keep economic meaning, layout role, and accrual versus cash apart. Sheet axes are published once on `axes` (grain, periods, `group_key`, and construction/operation phases from the flag rows on that axis). A timeline block stores `axis_ids` and one value series per axis; phase is not copied onto the block. A repeated year banner over months is `group_key`, not a second axis. Side-by-side year and month columns that share a header band stay one table. `mapping_stats.concept_coverage` is the share of annotatable rows with an accepted concept. `mapping_stats.mapping_quality` scores label, semantic, unit, temporal, and formula checks; a coverage of 1.0 does not mean those checks passed. An empty `unmapped.json` is not “every block row has a concept”. A small taxonomy may annotate a line with `concept_id`, or abstain: a wrong tag is worse than `unknown`. Structure (formula graph and neighbors) first, then labels, then embeddings, then an optional LLM rerank. Unknown rows still carry hints, neighbors, one formula, role-tagged cells, and top-3 candidates. `graph.json` and `graph.md` (schema `1.7.0`) publish summary counts and formula-level links with `formula_class`, `row_key`, and `period_id`. The Class column in `graph.md` repeats `formula_class`. AST and the expanded cell graph stay in `ir/*.parquet` — [graph](docs/graph.md).

Guides: [overview](docs/overview.md), [layout](docs/layout.md), [mapping](docs/mapping.md), [taxonomy](docs/taxonomy.md), [graph](docs/graph.md), [unmapped review](docs/review.md), [architecture](docs/architecture.md).

## Limits (MVP)

- `.xls`, `.xlsb`, and encrypted workbooks are rejected
- Cached formula values must already be in the file
- LLM/embeddings are optional: mapping falls back to structure + labels, then `unknown`
- The HTTP worker is in-process. Multi-replica deploys need an external queue.

Adapted from [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). See `NOTICE`.

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

Writes `context.json`, `context.md`, `graph.json`, and `graph.md`.

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

Submit a workbook. `POST` returns **202** and starts mapping. Repeated submission of the same workbook rebuilds compile, layout, mapping, and context (parse artifacts are reused), calling embeddings/LLM for rows unresolved by structure and labels. Poll until `status` is terminal:

| status | Meaning |
| --- | --- |
| `queued` / `running` | Still working |
| `succeeded` | Mapped without open questions |
| `needs_input` | Context is ready; some fact rows were left `unknown` (review questions) |
| `degraded` | Same as `needs_input`, but LLM/embeddings were not configured |
| `failed` | Pipeline error, or the worker exceeded `JOB_TIMEOUT_SEC` (`error` is `TimeoutError`); no usable context. Submit the workbook again. |

`needs_input` is not a crash. `context.json`, `context.md`, `graph.json`, and `graph.md` are still served.

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

`report_not_ready` is an artifact or trace fetched before the job has written that file.

Environment: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (process default `qwen3.6-27b-fp8` when unset), `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `DATA_DIR`, `JOB_TIMEOUT_SEC` (default 3600). See `.env.example`.

Learned high-confidence mappings persist in `$DATA_DIR/glossary.json` and are reused on later jobs. Taxonomy lives in `src/finance_context/ontology/taxonomy.yaml`. How to add a concept versus an alias, and how the cascade uses those fields: [docs/taxonomy.md](docs/taxonomy.md) and [docs/mapping.md](docs/mapping.md). Check/helper/flag rows are excluded from review questions; they stay in the block with `disposition=excluded`. Unmapped business rows stay `unknown` with candidates instead of taking a nearest guess.

## Docker

```bash
cp .env.example .env   # optional; LLM/embeddings may stay unset
docker compose up --build
```

Leave Compose running. API: `http://127.0.0.1:8080`. Job artifacts and `glossary.json` go to `./data` on the host. Compose mounts **`./data` only**, not `src/`: taxonomy and mapping code are whatever was baked into the image — rebuild after ontology changes.

Loopback LLM URLs in `.env` (`http://127.0.0.1:1234/v1`) are rewritten to `host.docker.internal` inside the container so LM Studio on the host stays reachable. Keep the model server listening on all interfaces or on the host gateway, not only inside another isolated network.

API container without Compose. The image command is `finance-context serve`:

```bash
docker build -t finance-context-builder .
docker run --rm -v "$PWD/data:/app/data" --env-file .env -p 8080:8080 \
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

The client checks the four document URLs, that `context.json` has no second row catalog and no AST, that `graph.json` is schema `1.5` with `links` (a range stays one ref), and that the Markdown twins repeat the same blocks and links, then smokes `GET .../graph/trace` and `.../graph/trace.md`. The script exiting with `OK` means the client finished; the server should still be listening on 8080.

```bash
bash scripts/run.sh path/to/model.xlsx
```

If no path is given, it looks for `resources/cashflow.xlsx`. Override `BASE_URL` (default `http://127.0.0.1:8080`), `OUT_DIR` (default `./out`), and `JOB_TIMEOUT_SEC` (default `300`).
