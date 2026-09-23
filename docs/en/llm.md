# Metric slice for the answering LLM

[Русский](../ru/llm.md) · **English**

The job canon is `context.json` / `context.md` (schema `1.13.0`) and `graph.json` / `graph.md` (schema `1.7.0`). The slice below is a prompt projection. It is not an artifact and it does not replace `blocks[].rows`. Cells, AST, and edges are not copied into the slice.

Related documents: [overview](overview.md), [architecture](architecture.md), [graph](graph.md).

## Two consumers

| Who | What they see | What they do not see |
| --- | --- | --- |
| Mapping (`ChatPort`) | Label, `label_path`, neighbors ±2, period headers | Numbers, the cache, units as an amount |
| An answer over an already built model | One observation: row × period, the fields below already joined | Raw `values[]` and a request to join the axis, `axes`, and the cell itself |

The mapping chat does not receive this slice. A wrong `concept_id` is worse than `unknown`.

## Example

An architecture example. The `observation` object is not written into the job JSON: it is assembled from the row, that axis's series, and `links` / trace.

```json
{
  "observation": {
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
    "scenario": null,
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
      "end_date": "2026-12-31",
      "group_key": null,
      "flags": {}
    }
  }
}
```

`Y5` and `phase_year: 1` are different clocks. The fifth model year can be the first operating year. `dimensions` is optional: today it receives `hints.segment` (`pc` / `hv`) when that hint is set. It is not a `vehicle_type` field and not a dimension dictionary.

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
| `period_position`, `aggregation` | row fields | For example `during_period` and `sum`. A scalar and a params row are `instant` / `none`. Next to `hints.time_semantics` |
| `scenario` | header of a params value/scenario column (`cells[].header`) | `Live` or `Case N`. Absent on a timeline row |
| `timeline.start_date`, `end_date` | `axes[].periods[]` | ISO when the axis was built from a Start/End band. Otherwise the keys are absent |
| `unit.kind`, `currency`, `scale`, `sign` | `hints.unit`, `hints.currency`, `hints.scale`, `hints.sign` | `scale` is the token `unit` / `k` / `m` / `bn`. The multiplier is the separate `scale_factor`. Without `kind` a rate looks like money |
| `formula.text` | `row.formula` | One fingerprint per row. Cell differences are `formula_exceptions`, not a second text by default |
| `formula.class` | `links[].formula_class` | One class per formula cell. AST is not copied |
| `formula.precedents` | `graph.links[]` or `GET .../graph/trace` | A short list of `row_key`, `concept_id`, `period_id`. AST is not copied |
| `source.sheet`, `source.cell` | The row plus the period column; a formula also has `links[].cell` | A citation. `context.json` has no per-cell `source` |
| `timeline.phase`, `phase_year`, `group_key`, `flags` | `axes[].periods[]` with the same `period_key` on the series axis | Copied into the slice so the model does not join. Phase is not duplicated on `blocks[].periods`. `group_key` is set on a month under a repeated year |

`flags` (case, covenant, repayment, and other 0/1 overlays) come from the axis whose rows contain the flags. Phase alone is not enough to read the number.

## What the slice does not contain

- A replacement of `context.json` by a catalog of observations. Completeness stays the row: one row, one fingerprint, `values[]` along the axis.
- `validation_context`. `phase` / `phase_year` are timeline clocks, not a check result.
- Replacing `value` with the multiplier or a JSON number. The cache stays a string; the multiplier and the base amount are separate fields.
- AST. It stays in `ir/cells.parquet` and enters a conversation only as a separate request, not in the ordinary prompt.
- A second job JSON. The slice lives in the answering model's prompt.
