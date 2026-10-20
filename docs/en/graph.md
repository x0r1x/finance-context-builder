# Formula graph

[Русский](../ru/graph.md) · **English**

The public graph is two files with the same facts: `graph.json` and `graph.md` (schema `1.7.0`). Cell-level edges and AST stay in `ir/*.parquet`. The model is `context.json` / `context.md`. Walking one cell is `GET .../graph/trace` and `GET .../graph/trace.md`, not a catalog of every cell. The graph is not merged into context.

Code: `src/finance_context/graph/` (`stage.py`, `cycles.py`, `trace.py`), render in `src/finance_context/render/graph.py`, range expansion in `formulas/csr.py`. Pipeline: `parse → compile → layout → graph → build → render`.

Related documents: [overview](overview.md), [architecture](architecture.md), [slices](slices.md).

## Who owns each fact

One fact, one place.

| Fact | Where |
| --- | --- |
| Formula, AST, cell cache | `ir/cells.parquet` |
| References as written in the formula (a range is one target, named ranges) | `ir/edges.parquet` |
| Cell→cell after expand: `dangling` / `dangling_reason` / `status` / `reason` / `evidence` / `range_ref` / `truncated`. `col_offset` and `period_lag` are empty here | `ir/cell_edges.parquet` |
| `col_offset` and `period_lag` after graph. Graph does not rewrite `cell_edges` | `ir/graph_edges.parquet` |
| `row_key`, `period_id`, `node_type` (including `empty` for verified blank cells) | `ir/graph_index.parquet`; a formula also has `links[].row_key` and `links[].period_id` |
| Counts (`nodes` / `edges` are cell-level parquet sizes), `iterate`, cycles, `circularity_hints`, `dangling_classes`, and `links[]` with `formula_class` | `graph.json` and the same in `graph.md` (schema `1.7.0`, Class column) |
| Every `<c>` on a sheet: `populated` or `styled_blank` | `raw/cell_presence.parquet` |
| Report row: label, one row formula, cached series per axis, value status, scale factor, normalized values | `context.json` / `context.md` (schema `1.14.0`, axes in `axes`) |

`links[]` is one record per formula cell: `cell`, the A1 text once, `formula_class` (`same_period`, `cross_period`, `aggregation`, `rollforward`, `conditional`, `hardcoded`), `refs`, the context row's `row_key`, and the axis column's `period_id`. Both keys are `null` when the cell is outside layout. A `total` or `stub` column is not a period, so its link has `period_id=null` even when the row sits in a timeline block. `SUM(J9:J12)` stays one range, not dozens of `range_member`. Empty range members are not in `links`. `nodes` and `edges` are not `len(links)`. `context.md` prints `[Sheet!A1]` in the period value, the same key as `links[].cell`; the formula stays on the link. The period header still carries the column letter (`Y23 (AA)`).

`context.json` stores a `graph` pointer (counts and paths). It has no `precedents_rows`, `dependents_rows`, `precedent_cells`, `formula_ast`, and no second row catalog. A row formula is one fingerprint. Values of a one-axis block are a flat cache array or `null`; with several axes the numbers live in `rows[].series` by `axis_id`, and flat `values` repeat the first series. Beside them, `value_statuses` and `normalized_values` have the same length, plus `scale_factor`. `flags` and `group_key` live in `axes`, not in a `timeline` field. This is not a cell catalog. Precedents in the observation slice come from `links` / trace (`row_key`, `period_id`); AST is not copied into the slice.

There are no separate `graph-edges.json`, `graph-dangling.json`, or `formulas.json`. Cell-graph completeness does not live in `context.blocks[].relations` (those are formula-shape alias, aggregate, difference, and roll_forward).

`dangling_reason` is computed from cell-edges and published as a class in `dangling_classes`, not as a list of addresses. The parquet edge also carries `status`, `reason`, and `evidence`. An empty cell and an unresolved reference are not mixed.

| class | status | reason | evidence | `dangling` |
| --- | --- | --- | --- | --- |
| `empty_range_member` | `empty` | `actual_blank_cell` | `omitted_by_excel` or `styled_blank` | no (`node_type=empty`) |
| `empty_ref` | `empty` | `actual_blank_cell` | the same for a single ref / cross_sheet on a parsed sheet | no |
| `missing_sheet` | `unresolved` | `missing_sheet` | `sheet_not_in_workbook` | yes |
| `parser_resolution_failure` | `unresolved` | `parser_resolution_failure` | `populated_missing_from_index` or `bad_address` | yes |

`included_in_formula_semantics` is `true` for `empty` (Excel reads a blank cell as zero or as an INDEX member). For `unresolved` it is `"unknown"`.

`omitted_by_excel` means the address is not in `cell_presence.parquet` and the sheet was parsed. `styled_blank` means the XML has a `<c>` with no value and no formula. If presence says `populated` and the cell is missing from the index, that is `parser_resolution_failure`, not a blank cell.

`unresolved` formulas, `external`, `dynamic`, and `truncated` are not mixed with blank cells in `dangling_classes`: they have their own counters. `graph.dangling.count` is only `status=unresolved`. The `empty_range_members` counter stays on the context pointer and is not a warning.

There is no `report.json`.

## Cycles

SCC on cell-edges with `kind ∈ {ref, cross_sheet, range}` (no unresolved/dangling):

| `class` | When |
| --- | --- |
| `iterative_ok` | Every edge in the component is a period shift (`period_lag` ≠ 0 / `same`), a typical roll-forward |
| `unexpected` | Any other cycle, including same-period Uses↔Interest |

An empty `cycles: []` does **not** mean Excel iterate is off. `graph.json.iterate` copies `calcPr/@iterate` from the workbook. If an SCC is found, the cycle record lists `members` and `class`. `breakers` stays an empty list. If there is no SCC and a label contains `circular`, `circularity_hints[]` is written (`sheet`, `row`, `label`, `cell_ids`).

## Trace

The CLI writes `context.json`, `context.md`, `graph.json`, and `graph.md` in one process. HTTP runs the book in a separate process; `scripts/run.sh` (with `serve` already up) only downloads those documents into `out/<timestamp>/json/` and `out/<timestamp>/md/` and does not itself stop the process. `scripts/check-graph.py` requires graph schema `1.7` and context schema `1.14`, the `links` key, and parquet paths in `artifacts`. It forbids AST and an expanded `range_member` in the public graph, a second row catalog, a per-cell `source`, and a merge into one contract (`formulas` / `concepts` / `series` / `audit_trail` at the top of context). `values` stays a flat list; `value_statuses` and `normalized_values` have the same length. Each axis from `axes` is a Markdown table header `| {axis_id} | period | ... |` (periods as columns, not rows). Period keys of one axis are unique. A repeated calendar key whose columns carry different variant markers is `<period_key>@<marker>` (`casefold` of the header marker) so the index keeps every column. `period_lag`, period order, and a period-chain cycle use the calendar key before `@`, so those columns share one slot. `period_lag` is the distance inside one grain: years differ by year, halves by `2*year+half`, quarters by `4*year+quarter`, months by `12*year+month`, and days by the number of days. A missing period stays in that count (`2016Q4` to `2011Q2` is `-22`). The same calendar key, including `<period>@<marker>` variants, is `same`. An ordinal key the axis parser emits (`Y<n>`, `Q<n>`, `M<n>`, `P<n>`, from a Year / Год / Quarter / Month / Period label) is its own grain, and the lag is the difference of those numbers (`Y3` to `Y1` is `-2`). A cross-grain pair, including an ordinal key against a calendar year or against another letter, or a key that is not a period (`value`, a scenario), is `unaligned`. `classify_formula` skips `unaligned`, so that edge is not a prior step and cannot by itself be `rollforward`. A bare `2024` beside `2024E` stays two ids (`2024` and `2024@e`). A role cell `total` / `stub` / `scenario` is not part of the axis. If a period has `start_date` / `end_date`, `context.md` has `Start` / `End` rows, dates satisfy `start ≤ end`, and the next period starts the day after `end`. Every non-unit role cell of a row (address and cache) is visible in `context.md`. Markdown must contain the same blocks, rows, `row_key`, `period_id`, links, and formula class, and for a row the `empty` / `n/a` and scale when they are in the JSON. A non-empty link `row_key` must be a context row. `--axes-summary` prints one line: axis count, block count, and the period span. `scripts/run.sh` writes it to `summary.txt`. If check-graph fails, the script prints the error and exits 1.

A link record:

```json
{
  "cell": "P&L!C13",
  "formula": "=SUM(C9:C12)",
  "formula_class": "aggregation",
  "refs": ["P&L!C9:C12"],
  "row_key": "P&L|13|P&L!r1",
  "period_id": "2024"
}
```

```bash
bash scripts/run.sh path/to/model.xlsx
# out/<timestamp>/json/context.json graph.json trace.json
# out/<timestamp>/md/context.md graph.md trace.md
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.json"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.md"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph/trace?from=P%26L!C13&direction=precedents&depth=8"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph/trace.md?from=P%26L!C13&direction=precedents&depth=8"
```

`from` is a cell address (`P&L!J13`), a `row_key` (`P&L|13|P&L!r2`), or a label (`EBITDA`). `direction`: `precedents` (toward inputs) or `dependents` (toward results). The response has the address, `row_key`, period, cache, A1 formula, and refs. No `formula_ast`. A range is one edge; empty `INDEX` members are not nodes. Non-empty `SUM` members are walked so depth reaches the next cells.

Expanded ranges stay in IR. The public link keeps one range ref.

## Examples (project finance)

- EBITDA `=SUM(J9:J12)` in `links` is one ref to the range. The cell expansion stays in `ir/cell_edges.parquet`.
- Blank cells inside `INDEX(J8:O8)` are `empty` / `actual_blank_cell` in parquet and a `dangling_classes` counter, not a parser error and not a ref in `links`. A single reference to the same blank cell is `empty_ref`. Trace does not show those blank addresses.
- `P&L` Gross revenues `=Operation!…` with `C[-1]` gets `period_lag` ≠ `same` in `ir/graph_edges.parquet`. In `ir/cell_edges.parquet` the lag stays empty.
- From the labels `EBITDA` / `CFADS`, trace reaches Input Assumptions (traffic, inflation, rates) when the formulas are linked that way.
