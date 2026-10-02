# Metric slice for the answering LLM

[Русский](../ru/llm.md) · **English**

The job canon is `context.json` / `context.md` (schema `1.13.0`) and `graph.json` / `graph.md` (schema `1.7.0`). The slice below is a projection of those files. A route returns it in the response and does not write a second JSON. It does not replace `blocks[].rows`. Cells, AST, and edges are not copied into the slice.

Related documents: [overview](overview.md), [architecture](architecture.md), [graph](graph.md).

## Two consumers

| Who | What they see | What they do not see |
| --- | --- | --- |
| Mapping (`ChatPort`) | Label, `label_path`, neighbors ±2, period headers | Numbers, the cache, units as an amount |
| An answer over an already built model | One observation: row × period, the fields below already joined | Raw `values[]` and a request to join the axis, `axes`, and the cell itself |

The mapping chat does not receive this slice. A wrong `concept_id` is worse than `unknown`.

## Example

The response of `GET /v1/context-jobs/{id}/observations`. The object is not written into the job JSON: it is assembled from the row, that axis's series, and `links` / trace. A timeline row has no `scenario` key.

```json
{
  "schema_version": "observation-1",
  "truncated": false,
  "observations": [
    {
      "row_key": "Operation|10|Operation!r8",
      "label": "Revenue",
      "concept_id": "pnl.revenue",
      "disposition": "mapped",
      "dimensions": {
        "segment": "pc"
      },
      "period_id": "Y5",
      "value": "1234.56",
      "value_status": "cached",
      "normalized_value": "1234560",
      "scale_factor": 1000,
      "period_position": "during_period",
      "aggregation": "sum",
      "unit": {
        "kind": "money",
        "currency": "GBP",
        "scale": "k",
        "sign": "inflow"
      },
      "formula": {
        "text": "=RC[-1]*(1+Growth)",
        "class": "cross_period",
        "precedents": []
      },
      "source": {
        "sheet": "Operation",
        "cell": "H10"
      },
      "timeline": {
        "axis_id": "Operation!r8",
        "phase": "operation",
        "phase_year": 1,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31"
      }
    }
  ]
}
```

`Y5` and `phase_year: 1` are different clocks. The fifth model year can be the first operating year. `dimensions` is optional: today it receives `hints.segment` (`pc` / `hv`) when that hint is set. It is not a `vehicle_type` field and not a dimension dictionary.

## How to request the slice

Do not put `context.json` or `graph.json` in the prompt. Read graph counters from the passport. Read numbers from observations.

| Route | Schema | What is inside |
| --- | --- | --- |
| `GET /v1/context-jobs?status=&q=` | a list from `meta.json` | Session books. `context.json` is not opened. `q` is a piece of the file name |
| `GET .../summary` | `summary-1` | Coverage, workbook counters, and graph counters: `unresolved.count`, `external.count`, `dangling`, `missing_cached_values`. No `links` and no cell cache |
| `GET .../catalog` | `catalog-1` | Rows and axes without numbers. A period carries `phase_year`, `flags`, and `group_key`, omitted when empty the same way as the axis in `context.json`. No formula class: one row has a different class per period |
| `GET .../observations` | `observation-1` | The only response that carries a cell cache |

The first operating year and a period that carries a flag are named from the catalog periods.

While a job is still running, read status from `GET /v1/context-jobs/{id}`. The passport does not replace that poll: without `context.json` or `graph.json`, the passport and observations answer 409 `report_not_ready`.

Observations require at least one selector: repeatable `row_key`, repeatable `concept_id`, or `q`. A request with none of them returns 400 `selector_required`. The route does not send the whole book. `period_id` and `phase` are not selectors. An unknown selector inside a finished job is 200 and an empty list. A foreign `job_id` is 404.

`limit` defaults to 24 and stops at 48. Past the limit the observation list is cut and `truncated=true`. `precedent_depth` defaults to 0 and stops at 3. Depth 0 does not call trace: the precedent list is empty and `precedents_total` is 0. Depth 1–3 calls the current `trace_graph` and embeds the whole walk at that depth. `precedents_total` is the length of that list. A wider walk on this route is a higher `precedent_depth`. Above 3 the response is 422. Beyond that depth, read `graph/trace`. Each item carries `depth` and the `label` of the context row with the same `row_key`. With no `row_key`, `label` is null. A number outside the range is 422.

The `ETag` of catalog, summary, and observations is strong: schema, `content_sha256`, and the file stat. Summary and observations also include the `graph.json` stat. The same URL repeated with `If-None-Match` returns 304 and an empty body. `HEAD` of the same path returns that `ETag` and an empty body. 400 and 409 do not send an `ETag`. The cell address and the formula class are response fields. They are still not written into `context.json`.

## Slice field → current artifact

| Slice field | Where it comes from | How it enters the prompt |
| --- | --- | --- |
| `row_key`, `label`, `concept_id`, `disposition` | `blocks[].rows` | As stored. `concept_id` may be `null` when `disposition=abstained` |
| `dimensions` | `hints.segment` | Only when segment is set. Otherwise the key is absent |
| `period_id` | `axes[].periods[]` of the axis the row's series belongs to | Axis key (`Y5`, a calendar year, a date). Grain comes from that axis |
| `value` | `row.series[]` with the same `axis_id`, otherwise `row.values[i]` | Excel cache string or `null`. Not a float: formulas are not recalculated |
| `value_status` | `row.value_statuses[i]` | `cached`, `empty`, `zero_explicit`, or `not_applicable`. An empty cell is not replaced with zero |
| `normalized_value` | `row.normalized_values[i]` | A string in base units, or `null` if the number cannot be parsed. Not a replacement for `value` |
| `scale_factor` | `row.scale_factor` | Integer multiplier `1` / `1000` / `1000000` / `1000000000`, or `null` |
| `period_position`, `aggregation` | row fields | Markdown Time joins them with `hints.time_semantics`: `flow/during_period/sum` for a filled money year, `rate/during_period/average` for a filled annual rate. A point is `instant/instant/none` (position `instant`, aggregation `none`, so the series is not summed). A static rate is `rate/instant/none`. Scenario columns are alternatives and keep aggregation `none` |
| `scenario` | header of a params value/scenario column (`cells[].header`) | `Live` or `Case N`. Absent on a timeline row |
| `timeline.start_date`, `end_date` | `axes[].periods[]` | ISO when the axis was built from a Start/End band. Otherwise the keys are absent |
| `unit.kind`, `currency`, `scale`, `sign` | `hints.unit`, `hints.currency`, `hints.scale`, `hints.sign` | `scale` is the token `unit` / `k` / `m` / `bn`. The multiplier is the separate `scale_factor`. Without `kind` a rate looks like money |
| `formula.text` | the link with the same `row_key` and `period_id`, otherwise `row.formula` | The formula text of this period. No link means the row fingerprint or `null`. A stub formula is not substituted |
| `formula.class` | `links[].formula_class` | The class of this cell in the response. It is not in `context.json`. AST is not copied |
| `formula.precedents` | `trace_graph` when `precedent_depth` is 1–3 | `depth`, `label`, `row_key`, `concept_id`, `period_id`, the cache in `value`, the address in `cell`. `depth` 1 is a direct input. `label` is the context row with the same `row_key`. The list is the whole walk at the requested depth, and `precedents_total` is its length. Depth 0 leaves the list empty and `precedents_total` at 0. A wider walk is a higher `precedent_depth` or `graph/trace` |
| `source.sheet`, `source.cell` | the sheet, the row number, and `period.col` | The address is computed in the response. `context.json` has no per-cell `source` |
| `timeline.phase`, `phase_year`, `group_key`, `flags` | `axes[].periods[]` with the same `period_key` on the series axis | Copied into the slice so the model does not join. Phase is not duplicated on `blocks[].periods`. `group_key` is set on a month under a repeated year |

`flags` (case, covenant, repayment, and other 0/1 overlays) come from the axis whose rows contain the flags. Phase alone is not enough to read the number.

## What the slice does not contain

- A replacement of `context.json` by a catalog of observations. Completeness stays the row: one row, one fingerprint, `values[]` along the axis.
- `validation_context`. `phase` / `phase_year` are timeline clocks, not a check result.
- Replacing `value` with the multiplier or a JSON number. The cache stays a string; the multiplier and the base amount are separate fields.
- AST. It stays in `ir/cells.parquet` and enters a conversation only as a separate request, not in the ordinary prompt.
- A second JSON on disk. The slice is a route response, not a job file.

## How to read context and graph

- Join `values[i]` to `periods[i]`. `period.index` starts at 1 and is not an array index.
- On a timeline, years live in `axes[]`. An empty `block.periods` on that block is normal. On params, columns live in `block.periods`.
- The row hierarchy is `label_path`. `parent_label` may name a coarser section.
- An empty `row.formula` means the row's columns have no formula. Take a year's formula from the link with the same `row_key` and `period_id`, or from trace. No such link means the value is an input.
- Look up a name in the formula text in `refs` and in `workbook.defined_names`. A local cell and a local range are already replaced by an address in `refs`. A token with no `!` means the name is not one cell and not one range in this workbook. A name formula that contains `[` or `#REF!` points outside this workbook.
- `dangling` of 0 does not mean every name resolved. Read `unresolved.count` and `external.count` on the `summary` passport, not in the full `graph.json`. `ids` holds at most 32 entries; the full number is `count`.
- `concept_id` is not a metric key. A question such as "DSCR in 2030" finds the row by label and `row_key`, then the period. If `semantic_identity.concept_id` differs, name both.
- A year on the axis with no `phase` is not an operating year.
- Mapping questions are not published. A row without a concept is `disposition=abstained`; the count is `mapping_stats.abstained`.
- The warning counts formula cells missing a cache inside the phase. `workbook.missing_cached_values` counts every such formula, including years outside the phase.
- Do not put `graph.json`, `graph.md`, or a wide block table into the prompt. Take graph counters from `summary`. When `numeric_summary.constant` is true, quote one value and the addresses of the first and last periods. Do not compress the exact `values` in JSON.
- Do not recalculate. The Excel cache remains the source of the number.
