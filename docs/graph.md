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
| Cell→cell рёбра после expand, `dangling` / `dangling_reason` / `truncated`, `col_offset`, `period_lag` | `ir/cell_edges.parquet` + выгрузка `graph-edges.json` |
| `row_key`, `concept_id`, `period_id`, `node_type` (включая materialized `empty` для дыр диапазона) | `ir/graph_index.parquet` |
| Counts, циклы, `dangling_classes`, пути к parquet и JSON | `graph.json` (schema `1.1.0`) |
| Полный список дыр без cap 32 | `graph-dangling.json` |
| Строка отчёта (лейбл, mapping, числа и A1-формула по периодам) | `context.json` (schema `1.6.0`) |

`context.json` хранит pointer `graph` (счётчики и пути) и **A1-текст** формулы на каждом `values[]` с `has_formula`. В нём нет `precedents_rows`, `dependents_rows`, `precedent_cells` и `formula_ast`.

`graph.json` — сводка: source of truth для рёбер остаётся parquet; JSON-список рёбер — `graph-edges.json` для аудита. Формулы, AST и inventory в summary не копируются. Узел ревью (`node_id`, `formula_ast`) — `GET .../graph/trace` или `formulas.json`.

Класс `dangling_reason`:

| class | Когда | `dangling` |
| --- | --- | --- |
| `empty_range_member` | `kind=range`, лист есть, ячейки нет в sparse parse | нет (узел `node_type=empty`) |
| `missing_cell` | одиночный ref / cross_sheet, лист есть, ячейки нет | да |
| `missing_sheet` | листа нет в workbook | да |

`unresolved` / `external` / `dynamic` / `truncated` в dangling не попадают. После материализации пустых членов диапазона `graph.dangling.count` — только missing_cell / missing_sheet. Пустые INDEX/SUM-дыры пишутся в `dangling_classes.empty_range_member` и в `graph-dangling.json`.

Файла `report.json` нет.

## Циклы

SCC на cell-edges (без unresolved/dangling):

| `class` | Когда |
| --- | --- |
| `iterative_ok` | Все рёбра в компоненте — сдвиг периода (`period_lag` ≠ 0 / `same`), типичный roll-forward |
| `unexpected` | Прочий цикл, в том числе A1↔B1 в одном периоде |

## Трассировка

CLI пишет `graph.json`, `graph-edges.json`, `graph-dangling.json` и `formulas.json` рядом с `context.json`. HTTP и `scripts/run.sh` (при живом `serve`) качают те же файлы; `scripts/check-graph.py` разрешает `formula` на `values[]`, запрещает `formula_ast` в context/summary, требует schema `1.1.x` и `artifacts.edges_json` / `dangling` / `formulas`, и с `--edges` / `--dangling` / `--formulas` проверяет, что sidecar’ы не обрезаны и не содержат формул в списке рёбер.

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
- Пустые клетки внутри `INDEX(J8:O8)` — `empty_range_member`, не ошибка парсера.
- `P&L` Gross revenues `=Operation!…` с `C[-1]` получает `period_lag` ≠ `same`.
- От `pnl.ebitda` / `cf.cfads` trace доходит до Input Assumptions (traffic, inflation, rates), если формулы так связаны.
