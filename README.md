# finance-context-builder

Read-only service that turns Excel cash-flow workbooks (`.xlsx` / `.xlsm`) into versioned JSON and Markdown context. Formulas are preserved; values come from Excel cached results and are not recalculated.

Every layout row is kept in `context.json` (`inventory`, schema `1.7.0`), including assumption tables without a period axis (`params`) and left-of-timeline scalars with roles (`value` / `unit` / `total`). The accepted `concept_id` is the reporting slot; `semantic_identity`, `reporting_roles`, and `cash_semantics` keep economic meaning, layout role, and accrual versus cash apart. A workbook-level `timeline` annotates model years with construction/operation phases from timing flags. Coverage lives in `mapping_stats`; empty `unmapped` is not “every inventory row has a concept”. A small taxonomy may annotate a line with `concept_id`, or abstain: a wrong tag is worse than `unknown`. Structure (formula graph and neighbors) first, then labels, then embeddings, then an optional LLM rerank. Unknown rows still carry hints, neighbors, formula fingerprint, role-tagged cells, and top-3 candidates. Calculated period cells include A1 `formula` text; AST and the cell-level graph stay in sidecars (`graph.json`, `graph-edges.json`, `formulas.json`, `ir/*.parquet`) — [graph](docs/graph.md).

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

Writes `context.json`, `graph.json`, `graph-edges.json`, `graph-dangling.json`, `formulas.json`, and `context.md`.

To extract rows that the mapping stage left without a concept, run:

```bash
uv run python scripts/extract-unmapped.py data/<job-id>/mapping.json
```

The script also accepts a generated `context.json`. By default it writes `unmapped.json`
next to the input file. Use `-o path/to/file.json` to choose another location. The output has the form
`{"count": <number>, "rows": [<unmapped attributes without period values>]}`.
Excluded rows and time-series `values` are omitted so the extract matches the unmapped rows shown in `context.md`.

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
| `failed` | Pipeline error; no usable context |

`needs_input` is not a crash. `context.json`, graph sidecars, and `context.md` are still served.

```bash
JOB=$(curl -sS -F "file=@path/to/model.xlsx" http://127.0.0.1:8080/v1/context-jobs)
echo "$JOB"
ID=$(python -c "import json,sys; print(json.loads(sys.argv[1])['job_id'])" "$JOB")

curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/context.json" -o context.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.json" -o graph.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph/edges" -o graph-edges.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph-dangling.json" -o graph-dangling.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/formulas.json" -o formulas.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/context.md" -o context.md
```

Endpoints:

- `POST /v1/context-jobs` — upload workbook
- `GET /v1/context-jobs/{id}` — status
- `GET /v1/context-jobs/{id}/context.json`
- `GET /v1/context-jobs/{id}/graph.json`
- `GET /v1/context-jobs/{id}/graph/edges`
- `GET /v1/context-jobs/{id}/graph-dangling.json`
- `GET /v1/context-jobs/{id}/formulas.json`
- `GET /v1/context-jobs/{id}/graph/trace?from=&direction=precedents&depth=8`
- `GET /v1/context-jobs/{id}/context.md`
- `GET /healthz`, `GET /readyz`

Environment: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `DATA_DIR`. See `.env.example`.

Learned high-confidence mappings persist in `$DATA_DIR/glossary.json` and are reused on later jobs. Taxonomy lives in `src/finance_context/ontology/taxonomy.yaml`. How to add a concept versus an alias, and how the cascade uses those fields: [docs/taxonomy.md](docs/taxonomy.md) and [docs/mapping.md](docs/mapping.md). Check/helper/flag rows are excluded from review questions; they still appear in Markdown (`## Excluded` and row navigator). Unmapped business rows stay `unknown` with candidates instead of taking a nearest guess.

## Docker

```bash
cp .env.example .env   # optional; LLM/embeddings may stay unset
docker compose up --build
```

Leave Compose running. API: `http://127.0.0.1:8080`. Job artifacts and `glossary.json` go to `./data` on the host. Compose mounts **`./data` only**, not `src/`: taxonomy and mapping code are whatever was baked into the image — rebuild after ontology changes.

Loopback LLM URLs in `.env` (`http://127.0.0.1:1234/v1`) are rewritten to `host.docker.internal` inside the container so LM Studio on the host stays reachable. Keep the model server listening on all interfaces or on the host gateway, not only inside another isolated network.

CLI one-off without Compose:

```bash
docker build -t finance-context-builder .
docker run --rm -v "$PWD/data:/app/data" --env-file .env -p 8080:8080 \
  --add-host=host.docker.internal:host-gateway finance-context-builder
```

## Tests

```bash
uv run pytest
uv run ruff check src tests
```

With the HTTP server **already running** in another terminal, `scripts/run.sh` calls `check-service.sh` then `run-context-job.sh` and writes HTTP bodies under `out/<timestamp>/` (`healthz.json`, `readyz.json`, `post-job.json`, `job-status.json`, `context.json`, `graph.json`, `graph-edges.json`, `graph-dangling.json`, `formulas.json`, `graph-trace.json`, `context.md`) plus extracted `unmapped.json`. The client checks job URLs, that `context.json` does not embed AST or row-graph fields (A1 `formula` on values is allowed), that `graph.json` is schema `1.4.x` stats-only with `iterate`, `artifacts.edges_json` / `dangling` / `formulas`, and that the downloaded sidecars are well-formed (`graph-edges.json` carries `formula_cell`, `precedent`, `formula`, and `resolution_status`), then smokes `GET .../graph/trace`. The script exiting with `OK` means the client finished; the server should still be listening on 8080.

```bash
bash scripts/run.sh path/to/model.xlsx
```

If no path is given, it looks for `resources/cashflow.xlsx`. Override `BASE_URL` (default `http://127.0.0.1:8080`), `OUT_DIR` (default `./out`), and `JOB_TIMEOUT_SEC` (default `300`).
