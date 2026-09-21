# Граф формул

Cell-level граф — отдельный IR-артефакт, не секция `context.json`. Отчёт описывает строки модели; трассировка Cash Flow / IRR читает parquet и `GET .../graph/trace`.

Код: `src/finance_context/graph/` (`stage.py`, `cycles.py`, `trace.py`), развёртка диапазонов — `formulas/csr.py`. Пайплайн: `parse → compile → layout → mapping → graph → build → render`.

Связанные документы: [обзор](overview.md), [архитектура](architecture.md), [маппинг](mapping.md).

## Владение данными

Один факт — одно место. Join при trace.

| Факт | Файл |
| --- | --- |
| Формула, AST, кэш ячейки | `ir/cells.parquet`; A1-текст расчётных ячеек дублируется в `formulas.json` и в `PeriodValue.formula` |
| Ссылки как в формуле (в т.ч. диапазоны и named ranges) | `ir/edges.parquet` |
| Cell→cell рёбра после expand: `formula_cell` / `precedent`, `formula`, `anchors`, `reference_kind`, `resolution_status`, `period_id`, `dangling` / `dangling_reason` / `truncated`, `col_offset`, `period_lag` | `ir/cell_edges.parquet` (якоря и статус) + самодостаточный `graph-edges.json` |
| `row_key`, `concept_id`, `period_id`, `node_type` (включая materialized `empty` для проверенных пустых ячеек) | `ir/graph_index.parquet` |
| Counts, `iterate`, циклы (`class` / `breakers`), `circularity_hints`, `dangling_classes`, пути к parquet и JSON | `graph.json` (schema `1.4.0`) |
| Полный список пустых и неразрешённых адресов без cap 32: `period_id`, `status`, `reason`, `evidence`, `sources` | `graph-dangling.json` |
| Каждый `<c>` листа: `populated` или `styled_blank` | `raw/cell_presence.parquet` |
| Строка отчёта (лейбл, mapping, числа и A1-формула по периодам) | `context.json` (schema `1.8.0`) |

`context.json` хранит pointer `graph` (счётчики и пути) и **A1-текст** формулы на каждом `values[]` с `has_formula`. В нём нет `precedents_rows`, `dependents_rows`, `precedent_cells` и `formula_ast`.

`graph.json` — сводка: counts и пути. Самодостаточный список рёбер для аудита одной JSON-выгрузки — `graph-edges.json`. На каждом ребре `direction` равен `formula_depends_on_precedent`: `source` и `formula_cell` — ячейка, где лежит формула; `target` и `precedent` — ячейка, на которую формула ссылается. Это не поток данных из примера ревью (там source был прецедентом). AST в summary не копируется; A1-текст формулы дублируется на ребре и остаётся в `formulas.json`. Полнота cell-графа не живёт в `context.blocks[].relations` (там только mapping alias/aggregate/difference/roll_forward). Узел ревью с `formula_ast` — `GET .../graph/trace` или `formulas.json`.

`context.graph` — pointer: счётчики циклов, `iterate` (флаг Excel `calcPr`), `dangling`. Само тело `graph.json` несёт `iterate`, классы SCC и, при необходимости, `breakers` / `circularity_hints`.

`dangling_reason` — класс записи. Рядом на ребре лежат `status`, `reason`, `evidence`. Пустая ячейка и неразрешённая ссылка не смешиваются.

| class | status | reason | evidence | `dangling` |
| --- | --- | --- | --- | --- |
| `empty_range_member` | `empty` | `actual_blank_cell` | `omitted_by_excel` или `styled_blank` | нет (узел `node_type=empty`) |
| `empty_ref` | `empty` | `actual_blank_cell` | то же для одиночного ref / cross_sheet на разобранном листе | нет |
| `missing_sheet` | `unresolved` | `missing_sheet` | `sheet_not_in_workbook` | да |
| `parser_resolution_failure` | `unresolved` | `parser_resolution_failure` | `populated_missing_from_index` или `bad_address` | да |

`included_in_formula_semantics` у `empty` — `true` (Excel читает пустую ячейку как ноль или член INDEX). У `unresolved` — `"unknown"`. `sources[]` — ячейка формулы и A1-диапазон или адрес ссылки; текст формулы остаётся в `formulas.json`.

`omitted_by_excel` — адреса нет в `cell_presence.parquet`, лист разобран. `styled_blank` — в XML есть `<c>` без значения и формулы. Если presence говорит `populated`, а ячейки нет в индексе, это `parser_resolution_failure`, не пустая ячейка.

`unresolved` формулы, `external`, `dynamic` и `truncated` в этот sidecar не попадают: у них свои счётчики. `graph.dangling.count` — только `status=unresolved`. Предупреждение context говорит про неразрешённые цели. Счётчик `empty_range_members` остаётся на pointer и предупреждением не является.

Файла `report.json` нет.

## Циклы

SCC на cell-edges `kind ∈ {ref, cross_sheet, range}` (без unresolved/dangling):

| `class` | Когда |
| --- | --- |
| `iterative_ok` | Все рёбра в компоненте — сдвиг периода (`period_lag` ≠ 0 / `same`), типичный roll-forward |
| `unexpected` | Прочий цикл, в том числе same-period Uses↔Interest |

Пустой `cycles: []` **не** означает, что Excel iterate выключен. `graph.json.iterate` копирует `calcPr/@iterate` из workbook. Если SCC найден, у записи цикла есть `breakers`: cell id членов, чья строка в mapping `excluded` как `technical_bridge` (лейблы вроде *Uses of funds for circularity breakdown*). Если SCC нет, а лейбл содержит `circular` или строка — `technical_bridge`, пишется `circularity_hints[]` (`sheet`, `row`, `label`, `cell_ids`). Это объясняет банковский circularity-bridge без копирования всех рёбер в `context.json`.

## Трассировка

CLI пишет `graph.json`, `graph-edges.json`, `graph-dangling.json` и `formulas.json` рядом с `context.json`. HTTP и `scripts/run.sh` (при живом `serve`) качают те же файлы; `scripts/check-graph.py` разрешает `formula` на `values[]` и на ребре аудита, запрещает `formula_ast` в context/summary и в списке рёбер, требует schema `1.4.x`, ключ `iterate` и `artifacts.edges_json` / `dangling` / `formulas`. С `--edges` проверяет, что у ребра есть `edge_id`, `formula_cell`, `precedent`, `formula`, `anchors`, `reference_kind`, `resolution_status` и что `source` совпадает с `formula_cell.node_id`, а `target` — с `precedent.node_id`. С `--dangling` проверяет `period_id`, `status` / `reason` / `evidence` / `sources`. `status=empty` не может иметь `reason=parser_resolution_failure`.

Ребро аудита (ячейка формулы зависит от прецедента):

```json
{
  "edge_id": "…",
  "direction": "formula_depends_on_precedent",
  "formula_cell": {"sheet": "Amortization", "address": "E8", "period_id": "Y1", "node_id": "Amortization!E8"},
  "precedent": {"sheet": "Construction", "address": "E19", "period_id": "Y1", "node_id": "Construction!E19"},
  "source": "Amortization!E8",
  "target": "Construction!E19",
  "relation_type": "formula_reference",
  "reference_kind": "direct",
  "anchors": {"abs_col": false, "abs_row": false},
  "formula": "=Construction!E19",
  "resolution_status": "resolved"
}
```

`reference_kind`: `direct` (`ref` / `cross_sheet`), `range_member` (член развёрнутого диапазона; у `anchors` ещё `abs_col_end` / `abs_row_end`), `named`, `external`, `dynamic`. `resolution_status`: `resolved`, `empty`, `unresolved`, `truncated`, `external`, `dynamic`.

```bash
bash scripts/run.sh path/to/model.xlsx
# out/<timestamp>/graph.json, graph-edges.json, graph-dangling.json, formulas.json, graph-trace.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.json"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph/edges"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph-dangling.json"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/formulas.json"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph/trace?from=P%26L!C13&direction=precedents&depth=8"
```

`from` — адрес ячейки (`P&L!J13`), `row_key` (`P&L|13|P&L!r2`) или `concept_id` (`pnl.ebitda`). `direction`: `precedents` (к входам) или `dependents` (к результатам). Ответ собирает formula/value/period/concept на лету из parquet.

Mapping по-прежнему использует IR (развёрнутые ranges) как внутренний сигнал structure; это не дублирование в отчёте.

## Примеры (project finance)

- EBITDA `=SUM(J9:J12)` даёт рёбра на **все** члены диапазона, не только первую ячейку.
- Пустые клетки внутри `INDEX(J8:O8)` — `empty` / `actual_blank_cell`, не ошибка парсера. Одиночная ссылка на такую же пустую ячейку — `empty_ref`.
- `P&L` Gross revenues `=Operation!…` с `C[-1]` получает `period_lag` ≠ `same`.
- От `pnl.ebitda` / `cf.cfads` trace доходит до Input Assumptions (traffic, inflation, rates), если формулы так связаны.
