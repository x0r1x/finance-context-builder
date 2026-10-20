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
| `context.json` / `context.md` | Schema `1.14.0`: passport, `axes` (the only place for flags and `group_key`), every block and every row (`row_key`, one formula, cached series per `axis_ids`, `value_statuses`, `scale_factor`, `normalized_values`. A year repeated over months is `group_key`, not a second axis. A year and a month in one header band are one table. The same header repeated in the next section points at the same axis. Markdown `## Axes` prints each axis once, periods as columns: the header is the axis id and the period keys (`\| PF Model!r9 \| 2021 \| 2022 \| 2032 \|`), and under it `Start` / `End` (ISO dates when the book has a Start/End ruler), `Group` / `Calendar` / `Flags`. A block table adds `Path` (`label_path`) and `Cells` (`L total: -86400`, `G Start: 01.01.2024`). A role cell has `header`. A `total` / `stub` column is not part of the axis. Empty attribute rows are hidden: a year with no dates, group, or flags is the header alone. A regular month grid collapses to one column per year (`\| Output!r6 \| 2020 \| 2021 \|` and a row `\| Periods \| 2020-01 .. 2020-12 \| ... \|`). JSON omits empty `flags` and a `calendar_year` that repeats the period key. The block table has `Axes: ...` and one grid per series. An empty cell is `empty`, a non-applicable cell is `n/a`, thousands render as `k£ ×1000` and `1.5 (1500)`. A period header is `period_key` plus the column letter (`Y23 (AA)`). A period value shows the cache and `[Sheet!A1]`. Under the block, `relations` use the same `row_key`. There is no second row catalog and no cell registry. The header line is content completeness. `graph` is a pointer |

Catalog, summary, and observations are HTTP projections, not files. Schemas: `catalog-2`, `summary-2`, `observation-2`. Details: [slices.md](slices.md).

Formula IR and layout for the same workbook are shared: `$DATA_DIR/shared/books/{job_id}/`.

## Pipeline

`parse → compile → layout → graph → build → render`.

The passport, the catalog, and observations are not in that sequence. They are read from `context.json` and `graph.json` that the job has already written.

HTTP starts a separate process for each new book and then reads `meta.json`. CLI `build` stays one process. PyArrow writes parquet: it is the compile cache and the trace input after the process exits. Inside one run, stages pass the row lists they already built. A repeat POST of a finished book returns the snapshot when `meta.json` `publisher` matches the code on disk. A different `publisher` stops that book's process and starts a new one; a missing stamp does not. The new run deletes only the stale stage tail in `meta.stages`, or from layout downward when that map is absent. A job left `queued` or `running` across a restart is `failed` with `error` `process_lost`. `scripts/run.sh` only polls HTTP. Its `JOB_TIMEOUT_SEC` defaults to 300 seconds and does not stop the process; the server stops the process at its own `JOB_TIMEOUT_SEC` (3600 in `.env.example`).

If the detector built neither a timeline nor params (prose / navigation) or found no article labels, there are no fact rows: the status can be `succeeded` with an empty context. That is layout. Details: [layout.md](layout.md).

Section headers (`abstract`) and counters (`index`) are **not dropped**: they are rows of their block. Values along an axis are an array on a `fact` / `flag` / `helper` row; params rows have column roles and values only in value columns. A row has one formula. Cell-level adjacency and AST live in IR / [graph.md](graph.md). Downstream sees the label, `label_path`, neighbors from formula row refs, the formula, hints, and the cache.

Markdown reports **content completeness** as `1.00 (rows/rows layout rows)`: every layout row of a published block is in the document.

## Job statuses

| Status | Meaning |
| --- | --- |
| `queued` / `running` | Still working |
| `succeeded` | Documents are ready |
| `failed` | Pipeline error, or the book's process was stopped by the server `JOB_TIMEOUT_SEC` (`error` = `TimeoutError`). No usable context |

Older snapshots may still say `needs_input` or `degraded`. Those jobs are finished, and the same four documents are served.

## What a row records

Hints come from the label and the unit cell: `unit` (`money` / `count` / `rate` / `ratio` / `years` / `price` / `date`), `unit_per`, `currency` (GBP/EUR/USD/RUB), and `scale` (`unit` / `k` / `m` / `bn`). `k£` in a label or Units cell is money, GBP, and `k`. `тыс. руб.` / `млн ₽` / `млрд руб.` is money, RUB, and `k` / `m` / `bn`; `руб.` alone is `unit`. `RURm` / `mRUB` / `RUB mn` is money, RUB, and `m`; `RUR` alone is RUB at `unit`; `USD bn`, `EURk`, and `RUB'000` carry `bn` / `k`. A unit caption in the period-header label applies to the block. A unit row inside a section applies until the next section. A unit cell on the row itself wins. A currency known only from a number format (`[$₽-419]`) leaves `scale` empty; `[$-419]` names no currency.

Time comes from the label and the section. `Opening`, `BoP`, `b/f`, `b/fwd`, `beginning of period`, `на начало периода`, `входящий остаток` are `bop`; `Closing`, `EoP`, `c/f`, `c/fwd`, `end of period`, `на конец периода`, `исходящее сальдо` are `eop`. A fact row on a `Balance Sheet` / `Баланс` tab (a year, `Consolidated`, or a scale such as `тыс. руб.` may sit beside the title) or under that heading is `stock` (`end/last`). A price (`EUR/MWh`), a percent, or a multiple (`x`, or a `DSCR` / `LLCR` / `ratio` / `коэффициент` label) stays `rate` on that sheet too. A price or a multiple, including a percent-formatted coverage ratio, has aggregation `none`. An ordinary percent rate keeps `average`; an index level keeps `last`.

Formula-shape relations on the block are `alias`, `aggregate`, `difference`, and `roll_forward`. They are not a second catalog of the row. `roll_forward` is a reference to the previous column on the same sheet; the previous column of another sheet is an `alias` (sheets often shift the period grid by one column).
