# finance-context-builder

Read-only service that turns Excel cash-flow workbooks (`.xlsx` / `.xlsm`) into versioned JSON and Markdown context. Formulas are preserved; values come from Excel cached results and are not recalculated.

## Limits (MVP)

- `.xls`, `.xlsb`, and encrypted workbooks are rejected
- Cached formula values must already be in the file
- LLM/embeddings are optional: mapping degrades to `unmapped` without them
- The HTTP worker is in-process. Multi-replica deploys need an external queue.

Adapted from [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). See `NOTICE`.

## Setup

```bash
uv sync
```

## CLI

```bash
uv run finance-context build path/to/model.xlsx -o ./out
```

Writes `context.json` and `context.md`.

## API

```bash
uv run finance-context serve --host 127.0.0.1 --port 8080
```

- `POST /v1/context-jobs` — upload workbook
- `GET /v1/context-jobs/{id}` — status
- `GET /v1/context-jobs/{id}/context.json`
- `GET /v1/context-jobs/{id}/context.md`
- `GET /healthz`, `GET /readyz`

Optional environment: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `DATA_DIR`.

## Tests

```bash
uv run pytest
uv run ruff check src tests
```
