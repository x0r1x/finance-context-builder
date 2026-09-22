# Граф формул

Публичный граф — два файла с одними и теми же фактами: `graph.json` и `graph.md` (schema `1.7.0`). Cell-level рёбра и AST остаются в `ir/*.parquet`. Модель — `context.json` / `context.md`. Обход одной ячейки — `GET .../graph/trace` и `GET .../graph/trace.md`, не каталог всех ячеек. Граф не вливается в context и не заменяется семантическим графом cell → formula → series → concept.

Код: `src/finance_context/graph/` (`stage.py`, `cycles.py`, `trace.py`), рендер — `src/finance_context/render/graph.py`, развёртка диапазонов — `formulas/csr.py`. Пайплайн: `parse → compile → layout → mapping → graph → build → render`.

Связанные документы: [обзор](overview.md), [архитектура](architecture.md), [маппинг](mapping.md), [срез для LLM](llm.md).

## Владение данными

Один факт — одно место.

| Факт | Где |
| --- | --- |
| Формула, AST, кэш ячейки | `ir/cells.parquet` |
| Ссылки как в формуле (диапазон — одна цель, named ranges) | `ir/edges.parquet` |
| Cell→cell после expand: `dangling` / `dangling_reason` / `status` / `reason` / `evidence` / `range_ref` / `truncated` / `col_offset` / `period_lag` | `ir/cell_edges.parquet` |
| `row_key`, `concept_id`, `period_id`, `node_type` (включая `empty` для проверенных пустых ячеек) | `ir/graph_index.parquet`; у формулы ещё `links[].row_key` и `links[].period_id` |
| Counts (`nodes` / `edges` — размеры cell-level parquet), `iterate`, циклы, `circularity_hints`, `dangling_classes` и `links[]` с `formula_class` | `graph.json` и то же в `graph.md` (schema `1.7.0`, колонка Class) |
| Каждый `<c>` листа: `populated` или `styled_blank` | `raw/cell_presence.parquet` |
| Строка отчёта: лейбл, mapping, одна формула строки, серии кэша по осям, статус значения, scale factor, нормализованные значения, position/aggregation | `context.json` / `context.md` (schema `1.13.0`, оси в `axes`) |

`links[]` — одна запись на ячейку с формулой: `cell`, A1-текст один раз, `formula_class` (`same_period`, `cross_period`, `aggregation`, `rollforward`, `conditional`, `hardcoded`), `refs`, `row_key` строки контекста и `period_id` колонки оси. Оба ключа `null`, если ячейка вне layout. Колонка `total` или `stub` периодом не является, поэтому у её link `period_id=null`, даже если строка лежит в timeline-блоке. `SUM(J9:J12)` остаётся одним диапазоном, не десятками `range_member`. Пустые члены диапазона в `links` не входят. `nodes` и `edges` — не `len(links)`. `context.md` печатает тот же `row_key` и заголовок периода с буквой колонки (`Y23 (AA)`), поэтому строка, ячейка и trace сходятся без parquet.

`context.json` хранит pointer `graph` (счётчики и пути). В нём нет `precedents_rows`, `dependents_rows`, `precedent_cells`, `formula_ast` и второго каталога строк (`inventory` / `unmapped` / `excluded`). Формула строки — один fingerprint. Значения блока с одной осью — плоский массив кэша или `null`; у нескольких осей числа лежат в `rows[].series` по `axis_id`, а плоские `values` повторяют первую серию. Рядом такой же длины `value_statuses` и `normalized_values`, плюс `scale_factor`, `period_position` и `aggregation`. Фазы и `group_key` — в `axes`, не в поле `timeline`. Это не каталог ячеек. Прецеденты среза для отвечающей LLM берутся из `links` / trace (`row_key`, `concept_id`, `period_id`); AST в срез не копируется.

Отдельных `graph-edges.json`, `graph-dangling.json` и `formulas.json` нет. Полнота cell-графа не живёт в `context.blocks[].relations` (там только mapping alias/aggregate/difference/roll_forward).

`dangling_reason` считается по cell-edges и публикуется классом в `dangling_classes`, не списком адресов. Рядом на ребре parquet лежат `status`, `reason`, `evidence`. Пустая ячейка и неразрешённая ссылка не смешиваются.

| class | status | reason | evidence | `dangling` |
| --- | --- | --- | --- | --- |
| `empty_range_member` | `empty` | `actual_blank_cell` | `omitted_by_excel` или `styled_blank` | нет (узел `node_type=empty`) |
| `empty_ref` | `empty` | `actual_blank_cell` | то же для одиночного ref / cross_sheet на разобранном листе | нет |
| `missing_sheet` | `unresolved` | `missing_sheet` | `sheet_not_in_workbook` | да |
| `parser_resolution_failure` | `unresolved` | `parser_resolution_failure` | `populated_missing_from_index` или `bad_address` | да |

`included_in_formula_semantics` у `empty` — `true` (Excel читает пустую ячейку как ноль или член INDEX). У `unresolved` — `"unknown"`.

`omitted_by_excel` — адреса нет в `cell_presence.parquet`, лист разобран. `styled_blank` — в XML есть `<c>` без значения и формулы. Если presence говорит `populated`, а ячейки нет в индексе, это `parser_resolution_failure`, не пустая ячейка.

`unresolved` формулы, `external`, `dynamic` и `truncated` в `dangling_classes` не смешиваются с пустыми ячейками: у них свои счётчики. `graph.dangling.count` — только `status=unresolved`. Счётчик `empty_range_members` остаётся на pointer context и предупреждением не является.

Файла `report.json` нет.

## Циклы

SCC на cell-edges `kind ∈ {ref, cross_sheet, range}` (без unresolved/dangling):

| `class` | Когда |
| --- | --- |
| `iterative_ok` | Все рёбра в компоненте — сдвиг периода (`period_lag` ≠ 0 / `same`), типичный roll-forward |
| `unexpected` | Прочий цикл, в том числе same-period Uses↔Interest |

Пустой `cycles: []` **не** означает, что Excel iterate выключен. `graph.json.iterate` копирует `calcPr/@iterate` из workbook. Если SCC найден, у записи цикла есть `breakers`: cell id членов, чья строка в mapping `excluded` как `technical_bridge` (лейблы вроде *Uses of funds for circularity breakdown*). Если SCC нет, а лейбл содержит `circular` или строка — `technical_bridge`, пишется `circularity_hints[]` (`sheet`, `row`, `label`, `cell_ids`).

## Трассировка

CLI пишет `context.json`, `context.md`, `graph.json` и `graph.md`. HTTP и `scripts/run.sh` (при живом `serve`) качают те же документы в `out/<timestamp>/json/` и `out/<timestamp>/md/`. `scripts/check-graph.py` требует schema графа `1.7` и context `1.13`, ключ `links` и parquet-пути в `artifacts`, запрещает AST и развёрнутый `range_member` в публичном графе, запрещает второй каталог строк, per-cell `source` и слияние в один контракт (`formulas` / `concepts` / `series` / `audit_trail` на верхнем уровне context). `values` остаётся плоским списком; `value_statuses` и `normalized_values` той же длины. Каждая ось из `axes` в Markdown — шапка таблицы `| {axis_id} | период | ... |` (периоды колонками, не строками). Ключи периодов одной оси уникальны. Колонка с role cell `total` / `stub` / `scenario` в ось не входит. Если у периода есть `start_date` / `end_date`, в `context.md` есть строки `Start` / `End`, даты идут `start ≤ end`, и следующий период начинается на следующий день после `end`. Каждая не-unit role-ячейка строки (адрес и кэш) видна в `context.md`. Markdown должен содержать те же блоки, строки, `row_key`, `period_id`, links, класс формулы, а для строки — `empty` / `n/a`, профиль времени и масштаб, если они есть в JSON. Ненулевой `row_key` ссылки должен быть строкой контекста. `--axes-summary` печатает одну строку: число осей, блоков и фазы construction/operation. `scripts/run.sh` пишет её в `summary.txt` и при падении check-graph только предупреждает, код выхода прогона не меняет.

Запись link:

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

`from` — адрес ячейки (`P&L!J13`), `row_key` (`P&L|13|P&L!r2`) или `concept_id` (`pnl.ebitda`). `direction`: `precedents` (к входам) или `dependents` (к результатам). Ответ: адрес, `row_key`, `concept_id`, период, кэш, A1-формула и refs. Без `formula_ast`. Диапазон — одно ребро; пустые члены `INDEX` в узлы не входят. Непустые члены `SUM` обходятся, чтобы глубина доходила до следующих ячеек.

Mapping по-прежнему использует развёрнутые ranges в IR как внутренний сигнал structure.

## Примеры (project finance)

- EBITDA `=SUM(J9:J12)` в `links` — один ref на диапазон. Развёртка по ячейкам остаётся в `ir/cell_edges.parquet`.
- Пустые клетки внутри `INDEX(J8:O8)` — `empty` / `actual_blank_cell` в parquet и счётчик `dangling_classes`, не ошибка парсера и не ref в `links`. Одиночная ссылка на такую же пустую ячейку — `empty_ref`. Trace эти пустые адреса не показывает.
- `P&L` Gross revenues `=Operation!…` с `C[-1]` получает `period_lag` ≠ `same` на cell-edge.
- От `pnl.ebitda` / `cf.cfads` trace доходит до Input Assumptions (traffic, inflation, rates), если формулы так связаны.
