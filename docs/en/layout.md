# Layout

[Русский](../ru/layout.md) · **English**

The `layout` stage cuts a workbook into blocks with a period axis and body rows. Without fact rows, mapping is not called: a job can finish `succeeded` with an empty context. If there is no axis, the shape classifier builds a `params` block (label + value column) or drops prose and navigation. Those regions are not in the completeness denominator.

Code: `src/finance_context/layout/` (`detect.py`, `periods.py`, `params.py`) and reference parsing in `src/finance_context/formulas/engine.py`.

Related documents: [overview](overview.md), [mapping](mapping.md), [graph](graph.md), [architecture](architecture.md).

## What counts as a block

A sheet collects **axis candidates**, then a body from one header to the next.

A calendar header is a row with ≥2 cells that `classify_header` treats as a date / year / quarter / month (`2024`, `2024E`, `Q1 2025`, `01.01.2026`).

A FAST date band (`Start of period` / `End of period`) is one axis of the sheet. The period key comes from the end date (the year for annual), and `header_row` is the end-date row (`PF Model!r7`, not the flag row). A column that has only an end date and no start (`Model_start` in L) gets the role `stub` and is not a period. 0/1 rows immediately under the ruler (`Construction` / `Operations`, formula `IF(AND(start>=G, end<=H),1,0)`) are not in the header band: they are flag rows of the block, and phases attach to this axis. A formula header that refers to the date row in the same column (`=YEAR(M7)`) is an alias of the same axis. Flag fingerprints are not compared for that alias.

A relative header (project finance) is a label `Year` / `Period` / `Month` / `Quarter` (and Russian год/период/мес/кв) with a contiguous integer run `0|1, 2, 3, …` of length ≥ 3 to the right. Period keys: `Y1` / `Q1` / `M1` / `P1`. Column role: `relative`. Grain: `model_year` / `model_quarter` / `model_month` / `model_period`. `apply_grain` does not touch calendar keys.

After `build_context`, 0/1 flags attach to the axis whose rows contain them (`context.axes`): `phase` construction/operation, `phase_year` inside a run, overlays (repayment, availability) only in `flags`. A calendar book without those flags gets `calendar_year` and `phase=null`. Flags stay excluded from mapping.

If the row has no Year word, but there are ≥ 8 integers `0|1..n` to the right **and** those columns match the widest formula-copy run on the sheet (`formula_template` / R1C1), the axis is still built. A `Week #` counter under a calendar on the same columns does not become a second axis.

A candidate is dropped when it has fewer than two periods (calendar) or fewer than three (a labeled relative axis).

## Axis dominance

On one sheet, false headers often sit **left** of the real timeline: a `Start`/`End` date pair in assumptions next to 40 year columns on the right. A short calendar run **inside or to the right** of a wide axis is also false when the column set differs.

The widest candidate is `primary`. What remains:

- the same column set (±1 / Jaccard ≥ 0.8) — two tables stacked on one timeline;
- non-overlapping columns, including different width and grain (years on the left, months on the right), when each run has ≥3 periods (or ≥2 years) — two side-by-side bands;
- a narrow run of ≤2 periods left of a wide axis (Start/End) is still dropped.

Calendar columns of one header are split into runs at an empty column and at a grain change (`2020` vs `янв.25`). A year repeated over the same columns as a finer axis does not become an axis: it writes `group_key` onto that axis's periods. A year and a month in one header band with one label column are one table (`axis_ids`): rows once, value series per axis. The same column sequence and keys in the next section's header do not open a second axis: the section points at the first (`Output!r6c3`). Section blocks stay distinct. If two sections put different flags on the same period, the axes are not merged. Week and biweek axes are not collapsed when keys repeat.

`body_rows` are cut from the **filtered** list, not from the raw header bands.

Formula-copy (the same `formula_template` in neighboring columns, length ≥ 3) sets the timeline geometry: headers confirm it. Flags and constants that are not copied along the row are not an axis.

## Label zone

Previously `label_col = min` of the text column: sections in col 2 and articles in col 4 made the body skip silently.

Now each body row takes the leftmost text cell left of the axis (not a number, not a period). The histogram of those “left” columns gives `block.label_col` (argmax; the left one wins a tie). `span` is those columns left of primary, plus primary.

A row label is the first non-empty cell of the span; `indent` is the position in the span plus leading spaces. A `LayoutRow` has its own `label_col` (the address in context) when the article is not in `block.label_col`. The word `Total` / `Sum` / `Итого` / `Всего` in the label column is the row name, not a column header. If the span has no label, the first text left of the period columns is used (`CHECK`).

Row kinds (`fact` / `abstract` / `index` / `helper` / `flag`) are as in [architecture.md](architecture.md). `index` only when the axis runs `0|1, 2, 3, …` and the label is a counter (`week`, `#`, …) **or** the period cells have no formulas. The “every number ≤ 12” branch is gone: money 1..12 with formulas stays `fact`. Placeholders `Spare` / `None` are `helper`. Kind `flag` is only a binary series (≥90% of values in `{0, 1}`) or a `flag` token in the label. `Mid case` / `Low case` / `Applied (real terms)` with prices stay `fact`. `Case Number` is a scenario header, not a data row. A selector (`Live Case`, `* choice`) is `flag` with `context_role=scenario_selector`. A scalar left of the ruler (IRR, NPV, `Months per year`) with a numeric role cell and no period values is `fact`, not `abstract`. A row under a `Check` outline stays `helper`.

## Formulas and the axis

Compile marks a formula `unparsed` when a token is not parsed. A range with the sheet qualifier **on the right end** (`SUM(TBA!$D$10:'TBA'!D10)`) is allowed: the sheet prefix after `:` is skipped. Otherwise structure does not see the SUM/edges, and warnings accumulate in context.

Cached values are not required for layout when the header text is already a calendar or a year index.

## Blocks without a period axis

If the sheet has no axis, `detect_params_block` classifies the shape (not a whitelist of sheet names):

| Shape | Result |
| --- | --- |
| A label in the left text column **and** a value column (numbers / dates / short scalars) on ≥3 rows | `block.kind=params` |
| A 2-D grid whose row label is itself a number (`Sensitivity`) | Header row only, `kind=abstract` |
| Long prose, Cover, Disclaimer, shortcuts | Region dropped, not in completeness |

A params header may be multi-row: scenario names and `Units` / `Values` / `Active` / `Notes` are collected per column. Unknown text names above numeric columns become role `scenario`. The **active value** column is chosen by the number of cross-sheet dependents in `ir/edges.parquet`; the header lexicon is a hint.

A **Scenario Chosen** / selected scenario row is not a column header. It is a control (`kind=flag`). The live-scenario index (often `D3`) stays a block row with `disposition=excluded` and `context_role=scenario_selector`; column D is the selected value, F:… is the case matrix. Numbers `1..n` on the selector row do not overwrite axis names (`Base Case`). The selector label does not vote for the `value` column, or `label_col` slides right.

An all-caps row with no values becomes `abstract`. A `Check` / `Result` table becomes `helper` / `check_row`; exclusion itself takes it out of tagging.

On a sheet **with** an axis, `carve_params_regions` cuts out rows that have no values to the right of the scenario span:

| Region | Result |
| --- | --- |
| A `Case Number` / `Scenario` header and a run of `1..n` (n ≥ 3 and not wider than 60% of the period columns) | `params`: a column labeled like `Live Case` is `value`, columns `1..n` are `scenario` (`Case 1` …). The selector (`Live_case`, `OFFSET`) is `context_role=scenario_selector` |
| Checks and constants (Reference / Result / a scalar in E/G/H), when there are ≥ 3 fact/helper rows | a separate `params` block |

Those rows do not stay in the timeline block. Scenario columns are not part of the axis.

## Non-period cells on a timeline

Columns between the label zone and `min(period_col)` get roles on `LayoutRow.cells`: `SUM` over the period columns → `total`; unit text (`k£`, `EUR'000`, `EUR/MWh`, `%`, `x`, `years`) → `unit`; a column with only an end date and no start → `stub`; any other number/formula → `value`. A role cell has `header` — the nearest label in the same column inside the section (`Start`, `End`, `Live Case`, `Min`, `Avg`). `total` and `stub` are not periods of the axis; the graph link for that cell has `period_id=null`. These are scalars such as `Construction!C22`, not a dump of every cell on the sheet.

## What to check when mapping is empty

| Symptom | Where to look |
| --- | --- |
| 0 blocks | No calendar axis, no `Year`+`1..n`, no long `1..n` on a formula-copy, and the shape is not params (prose / nav) |
| Many 2-period blocks, 0 fact | False Start/End pairs were not filtered, or labels sit to the right of `label_col` |
| Blocks exist, fact = 0 | Label zone / skip of an empty `label_col` |
| No `D3` / Scenario Chosen among block rows | The control row landed in the params header (`params.py`) |
| `Unparsed formula` on a header or a SUM | `formulas/engine.py`, not the taxonomy |

Public corpus: Packt (year index + `Input Assumptions` as params) and RVI (calendar + sections in several columns; `Top Shortcuts` produces no rows). Gold: `tests/fixtures/mapping/*_dispositions.yaml`. `three-statement.xlsx` may be unavailable in the lock — the corpus test skips.

Detector tests: `tests/layout/`. On `cashflow.xlsx`, params blocks appear (Assumptions / Checks); Cover and Disclaimer still have no rows. Timeline `label_col` of the weekly blocks must not slide.
