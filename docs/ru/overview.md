# Обзор проекта

[English](../en/overview.md) · **Русский**

**finance-context-builder** — read-only сервис. Он превращает Excel-модели cash-flow (`.xlsx` / `.xlsm`) в версионированные JSON и Markdown.

Формулы не пересчитываются: в артефакты попадают кэшированные значения Excel. Контент полный: каждая строка layout живёт один раз внутри своего блока. JSON и Markdown — одни и те же факты. Ячейки, AST и рёбра не сливаются в один документ: они остаются в `raw/` и `ir/`, а публичные `context` и `graph` описывают строку отчёта и формульные links.

Как запускать, Docker и API: [README](../../README.ru.md). Снимок слоёв: [architecture.md](architecture.md).

| Тема | Документ |
| --- | --- |
| Блоки, оси периодов, подписи строк | [layout.md](layout.md) |
| Граф формул, циклы, trace | [graph.md](graph.md) |
| Одно наблюдение для отвечающей LLM | [llm.md](llm.md) |

## Что на входе и на выходе

На входе книга, формулы которой уже посчитаны. `.xls`, `.xlsb` и зашифрованные файлы отклоняются.

На выход джоба. `raw/`, formula IR (`cells`, `edges`, `cell_edges`, `compile.json`) и `layout.json` лежат в `shared/books/{sha256}/` и одинаковы для любой сессии. Context, graph, `meta.json`, `ir/graph_edges.parquet` и `ir/graph_index.parquet` лежат в `sessions/{session}/jobs/{sha256}/`.

| Артефакт | Содержимое |
| --- | --- |
| `raw/` | Ячейки, формулы, кэш, форматы, `cell_presence.parquet` (`populated` / `styled_blank`) |
| `ir/` | Шаблоны, AST, `edges.parquet` (как написано в формуле), `cell_edges.parquet` (раскрытие формулы, lag пустой), `graph_edges.parquet` (`col_offset` / `period_lag`) и `compile.json` (штамп схемы IR) |
| `layout.json` | Блоки отчёта, оси периодов, виды строк |
| `graph.json` / `graph.md` | Схема `1.7.0`: счётчики parquet на уровне ячеек, `iterate`, циклы (`breakers`) / `circularity_hints`, `dangling_classes` и `links[]` (ячейка, формула A1, `formula_class`, `row_key`, `period_id`, входы; диапазон не раскрывается). В Markdown у links есть колонка Class. AST и cell-edges остаются в `ir/` |
| `context.json` / `context.md` | Схема `1.14.0`: паспорт, `axes` (единственное место фаз, флагов и `group_key`), каждый блок и каждая строка (`row_key`, `disposition`, одна формула, серии кэша по `axis_ids`, `value_statuses`, `scale_factor`, `normalized_values`, `period_position`, `aggregation`). Год, повторённый над месяцами, — `group_key`, не вторая ось. Год и месяц в одной полосе заголовков — одна таблица. Повтор той же шапки в следующей секции ссылается на ту же ось. В Markdown секция `## Axes` печатает ось один раз, периоды — колонками: в шапке id оси и ключи периодов (`\| PF Model!r9 \| 2021 \| 2022 \| 2032 \|`), под ней строки `Start` / `End` (ISO-даты периода, если линейка Start/End есть в книге), `Group` / `Phase` / `Phase year` / `Calendar` / `Flags`. Таблица блока добавляет колонки `Path` (`label_path`) и `Cells` (`L total: -86400`, `G Start: 01.01.2024`). У role-ячейки есть `header`. Колонка Time в Markdown — `time_semantics/period_position/aggregation`. Скаляр и строка params — `instant/instant/none`; статическая ставка — `rate/instant/none`; заполненный годовой поток — `flow/during_period/sum`. Слово `none` — это aggregation. Колонка `total` / `stub` не входит в ось. Пустые строки атрибутов скрыты: год без фазы — только шапка. Регулярная месячная сетка схлопывается в одну колонку на год (`\| Output!r6 \| 2020 \| 2021 \|` и строка `\| Periods \| 2020-01 .. 2020-12 \| ... \|`). JSON не пишет пустую `phase`, пустой `flags` и `calendar_year`, если год уже в ключе. У блока есть `Axes: ...` и одна сетка на серию. Time стоит рядом с Unit. Пустая ячейка — `empty`, период вне фазы — `n/a`, тысячи печатаются как `k£ ×1000` и `1.5 (1500)`. Заголовок периода — `period_key` и буква колонки (`Y23 (AA)`). Значение периода показывает кэш и `[Sheet!A1]`. Под блоком `relations` используют тот же `row_key`. Второго каталога строк и реестра ячеек нет. В шапке одна строка полноты контента. `graph` — указатель |

Каталог, паспорт и наблюдения — HTTP-проекции, не файлы. Схемы: `catalog-2`, `summary-2`, `observation-2`. Подробности: [llm.md](llm.md).

Formula IR и layout той же книги общие: `$DATA_DIR/shared/books/{sha256}/`.

## Пайплайн

`parse → compile → layout → graph → build → render`.

Паспорт, каталог и наблюдения в эту последовательность не входят. Они читаются из уже записанных `context.json` и `graph.json`.

HTTP поднимает отдельный процесс на каждую новую книгу и дальше читает `meta.json`. CLI `build` остаётся одним процессом. PyArrow пишет parquet: это кэш compile и вход trace после выхода процесса. Внутри одного прогона стадии передают уже собранные списки строк. Повторный POST готовой книги возвращает снимок, когда `publisher` в `meta.json` совпадает с кодом на диске. Другой `publisher` останавливает процесс этой книги и стартует новый; пустой штамп не останавливает. Новый прогон удаляет только хвост устаревших стадий из `meta.stages` или от layout вниз, если карты нет. Джоба, оставшаяся в `queued` или `running` через рестарт, становится `failed` с `error` `process_lost`. `scripts/run.sh` только опрашивает HTTP. Его `JOB_TIMEOUT_SEC` по умолчанию 300 секунд и процесс не останавливает; сервер останавливает процесс по своему `JOB_TIMEOUT_SEC` (3600 в `.env.example`).

Если детектор не собрал ни timeline, ни params (проза / навигация) или не нашёл подписей статей, fact-строк нет: статус может быть `succeeded` с пустым context. Это layout. Подробности: [layout.md](layout.md).

Заголовки секций (`abstract`) и счётчики (`index`) **не теряются**: они строки своего блока. У обычной строки, включая abstract-заголовок, `disposition` равен null. У flag, helper, check, noise и technical bridge `disposition=excluded` и есть `exclusion_reason`. Значения по оси — массив на строке `fact` / `flag` / `helper`; у params — роли колонок и значения только в value-колонках. Формула строки одна. Cell-level adjacency и AST — в IR / [graph.md](graph.md). Даунстрим видит лейбл, `label_path`, соседей по формульным ссылкам строк, формулу, hints и кэш.

Markdown пишет **полноту контента** как `1.00 (rows/rows layout rows)`: каждая строка layout опубликованного блока есть в документе.

## Статусы джоба

| Статус | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Документы готовы |
| `failed` | Ошибка пайплайна или процесс книги остановлен серверным `JOB_TIMEOUT_SEC` (`error` = `TimeoutError`). Годного context нет |

Старые снимки могут ещё говорить `needs_input` или `degraded`. Такая джоба уже закончена, те же четыре документа отдаются.

## Что записано в строке

Hints берутся из подписи и ячейки единицы: `nature`, `time_semantics` (`flow` / `bop` / `eop` / `rate` / `stock` / `instant`), `unit` (`money` / `count` / `rate` / `ratio` / `years` / `price` / `date`), `unit_per`, `currency` (GBP/EUR/USD/RUB), `scale` (`unit` / `k` / `m` / `bn`) и `sign` (`inflow` / `outflow` / `stock`). `k£` в лейбле или ячейке Units — money, GBP и `k`.

`disposition` — только структурное исключение. Он равен `excluded`, когда причина `flag`, `helper`, `check`, `noise` или `technical_bridge`. Иначе `disposition` и `exclusion_reason` равны null. Relations блока по форме формулы — `alias`, `aggregate`, `difference` и `roll_forward`. Это не второй каталог строки.
