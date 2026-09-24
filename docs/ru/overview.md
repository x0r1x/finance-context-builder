# Обзор проекта

**Русский** · [English](../en/overview.md)

**finance-context-builder** — read-only сервис, который превращает Excel-модели cash-flow (`.xlsx` / `.xlsm`) в версионированный JSON и Markdown-контекст для людей и LLM.

Формулы не пересчитываются: в артефакты попадают кэшированные значения Excel. Контент полный: каждая строка layout живёт один раз внутри своего блока. Таксономия — аннотация `concept_id`, не воронка: неверный тег хуже, чем `unknown`. Unknown-строка остаётся в блоке с иерархией подписи, соседями, формулой и top-3 кандидатами. JSON и Markdown — одни и те же факты. Ячейки, AST и рёбра не сливаются в один документ: они остаются в `raw/` и `ir/`, а публичные `context` и `graph` описывают строку отчёта и формульные links.

Запуск, Docker и API — в [README](../../README.ru.md). Технический снимок слоёв — в [architecture.md](architecture.md).

| Тема | Документ |
| --- | --- |
| Блоки, оси периодов, лейблы строк | [layout.md](layout.md) |
| Каскад маппинга, сигналы, пороги, glossary | [mapping.md](mapping.md) |
| Таксономия: поля, семейства id, как добавлять смысл | [taxonomy.md](taxonomy.md) |
| Граф формул, циклы, trace | [graph.md](graph.md) |
| Срез одного наблюдения для отвечающей LLM | [llm.md](llm.md) |
| Разбор `unknown` / `unmapped.json` после прогона | [review.md](review.md) |

## Что на входе и на выходе

На вход — рабочая книга с уже посчитанными формулами. Отклоняются `.xls`, `.xlsb` и зашифрованные файлы.

На выход джоба:

| Артефакт | Содержание |
| --- | --- |
| `raw/` | Ячейки, формулы, кэш, форматы, `cell_presence.parquet` (`populated` / `styled_blank`) |
| `ir/` | Шаблоны, AST, `edges.parquet` (как в формуле), `cell_edges.parquet` (развёртка формул, лаг пустой), `graph_edges.parquet` (`col_offset` / `period_lag`) и `compile.json` (штамп схемы IR) |
| `layout.json` | Блоки отчётов, оси периодов, виды строк |
| `mapping.json` | Связь fact/flag/helper-строк с `concept_id` или отказ |
| `graph.json` / `graph.md` | Схема `1.7.0`: counts cell-level parquet, `iterate`, циклы (`breakers`) / `circularity_hints`, `dangling_classes` и `links[]` (ячейка, A1-формула, `formula_class`, `row_key`, `period_id`, входы; диапазон не развёрнут). В Markdown у links есть колонка Class. AST и cell-edges остаются в `ir/` |
| `context.json` / `context.md` | Схема `1.13.0`: паспорт, `axes` (единственное место фаз, флагов и `group_key`), каждый блок и каждая строка (`row_key`, `disposition`, `concept_id`, одна формула, серии кэша по `axis_ids`, `value_statuses`, `scale_factor`, `normalized_values`, `period_position`, `aggregation`). Год, повторённый над месяцами, — `group_key`, не вторая ось. Год и месяц в одной полосе заголовков — одна таблица. Повтор той же шапки в следующей секции ссылается на ту же ось. В Markdown секция `## Axes` печатает ось один раз, периоды — колонками: в шапке id оси и ключи периодов (`\| PF Model!r9 \| 2021 \| 2022 \| 2032 \|`), под ней строки `Start` / `End` (ISO-даты периода, если линейка Start/End есть в книге), `Group` / `Phase` / `Phase year` / `Calendar` / `Flags`. Таблица блока добавляет колонки `Path` (`label_path`) и `Cells` (`L total: -86400`, `G Start: 01.01.2024`). Ячейка периода показывает кэш и `[Sheet!A1]`. У role-ячейки есть `header`. Скаляр и params-строка — `instant` / `none`. Колонка `total` / `stub` в ось не входит. Пустые строки атрибутов скрыты: у года без фазы остаётся одна шапка. Ровная месячная сетка сжимается до колонки на год (`\| Output!r6 \| 2020 \| 2021 \|` и строка `\| Periods \| 2020-01 .. 2020-12 \| ... \|`). В JSON у периода нет пустой `phase`, пустого `flags` и `calendar_year`, если год уже в ключе. У таблицы `Axes: ...` и сетка по каждой серии. Рядом с Unit стоит Time (`flow/during_period/sum`), пустая ячейка — `empty`, период вне фазы — `n/a`, тысячи показываются как `k£ ×1000` и `1.5 (1500)`. Заголовок периода — `period_key` и буква колонки (`Y23 (AA)`). Под блоком список `relations` теми же `row_key`. Без `inventory` / `unmapped` / `excluded` и без реестра ячеек. `mapping_stats` (`concept_coverage` и `mapping_quality`), pointer `graph` |
| `unmapped.json` | Abstained-строки из `blocks[].rows` (после `run.sh`, в корне прогона) |

Отвечающей LLM отдают срез одного наблюдения из этих артефактов, а не второй JSON джобы: [llm.md](llm.md).

Между джобами: `$DATA_DIR/glossary.json` (выученные high-confidence пары) и `taxonomy_embeddings.npz` (кэш эмбеддингов концептов).

## Пайплайн

`parse → compile → layout → mapping → graph → build → render`.

HTTP поднимает на каждую новую книгу отдельный процесс и дальше читает `meta.json`. CLI `build` остаётся одним процессом. Parquet пишет PyArrow: это кэш compile и вход trace после выхода процесса. Внутри одного прогона стадии передают уже собранные списки строк. Повторный POST готовой книги отдаёт снимок, если `publisher` в `meta.json` совпадает с кодом на диске. Другой или пустой `publisher` останавливает процесс этой книги и запускает новый. `scripts/run.sh` только опрашивает HTTP. Его `JOB_TIMEOUT_SEC` по умолчанию 300 секунд и процесс не останавливает; сервер останавливает процесс по своему `JOB_TIMEOUT_SEC` (в `.env.example` это 3600).

Каскад маппинга резолвит `fact` / `flag` / `helper`. Если детектор не собрал ни timeline, ни params (проза / навигация) или не нашёл лейблы статей, fact-строк нет: статус может быть `succeeded` при пустом контексте — это layout, не таксономия. Подробности: [layout.md](layout.md).

Заголовки секций (`abstract`) и счётчики (`index`) **не теряются**: они строки своего блока (`disposition=header` у abstract). Значения по оси — массив на строке `fact` / `flag` / `helper`; у params — роли колонок и значения только в value-колонках. Формула строки одна. Cell-level adjacency и AST — в IR / [graph.md](graph.md). Даунстрим видит лейбл, `label_path`, соседей ±2, формулу, hints, кандидатов и кэш. Пустой `unmapped.json` не значит, что у каждой строки блока есть `concept_id`.

Опциональны embeddings и chat (OpenAI-совместимый endpoint, по умолчанию LM Studio). Без них остаются structure + lexical + glossary, затем `unknown` с кандидатами.

Две метрики в отчёте не смешивать: **content completeness** должна быть 100% (число строк блоков равно числу layout-строк принятых блоков); **concept coverage** может быть ниже 100% честно (phasing 0.2/0.8 остаётся unknown).

## Статусы джоба

| Статус | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Нет открытых mapping-вопросов |
| `needs_input` | Контекст готов; часть fact-строк осталась `unknown` |
| `degraded` | Как `needs_input`, но LLM и embeddings не настроены |
| `failed` | Ошибка пайплайна или процесс книги остановлен по серверному `JOB_TIMEOUT_SEC` (`error` = `TimeoutError`). Usable context нет |

`needs_input` — не падение: `context.json`, `context.md`, `graph.json` и `graph.md` всё равно отдаются.

## Два рычага покрытия

1. **Таксономия** (`src/finance_context/ontology/taxonomy.yaml`) — словарь смыслов, не решатель. Правится, когда появляется **новое** финансовое значение.
2. **Каскад сигналов** — признаки (лейбл, секция, соседи, форма формулы, граф). Новый тип совпадения — новый `Signal`, не широкий `anti_labels` и не ветка «если лист = Cash_Receipts».

Выученный glossary не заменяет yaml: он запоминает уже уверенные пары `(лейбл, родитель) → concept_id` для следующих книг.
