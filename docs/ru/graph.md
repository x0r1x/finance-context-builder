# Граф формул

**Русский** · [English](../en/graph.md)

Публичный граф — два файла с одними и теми же фактами: `graph.json` и `graph.md` (schema `1.7.0`). Cell-level рёбра и AST остаются в `ir/*.parquet`. Модель — `context.json` / `context.md`. Обход одной ячейки — `GET .../graph/trace` и `GET .../graph/trace.md`, не каталог всех ячеек. Граф не вливается в context.

Код: `src/finance_context/graph/` (`stage.py`, `cycles.py`, `trace.py`), рендер — `src/finance_context/render/graph.py`, развёртка диапазонов — `formulas/csr.py`. Пайплайн: `parse → compile → layout → graph → build → render`.

Связанные документы: [обзор](overview.md), [архитектура](architecture.md), [срезы](slices.md).

## Владение данными

Один факт — одно место.

| Факт | Где |
| --- | --- |
| Формула, AST, кэш ячейки | `ir/cells.parquet` |
| Ссылки как в формуле (диапазон — одна цель, named ranges) | `ir/edges.parquet` |
| Cell→cell после expand: `dangling` / `dangling_reason` / `status` / `reason` / `evidence` / `range_ref` / `truncated`. `col_offset` и `period_lag` здесь пустые | `ir/cell_edges.parquet` |
| `col_offset` и `period_lag` после graph. `cell_edges` graph не переписывает | `ir/graph_edges.parquet` |
| `row_key`, `period_id`, `node_type` (включая `empty` для проверенных пустых ячеек) | `ir/graph_index.parquet`; у формулы ещё `links[].row_key` и `links[].period_id` |
| Counts (`nodes` / `edges` — размеры cell-level parquet), `iterate`, циклы, `circularity_hints`, `dangling_classes` и `links[]` с `formula_class` | `graph.json` и то же в `graph.md` (schema `1.7.0`, колонка Class) |
| Каждый `<c>` листа: `populated` или `styled_blank` | `raw/cell_presence.parquet` |
| Строка отчёта: лейбл, одна формула строки, серии кэша по осям, статус значения, scale factor, нормализованные значения | `context.json` / `context.md` (schema `1.14.0`, оси в `axes`) |

`links[]` — одна запись на ячейку с формулой: `cell`, A1-текст один раз, `formula_class` (`same_period`, `cross_period`, `aggregation`, `rollforward`, `conditional`, `hardcoded`), `refs`, `row_key` строки контекста и `period_id` колонки оси. Оба ключа `null`, если ячейка вне layout. Колонка `total` или `stub` периодом не является, поэтому у её link `period_id=null`, даже если строка лежит в timeline-блоке. `SUM(J9:J12)` остаётся одним диапазоном, не десятками `range_member`. Пустые члены диапазона в `links` не входят. `nodes` и `edges` — не `len(links)`. `context.md` печатает в значении периода `[Sheet!A1]` — это `links[].cell`, формула остаётся в link. Заголовок периода по-прежнему содержит букву колонки (`Y23 (AA)`).

`context.json` хранит pointer `graph` (счётчики и пути). В нём нет `precedents_rows`, `dependents_rows`, `precedent_cells`, `formula_ast` и второго каталога строк. Формула строки — один fingerprint. Значения блока с одной осью — плоский массив кэша или `null`; у нескольких осей числа лежат в `rows[].series` по `axis_id`, а плоские `values` повторяют первую серию. Рядом такой же длины `value_statuses` и `normalized_values`, плюс `scale_factor`. `flags` и `group_key` — в `axes`, не в поле `timeline`. Это не каталог ячеек. Прецеденты среза наблюдения берутся из `links` / trace (`row_key`, `period_id`); AST в срез не копируется.

## Узлы-диапазоны

Целый столбец или строка (`A:A`, `1:1`) и прямоугольник больше `RANGE_NODE_MIN_CELLS` ячеек (по умолчанию 64) — узлы-диапазоны. В `ir/cell_edges.parquet` и `ir/graph_edges.parquet` у формулы одно ребро `kind=range`: `target` — id узла (`Data!A:A`, `Data!B2:B5000`, `Data!3:3`; в id ячейки двоеточия нет), `range_ref` — текст из формулы. Больше для узла ничего не пишется: его ячейки берутся из границ и ячеек книги, когда они нужны читателю. Скопированный вниз `SUMIF(A:A, ...)` или нарастающий итог `SUM($B$2:B{n})` стоят одно ребро на формулу, поэтому рёбер столько же, сколько формул, а не формулы × ячейки. Диапазон на отсутствующем листе или на листе без ячеек и диапазон до `RANGE_NODE_MIN_CELLS` ячеек раскрываются в каждой формуле, как раньше, с пустыми членами и обрезкой `RANGE_EXPAND_CAP`.

Что совпадает с раскрытием в каждой формуле и проверено тестами: `links[]` (`formula_class`, `refs`), `cycles` (состав и класс), `dangling`, узлы и рёбра trace. У ребра в узел `col_offset` и `period_lag` первой ячейки узла (угол прямоугольника или первая заполненная ячейка целого столбца или строки) — того ребра, которое раскрытие давало первым. `formula_class` берёт лаги всех колонок узла. Циклы ищутся через узел к формульным ячейкам внутри него, и в класс `iterative_ok` голосуют только рёбра формула → ячейка, которые были бы при раскрытии.

Что меняется: `edges` и `kinds.range` считают одно ребро на формулу и узел; пустые ячейки внутри узла не становятся строками `node_type=empty` в `ir/graph_index.parquet` и не входят в `dangling_classes.empty_range_member` (раскрытие считало их только в первых 2000 ячейках); `truncated` не перечисляет формулы с узлом — узел не обрезается.

Trace проходит узел насквозь и не показывает его как узел. Прецеденты формулы — заполненные ячейки внутри узла, по строкам, затем по колонкам. Обход идёт не дальше `TRACE_RANGE_MEMBERS` из них (по умолчанию 200, 0 — все; эндпоинты trace принимают и `range_members`), и тогда у ребра есть `members_total` и `members_shown`, а `trace.md` перечисляет такие диапазоны в разделе «Ranges not followed in full». Зависимые ячейки — все формулы, читающие узел, в границы которого попадает ячейка; их находит индекс границ узлов. Пустая ячейка внутри узла может быть началом trace.

Отдельных `graph-edges.json`, `graph-dangling.json` и `formulas.json` нет. Полнота cell-графа не живёт в `context.blocks[].relations` (там formula-shape alias, aggregate, difference и roll_forward).

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

Пустой `cycles: []` **не** означает, что Excel iterate выключен. `graph.json.iterate` копирует `calcPr/@iterate` из workbook. Если SCC найден, запись цикла перечисляет `members` и `class`. Список `breakers` пуст. Если SCC нет и лейбл содержит `circular`, пишется `circularity_hints[]` (`sheet`, `row`, `label`, `cell_ids`).

## Трассировка

CLI пишет `context.json`, `context.md`, `graph.json` и `graph.md` в одном процессе. HTTP считает книгу в отдельном процессе; `scripts/run.sh` (при живом `serve`) только качает те же документы в `out/<timestamp>/json/` и `out/<timestamp>/md/` и сам процесс не останавливает. `scripts/check-graph.py` требует schema графа `1.7` и context `1.14`, ключ `links` и parquet-пути в `artifacts`, запрещает AST и развёрнутый `range_member` в публичном графе, запрещает второй каталог строк, per-cell `source` и слияние в один контракт (`formulas` / `concepts` / `series` / `audit_trail` на верхнем уровне context). `values` остаётся плоским списком; `value_statuses` и `normalized_values` той же длины. Каждая ось из `axes` в Markdown — шапка таблицы `| {axis_id} | период | ... |` (периоды колонками, не строками). Ключи периодов одной оси уникальны. Повторенный календарный ключ у колонок с разными маркерами варианта записывается как `<period_key>@<marker>` (`casefold` текста маркера), чтобы индекс сохранил каждую колонку. `period_lag`, порядок периодов и цикл-цепочка считаются по календарному ключу до `@`, поэтому эти колонки делят один слот. `period_lag` — расстояние внутри одного зерна: годы отличаются на год, полугодия на `2*year+half`, кварталы на `4*year+quarter`, месяцы на `12*year+month`, дни — на число дней. Пропущенный период остаётся в этом счёте (`2016Q4` к `2011Q2` это `-22`). Один и тот же календарный ключ, включая варианты `<period>@<marker>`, даёт `same`. Порядковый ключ, который выдаёт разбор оси (`Y<n>`, `Q<n>`, `M<n>`, `P<n>` — из подписи Year / Год / Quarter / Month / Period), — своё зерно, и лаг равен разнице этих чисел (`Y3` к `Y1` это `-2`). Пара разных зёрен, в том числе порядковый ключ против календарного года или против другой буквы, или ключ, который не является периодом (`value`, сценарий), даёт `unaligned`. `classify_formula` пропускает `unaligned`, поэтому такое ребро не является предыдущим шагом и само по себе не становится `rollforward`. Голый `2024` рядом с `2024E` остаётся двумя id (`2024` и `2024@e`). Колонка с role cell `total` / `stub` / `scenario` в ось не входит. Если у периода есть `start_date` / `end_date`, в `context.md` есть строки `Start` / `End`, даты идут `start ≤ end`, и следующий период начинается на следующий день после `end`. Каждая не-unit role-ячейка строки (адрес и кэш) видна в `context.md`. Markdown должен содержать те же блоки, строки, `row_key`, `period_id`, links, класс формулы, а для строки — `empty` / `n/a` и масштаб, если они есть в JSON. Ненулевой `row_key` ссылки должен быть строкой контекста. `--axes-summary` печатает одну строку: число осей, блоков и диапазон периодов. `scripts/run.sh` пишет её в `summary.txt`. Если check-graph падает, скрипт печатает ошибку и завершается с кодом 1.

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

`from` — адрес ячейки (`P&L!J13`), `row_key` (`P&L|13|P&L!r2`) или подпись (`EBITDA`). `direction`: `precedents` (к входам) или `dependents` (к результатам). Ответ: адрес, `row_key`, период, кэш, A1-формула и refs. Без `formula_ast`. Диапазон — одно ребро; пустые члены `INDEX` в узлы не входят. Непустые члены `SUM` обходятся, чтобы глубина доходила до следующих ячеек.

Развёрнутые диапазоны остаются в IR. Публичный link хранит один range ref.

## Примеры (project finance)

- EBITDA `=SUM(J9:J12)` в `links` — один ref на диапазон. Развёртка по ячейкам остаётся в `ir/cell_edges.parquet`.
- Пустые клетки внутри `INDEX(J8:O8)` — `empty` / `actual_blank_cell` в parquet и счётчик `dangling_classes`, не ошибка парсера и не ref в `links`. Одиночная ссылка на такую же пустую ячейку — `empty_ref`. Trace эти пустые адреса не показывает.
- `P&L` Gross revenues `=Operation!…` с `C[-1]` получает `period_lag` ≠ `same` в `ir/graph_edges.parquet`. В `ir/cell_edges.parquet` лаг остаётся пустым.
- От подписей `EBITDA` / `CFADS` trace доходит до Input Assumptions (traffic, inflation, rates), если формулы так связаны.
