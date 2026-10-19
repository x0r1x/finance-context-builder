# Layout

[Русский](../ru/layout.md) · **English**

The `layout` stage cuts a workbook into blocks with a period axis and body rows. Without fact rows a job can finish `succeeded` with an empty context. If there is no axis, the shape classifier builds a `params` block (label + value column) or drops prose and navigation. Those regions are not in the completeness denominator.

Code: `src/finance_context/layout/` (`detect.py`, `periods.py`, `params.py`) and reference parsing in `src/finance_context/formulas/engine.py`.

Related documents: [overview](overview.md), [graph](graph.md), [architecture](architecture.md).

## What counts as a block

A sheet collects **axis candidates**, then a body from one header to the next.

A calendar header is a row with ≥2 cells that `classify_header` treats as a date / year / quarter / month (`2024`, `2024E`, `Q1 2025`, `01.01.2026`).

Calendar spellings: years `2025`, `2025E`, `FY2025`, `FY25`, `FY 25`, `2025г.`, `2025 год`; quarters `Q1 2025`, `Q2'25`, `1Q25`, `2025 Q1`, `2025Q1`, `2025-Q1`, `2025/Q1`, `Q1-2025`, `Q1/2025`, `1 кв. 2025`, `I кв. 2025`, `1-й квартал 2025`, `1–й квартал 2025` (en dash), `Кв.1 2025` (key `2025Q1`); half-years `H1 2025`, `2H25`, `2025 H1`, `2025H2`, `2025-H1`, `Н1 2025`, `1Н25`, `2025 Н1`, `1 полугодие 2025`, `II полугодие 2025 г.`, `1 пол. 2025`, `1 п/г 2025`, `2П25`, `Полугодие 1` (key `2025H1`, grain `half_year`); months `янв.25`, `января 25`, `мая 25`, `марта 2025`, `августа 2025`, `сентябрь 2025`, `январём 2025`, `2025-01`, `01.2025`. A role word before or after the period (`Факт 2025`, `2024 факт`, `2025 (план)`, `Budget 2026`, `Бюджет 2026`, `2026 бюджет`, `Plan 2026`, `Оценка 2025`, `Ожид. 2025`) sets the role and keeps the calendar key. The same suffix letters sit on a year, quarter, half, month or date (`2025П`, `1 кв. 2025П`, `1 кв. 2025Ф`, `01.2025A`). `П` is forecast, like `P`; `Ф` is explicit historical, like a bare `2025Ф`. Cyrillic lookalikes count only as that code: `Н` is the half marker `H` (`Н1 2025`, `1Н25`), and `2025Н` is not a year suffix. `А` is actual, like `A`. `Р` is forecast, like `P`. `Е` is forecast, like `E`. `П` stays the Russian plan letter (forecast) and `Ф` stays explicit historical. `FY25E` keeps the forecast suffix; `FY2025` is the four-digit form. `Q5-2025`, `Q1/2`, `2025 Q`, `FY`, `FYI` and `FY250` are not periods. A month header is a real name, abbreviation or case form (`мая`, `янв.`, `сентябрь`), so `маяк 25`, `марка 25` and `июльский 25` are not May, March or July. `I кв.`, `H2`, `Н1` or `Полугодие 1` without a year composes with a year row above it. `2П` without a year does not. Scenario headers accept either order (`сценарий базовый`, `Сценарий: базовый`, `Базовый сценарий`, `Пессимистичный сценарий`); a bare `базовый` or `мой сценарий` is not one. `Факт` or `Прогноз` in one cell paints every period column from that cell to the right until the next banner or the end of the axis, whether or not the cells are merged. A banner to the left of the calendar still paints the whole axis, and one marker sitting on a single calendar column under that banner overrides only that column. A later header with the same columns and period keys, and no role marker of its own, keeps those roles.

Param-header words, phase names and period role words live in `src/finance_context/layout/lexicon.json`. A word there matches whole, a trailing `*` keeps any ending (`факт*`, `строител*`), and a word with a space is a phrase; so `кв` is not `кВт` and `г` is not `график`. A param header is the whole cell after punctuation becomes spaces (`Ед. изм.` is `ед изм`, `Ед. измерения` / `Единица изм.` / `Размерность` / `UoM` / `Unit of measure` are units, `Значение, руб.` and `Значение (руб.)` are the value column `значение руб`, `Источник` / `Описание` / `Source` / `Comment` / `Comments` / `Comment(s)` are notes, `Наименование показателя` / `Статья затрат` / `Параметры проекта` / `Исходные данные` are labels). The `*` does not apply to that lookup. When the whole caption is not in the list, the first word still carries its role if at most two short qualifiers follow (`Комментарии КП`, `Источник информации`, `Source of information`, `Note(s)`, `Выводы ПМ`). `Мероприятия` is a note. A value, label, or unit head with a scenario word in the qualifier does not match, so `Value upside` can stay a scenario. `Отрасль` and `Периодичность` are not notes. Still hardcoded, not in the lexicon: total and stub markers (`Итого`, `Всего`, `Total`, `Sum`, `stub`, `частичн*`), scenario names (`Base case`, `Upside`, `Downside`, `Базовый сценарий`, `Сценарий: базовый`), month names and their case forms, the quarter and month axis labels, `Start of period` / `End of period` (`начало периода`, `конец периода`), and the scenario-table header (`Case` / `Scenario`). Label matching and a param-header lookup fold `ё` to `е` (`Опёрационный период` matches `операционн*`, `Значениё` matches `значение`). Period codes do not use that fold. Editing `lexicon.json` makes the layout stage stale, like a code change. Other JSON files in the package do not.

A FAST date band (`Start of period` / `End of period`) is one axis of the sheet. The period key comes from the end date (the year for annual), and `header_row` is the end-date row (`PF Model!r7`, not the flag row). A column that has only an end date and no start (`Model_start` in L) gets the role `stub` and is not a period. 0/1 rows immediately under the ruler (`Construction` / `Operations`, formula `IF(AND(start>=G, end<=H),1,0)`) are not in the header band: they are flag rows of the block, and phases attach to this axis. A formula header that refers to the date row in the same column (`=YEAR(M7)`) is an alias of the same axis. Flag fingerprints are not compared for that alias.

A relative header (project finance) is a label `Year` / `Period` / `Month` / `Quarter` (and Russian год/период/мес/кв) with a contiguous integer run `0|1, 2, 3, …` of length ≥ 3 to the right. Period keys: `Y1` / `Q1` / `M1` / `P1`. Column role: `relative`. Grain: `model_year` / `model_quarter` / `model_month` / `model_period`. `apply_grain` does not touch calendar keys.

After `build_context`, 0/1 flags attach to the axis whose rows contain them (`context.axes`): `phase` construction (`Construction`, `Constr. flag`, `Флаг строительства`, `Флаг строит.`, `Стройка`, `Период СМР`, `Инвестиционная фаза`, `Инвестиции`) / operation (`Operations`, `Op. flag`, `Флаг эксплуатации`, `Флаг эксп.`, `Операционный период`, `Операции`, `Производственная фаза`; a start, end, date or commissioning flag such as `Ввод в эксплуатацию` is not a phase, and neither is a duration such as `Срок строительства` or `Длительность эксплуатации`). `Начальный период эксплуатации` is the operation phase: only `начало` / `начала` / `начале` are start events. `phase_year` inside a run, overlays (repayment, availability) only in `flags`. A repeated flag label does not overwrite the earlier row: its key is the normalized label plus the already-assigned key of the nearest different preceding flag in parentheses. A taken key keeps that form and adds the row number. With no preceding flag, the key is the label plus the row number. A calendar book without those flags gets `calendar_year` and `phase=null`. Flag rows stay on the block with `disposition=excluded`.

If the row has no Year word, but there are ≥ 8 integers `0|1..n` to the right **and** those columns match the widest formula-copy run on the sheet (`formula_template` / R1C1), the axis is still built. A `Week #` counter under a calendar on the same columns does not become a second axis.

A candidate is dropped when it has fewer than two periods (calendar) or fewer than three (a labeled relative axis).

## Axis dominance

On one sheet, false headers often sit **left** of the real timeline: a `Start`/`End` date pair in assumptions next to 40 year columns on the right. A short calendar run **inside or to the right** of a wide axis is also false when the column set differs.

The widest candidate is `primary`. What remains:

- the same column set (±1 / Jaccard ≥ 0.8) — two tables stacked on one timeline;
- non-overlapping columns, including different width and grain (years on the left, months on the right), when each run has ≥3 periods (or ≥2 years) — two side-by-side bands;
- a narrow run of ≤2 periods left of a wide axis (Start/End) is still dropped;
- a header of ≥3 periods on another row stays its own block even when the columns overlap the wider axis. A run of ≤2 periods that overlaps that axis is still dropped.

Calendar columns of one header are split into runs at an empty visible column and at a grain change (`2020` vs `янв.25`). A gap that is only hidden columns stays one run, including when every period before the gap is hidden and the next header continues the sequence (`2023`, `2024`, an empty hidden column, `2025`). Hidden scratch that does not continue (`1965` beside `1939`) stays off that axis. A period that sits in a hidden column stays in that run and is published with `hidden`. A year repeated over the same columns as a finer axis does not become an axis: it writes `group_key` onto that axis's periods. A banner year that contradicts the year already inside the period key (`2014Q3` under `2008`) does not write `group_key`; a year row that matches the key does. A banner that contradicts every overlapping key is dropped. A year and a month in one header band with one label column are one table (`axis_ids`): rows once, value series per axis. The same column sequence and keys in the next section's header do not open a second axis: the section points at the first (`Output!r6c3`). Section blocks stay distinct. If two sections put different flags on the same period, the axes are not merged. Week and biweek axes are not collapsed when keys repeat.

The same `model_year` or `year` key sequence shares one period grid, even when the columns are shifted or the headers sit on different sheets. Each local axis stays in `axes` with its own id and columns and gets the fullest axis as `timeline_id`. `context.md` prints one table: a row per axis (the column letter under each period key) and the phase rows under the same keys. A value stays in its own column: `Y1` can be column D on one sheet and E on another. Axes stay apart when both have dates and those dates disagree, or when two non-empty flag sets differ. `Y1` and `2024` do not merge. Different sequence lengths do not merge either.

`body_rows` are cut from the **filtered** list, not from the raw header bands. A label-only row whose period columns are empty (no number, text, or formula), with only empty rows between it and the next header, labels that next block, including when that row is hidden: the title stays and `hidden` is set. A row that still has a period value or a formula stays in the block above. A hidden row that has values joins that same block, with `hidden` set on the row. Row visibility comes from the workbook's `hidden_rows`. A visible row whose only text sits in a hidden column is not a hidden row, and that scratch text does not become a section title.

Formula-copy (the same `formula_template` in neighboring columns, length ≥ 3) sets the timeline geometry: headers confirm it. Flags and constants that are not copied along the row are not an axis.

## Label zone

Previously `label_col = min` of the text column: sections in col 2 and articles in col 4 made the body skip silently.

Now each body row takes the leftmost visible text cell left of the axis (not a number, not a period). The histogram of those “left” columns gives `block.label_col` (argmax; the left one wins a tie). A hidden scratch column does not vote, so it does not become `label_col`. `span` is those columns left of primary, plus primary. A hidden row is read in that same span, and its label cell keeps `hidden`.

A row label is the first non-empty cell of the span; `indent` is the position in the span, plus leading spaces, plus the label cell's alignment indent (`styles.xml` `cellXfs/xf/alignment@indent`). Italic text is not an indent. A row outline level (`sheetData/row@outlineLevel`) is read with `summaryBelow` (`sheetPr/outlinePr`, or `sheetFormatPr/outlinePr` when a producer puts the flag there). Level 1 under a level 0 band stays with the indent parent. A deeper level chooses the summary row inside that group when it sits closer than the indent parent: above the detail when `summaryBelow` is false, and below it when `summaryBelow` is true (Excel's default). A column `outlineLevel` is stored on the cell and does not change the row parent. A `LayoutRow` has its own `label_col` (the address in context) when the article is not in `block.label_col`. The word `Total` / `Sum` / `Итого` / `Всего` in the label column is the row name, not a column header; so are `План`, `Факт`, `Budget` and `Base case`. Only a period there (`2024`, `I кв. 2025`) is not a row name. If the span has no label, the first text left of the period columns is used (`CHECK`).

Row kinds (`fact` / `abstract` / `index` / `helper` / `flag`) are as in [architecture.md](architecture.md). `index` only when the axis runs `0|1, 2, 3, …` and the label is a counter (`week`, `#`, …) **or** the period cells have no formulas. The “every number ≤ 12” branch is gone: money 1..12 with formulas stays `fact`. Placeholders `Spare` / `None` are `helper`. Kind `flag` is only a binary series (≥90% of values in `{0, 1}`) or a `flag` token in the label. `Mid case` / `Low case` / `Applied (real terms)` with prices stay `fact`. `Case Number` is a scenario header, not a data row. A selector (`Live Case`, `* choice`) is `flag` with `disposition=excluded`. A scalar left of the ruler (IRR, NPV, `Months per year`) with a numeric role cell and no period values is `fact`, not `abstract`. A row under a `Check` outline stays `helper`.

## Formulas and the axis

Compile marks a formula `unparsed` when a token is not parsed. A range with the sheet qualifier **on the right end** (`SUM(TBA!$D$10:'TBA'!D10)`) is allowed: the sheet prefix after `:` is skipped. Otherwise structure does not see the SUM/edges, and warnings accumulate in context.

Cached values are not required for layout when the header text is already a calendar or a year index.

## Blocks without a period axis

If the sheet has no axis, `detect_params_block` classifies the shape (not a whitelist of sheet names):

| Shape | Result |
| --- | --- |
| A label in the left text column **and** a value column (numbers / dates / short scalars) on ≥3 rows | `block.kind=params` |
| A 2-D grid whose row label is itself a number (`Sensitivity`) | Header row only, `kind=abstract` |
| Numbered rows under a header of ≥3 text captions (`№ п/п`, `Показатель`, a sentence that contains a date) | `params` table. The date stays inside the caption and is not a period key |
| Long prose, Cover, Disclaimer, shortcuts, including a hidden option list with no textual value header | Region dropped, not in completeness |

A params header may be multi-row: scenario names and `Units` / `Values` / `Active` / `Notes` are collected per column. A column is `scenario` only with positive evidence: the caption contains `базов*`, `консервативн*`, `оптимистичн*`, `пессимистичн*`, `стресс*`, `сценари*`, `base`, `upside`, `downside`, `case`, or `scenario`, or it is a short numeric sibling of such a column (the same parameter rows carry numbers, as `Bull` does next to `Base`). Any other short caption is `unknown`. Context adds one line per block, `Unrecognized column headers in {block_id}: «Отрасль», «Периодичность»`. `№ п/п`, `№`, `#`, and `No.` are role `index`, not `unknown`. A long caption, or a numeric column with no caption, stays `value`. A numeric cell is not a header. A label-and-amount list with no header row keeps every row, including the first; the amount column is `value` and its header text is empty. The role name is not written out as header text. A hidden row inside that table joins it with `hidden` set and keeps its value cells. A hidden column that is only an option list, with no visible table around it, does not become a scenario column and does not open a params block. The **active value** column is chosen by the number of cross-sheet dependents in `ir/edges.parquet`, and only when that column already has a textual caption; the header lexicon is a hint. It does not relabel the other columns as scenarios.

A hidden **sheet**, **row**, and **column** are read like visible ones. `SheetLayout.hidden` comes from the workbook metadata. Parse also records `hidden_rows` and `hidden_cols` on each sheet. Context adds one line per fact, with consecutive addresses collapsed: `Hidden sheet {name} is included`, `Hidden rows {name}!17:18,20:22 are included`, `Hidden columns {name}!W:AA are included`. A hidden row or column is listed only when it holds a value or a formula; an empty hidden range, including unused columns through XFD, is silent. A published row, period, and role cell carry `hidden` when that row or column is hidden; the flag is omitted when it is false. `label_col` still follows the visible text column.

A **Scenario Chosen** / selected scenario row is not a column header. It is a control (`kind=flag`). The live-scenario index (often `D3`) stays a block row with `disposition=excluded`; column D is the selected value, F:… is the case matrix. Numbers `1..n` on the selector row do not overwrite axis names (`Base Case`). The selector label does not vote for the `value` column, or `label_col` slides right.

An all-caps row with no values becomes `abstract`. A `Check` / `Result` table becomes `helper` / `check_row` and is published with `disposition=excluded`.

On a sheet **with** an axis, `carve_params_regions` cuts out rows that have no values to the right of the scenario span:

| Region | Result |
| --- | --- |
| A `Case Number` / `Scenario` header and a run of `1..n` (n ≥ 3 and not wider than 60% of the period columns) | `params`: a column labeled like `Live Case` is `value`, columns `1..n` are `scenario` (`Case 1` …). The selector (`Live_case`, `OFFSET`) is a `flag` row with `disposition=excluded` |
| Checks and constants (Reference / Result / a scalar in E/G/H), when there are ≥ 3 fact/helper rows | a separate `params` block |

Those rows do not stay in the timeline block. Scenario columns are not part of the axis.

## Non-period cells on a timeline

Columns between the label zone and `min(period_col)` get roles on `LayoutRow.cells`: `SUM` over the period columns → `total`; unit text (`k£`, `EUR'000`, `RURm`, `RUR`, `mRUB`, `USD bn`, `RUB'000`, `EUR/MWh`, `%`, `x`, `years`, a whole cell `Index`, `MWh p.a.`, `#`, `№`) → `unit`. The label cell of the period-header row (`RURm` beside `2020`) is the unit of the block. A body row whose label is only such a caption (`% p.a.`, `k£`, `EUR'000`, `тыс. руб.`) covers the following rows until the next section. A header `Year` and a line item `Euro` are not that caption. A unit role cell on the row itself wins. A column title `Index` stays a header. `Base Index` is not a unit. A trailing `p.a.` is a period marker on the caption (`MWh p.a.` is count, `EUR p.a.` is money, `% p.a.` is rate); a column with only an end date and no start → `stub`; any other number/formula → `value`. A role cell has `header` — the nearest label in the same column inside the section (`Start`, `End`, `Live Case`, `Min`, `Avg`). `total` and `stub` are not periods of the axis; the graph link for that cell has `period_id=null`. These are scalars such as `Construction!C22`, not a dump of every cell on the sheet.

## What to check when the context has no fact rows

| Symptom | Where to look |
| --- | --- |
| 0 blocks | No calendar axis, no `Year`+`1..n`, no long `1..n` on a formula-copy, and the shape is not params (prose / nav) |
| Many 2-period blocks, 0 fact | False Start/End pairs were not filtered, or labels sit to the right of `label_col` |
| Blocks exist, fact = 0 | Label zone / skip of an empty `label_col` |
| No `D3` / Scenario Chosen among block rows | The control row landed in the params header (`params.py`) |
| `Unparsed formula` on a header or a SUM | `formulas/engine.py` |

Public corpus: Packt (year index + `Input Assumptions` as params) and RVI (calendar + sections in several columns; `Top Shortcuts` produces no rows). `three-statement.xlsx` may be unavailable — a corpus check skips it.

Detector tests: `tests/layout/`. On `cashflow.xlsx`, params blocks appear (Assumptions / Checks); Cover and Disclaimer still have no rows. Timeline `label_col` of the weekly blocks must not slide.
