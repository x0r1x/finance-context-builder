# Project overview

[Русский](../ru/overview.md) · **English**

**finance-context-builder** is a read-only service that turns Excel cash-flow models (`.xlsx` / `.xlsm`) into versioned JSON and Markdown context for people and LLMs.

Formulas are not recalculated: artifacts keep Excel's cached values. Content is complete: every layout row lives once inside its block. The taxonomy annotates `concept_id`; it is not a funnel. A wrong tag is worse than `unknown`. An unknown row stays in the block with its label hierarchy, neighbors, formula, and top-3 candidates. JSON and Markdown are the same facts. Cells, AST, and edges are not merged into one document: they stay in `raw/` and `ir/`, while the public `context` and `graph` describe the report row and formula links.

How to run, Docker, and the API: [README](../../README.md). The layer snapshot is [architecture.md](architecture.md).

| Topic | Document |
| --- | --- |
| Blocks, period axes, row labels | [layout.md](layout.md) |
| Mapping cascade, signals, thresholds, glossary | [mapping.md](mapping.md) |
| Taxonomy: fields, id families, how to add a meaning | [taxonomy.md](taxonomy.md) |
| Formula graph, cycles, trace | [graph.md](graph.md) |
| One observation for the answering LLM | [llm.md](llm.md) |
| Review of `unknown` / `unmapped.json` after a run | [review.md](review.md) |

## Input and output

The input is a workbook whose formulas are already calculated. `.xls`, `.xlsb`, and encrypted files are rejected.

A job writes:

| Artifact | Contents |
| --- | --- |
| `raw/` | Cells, formulas, cache, formats, `cell_presence.parquet` (`populated` / `styled_blank`) |
| `ir/` | Templates, AST, `edges.parquet` (as written in the formula), `cell_edges.parquet` (formula expansion, lag empty), `graph_edges.parquet` (`col_offset` / `period_lag`), and `compile.json` (IR schema stamp) |
| `layout.json` | Report blocks, period axes, row kinds |
| `mapping.json` | Fact/flag/helper rows linked to a `concept_id`, or an abstention |
| `graph.json` / `graph.md` | Schema `1.7.0`: cell-level parquet counts, `iterate`, cycles (`breakers`) / `circularity_hints`, `dangling_classes`, and `links[]` (cell, A1 formula, `formula_class`, `row_key`, `period_id`, inputs; a range is not expanded). Markdown links have a Class column. AST and cell-edges stay in `ir/` |
| `context.json` / `context.md` | Schema `1.13.0`: passport, `axes` (the only place for phases, flags, and `group_key`), every block and every row (`row_key`, `disposition`, `concept_id`, one formula, cached series per `axis_ids`, `value_statuses`, `scale_factor`, `normalized_values`, `period_position`, `aggregation`). A year repeated over months is `group_key`, not a second axis. A year and a month in one header band are one table. The same header repeated in the next section points at the same axis. Markdown `## Axes` prints each axis once, periods as columns: the header is the axis id and the period keys (`\| PF Model!r9 \| 2021 \| 2022 \| 2032 \|`), and under it `Start` / `End` (ISO dates when the book has a Start/End ruler), `Group` / `Phase` / `Phase year` / `Calendar` / `Flags`. A block table adds `Path` (`label_path`) and `Cells` (`L total: -86400`, `G Start: 01.01.2024`). A role cell has `header`. A scalar and a params row are `instant` / `none`. A `total` / `stub` column is not part of the axis. Empty attribute rows are hidden: a year with no phase is the header alone. A regular month grid collapses to one column per year (`\| Output!r6 \| 2020 \| 2021 \|` and a row `\| Periods \| 2020-01 .. 2020-12 \| ... \|`). JSON omits an empty `phase`, empty `flags`, and a `calendar_year` that repeats the period key. The block table has `Axes: ...` and one grid per series. Time sits next to Unit (`flow/during_period/sum`), an empty cell is `empty`, a period outside the phase is `n/a`, thousands render as `k£ ×1000` and `1.5 (1500)`. A period header is `period_key` plus the column letter (`Y23 (AA)`). Under the block, `relations` use the same `row_key`. There is no `inventory` / `unmapped` / `excluded` and no cell registry. `mapping_stats` (`concept_coverage` and `mapping_quality`) and a `graph` pointer |
| `unmapped.json` | Abstained rows from `blocks[].rows` (after `run.sh`, at the run root) |

The answering LLM gets one observation sliced from these artifacts, not a second job JSON: [llm.md](llm.md).

Across jobs: `$DATA_DIR/glossary.json` (learned high-confidence pairs) and `taxonomy_embeddings.npz` (concept embedding cache).

## Pipeline

`parse → compile → layout → mapping → graph → build → render`.

HTTP starts a separate process for each new book and then reads `meta.json`. CLI `build` stays one process. PyArrow writes parquet: it is the compile cache and the trace input after the process exits. Inside one run, stages pass the row lists they already built. A repeat POST of a finished book returns the snapshot. `?remap=1` stops that book's process and starts a new one. `scripts/run.sh` only polls HTTP. Its `JOB_TIMEOUT_SEC` defaults to 300 seconds and does not stop the process; the server stops the process at its own `JOB_TIMEOUT_SEC` (3600 in `.env.example`).

The mapping cascade resolves `fact` / `flag` / `helper`. If the detector built neither a timeline nor params (prose / navigation) or found no article labels, there are no fact rows: the status can be `succeeded` with an empty context. That is layout, not the taxonomy. Details: [layout.md](layout.md).

Section headers (`abstract`) and counters (`index`) are **not dropped**: they are rows of their block (`disposition=header` on abstract). Values along an axis are an array on a `fact` / `flag` / `helper` row; params rows have column roles and values only in value columns. A row has one formula. Cell-level adjacency and AST live in IR / [graph.md](graph.md). Downstream sees the label, `label_path`, neighbors ±2, the formula, hints, candidates, and the cache. An empty `unmapped.json` does not mean every block row has a `concept_id`.

Embeddings and chat are optional (an OpenAI-compatible endpoint, LM Studio by default). Without them, structure + lexical + glossary remain, then `unknown` with candidates.

Do not mix the two report metrics: **content completeness** must be 100% (block rows equal the layout rows of accepted blocks); **concept coverage** may honestly be below 100% (phasing 0.2/0.8 stays unknown).

## Job statuses

| Status | Meaning |
| --- | --- |
| `queued` / `running` | Still working |
| `succeeded` | No open mapping questions |
| `needs_input` | Context is ready; some fact rows stayed `unknown` |
| `degraded` | Same as `needs_input`, but LLM and embeddings were not configured |
| `failed` | Pipeline error, or the book's process was stopped by the server `JOB_TIMEOUT_SEC` (`error` = `TimeoutError`). No usable context |

`needs_input` is not a crash: `context.json`, `context.md`, `graph.json`, and `graph.md` are still served.

## Two levers for coverage

1. **Taxonomy** (`src/finance_context/ontology/taxonomy.yaml`) — a dictionary of meanings, not a solver. Edit it when a **new** financial value appears.
2. **Signal cascade** — features (label, section, neighbors, formula shape, graph). A new kind of match is a new `Signal`, not a wide `anti_labels` and not a branch “if the sheet is Cash_Receipts”.

The learned glossary does not replace yaml: it remembers pairs that were already confident, `(label, parent) → concept_id`, for later books.
