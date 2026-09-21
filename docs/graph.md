# Граф формул

Cell-level граф — отдельный IR-артефакт, не секция `context.json`. Отчёт описывает строки модели; трассировка Cash Flow / IRR читает parquet и `GET .../graph/trace`.

Код: `src/finance_context/graph/` (`stage.py`, `cycles.py`, `trace.py`), развёртка диапазонов — `formulas/csr.py`. Пайплайн: `parse → compile → layout → mapping → graph → build → render`.

Связанные документы: [обзор](overview.md), [архитектура](architecture.md), [маппинг](mapping.md).

## Владение данными

Один факт — одно место. Join при trace.

| Факт | Файл |
| --- | --- |
| Формула, AST, кэш ячейки | `ir/cells.parquet` |
| Ссылки как в формуле (в т.ч. диапазоны и named ranges) | `ir/edges.parquet` |
| Cell→cell рёбра после expand, `dangling` / `truncated`, `col_offset`, `period_lag` | `ir/cell_edges.parquet` |
| `row_key`, `concept_id`, `period_id`, `node_type` | `ir/graph_index.parquet` |
| Counts, циклы, пути к parquet | `graph.json` |
| Строка отчёта (лейбл, mapping, числа по периодам) | `context.json` |

`context.json` хранит только pointer `graph` (счётчики и пути). В нём нет `precedents_rows`, `dependents_rows`, `precedent_cells`, AST и текста формулы в `values[]`.

`graph.json` не копирует формулы, значения и inventory. Узел ревью (`node_id`, `formula_ast`, `dependencies`) — проекция `cells ⋈ graph_index ⋈ cell_edges` в trace.

Файла `report.json` нет.

## Циклы

SCC на cell-edges (без unresolved/dangling):

| `class` | Когда |
| --- | --- |
| `iterative_ok` | Все рёбра в компоненте — сдвиг периода (`period_lag` ≠ 0 / `same`), типичный roll-forward |
| `unexpected` | Прочий цикл, в том числе A1↔B1 в одном периоде |

## Трассировка

CLI пишет `graph.json` рядом с `context.json`. HTTP и `scripts/run.sh` (при живом `serve`) тоже: `run-context-job.sh` качает sidecar, `scripts/check-graph.py` проверяет, что отчёт не дублирует граф.

```bash
bash scripts/run.sh path/to/model.xlsx
# out/<timestamp>/graph.json and graph-trace.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.json"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph/trace?from=P%26L!C13&direction=precedents&depth=8"
```

`from` — адрес ячейки (`P&L!J13`), `row_key` (`P&L|13|P&L!r2`) или `concept_id` (`pnl.ebitda`). `direction`: `precedents` (к входам) или `dependents` (к результатам). Ответ собирает formula/value/period/concept на лету из parquet.

Mapping по-прежнему использует IR (развёрнутые ranges) как внутренний сигнал structure; это не дублирование в отчёте.

## Примеры (project finance)

- EBITDA `=SUM(J9:J12)` даёт рёбра на **все** члены диапазона, не только первую ячейку.
- `P&L` Gross revenues `=Operation!…` с `C[-1]` получает `period_lag` ≠ `same`.
- От `pnl.ebitda` / `cf.cfads` trace доходит до Input Assumptions (traffic, inflation, rates), если формулы так связаны.
