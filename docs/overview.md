# Обзор проекта

**finance-context-builder** — read-only сервис, который превращает Excel-модели cash-flow (`.xlsx` / `.xlsm`) в версионированный JSON и Markdown-контекст для людей и LLM.

Формулы не пересчитываются: в артефакты попадают кэшированные значения Excel. Контент полный: в `context.json` есть **все** строки layout (`inventory`). Таксономия — аннотация `concept_id`, не воронка: неверный тег хуже, чем `unknown`. Unknown-строка остаётся в контексте с иерархией подписи, соседями, формулой и top-3 кандидатами.

Запуск, Docker и API — в [README](../README.md). Технический снимок слоёв — в [architecture.md](architecture.md) (EN).

| Тема | Документ |
| --- | --- |
| Блоки, оси периодов, лейблы строк | [layout.md](layout.md) |
| Каскад маппинга, сигналы, пороги, glossary | [mapping.md](mapping.md) |
| Таксономия: поля, семейства id, как добавлять смысл | [taxonomy.md](taxonomy.md) |
| Cell-level граф формул, циклы, trace | [graph.md](graph.md) |
| Разбор `unknown` / `unmapped.json` после прогона | [review.md](review.md) |

## Что на входе и на выходе

На вход — рабочая книга с уже посчитанными формулами. Отклоняются `.xls`, `.xlsb` и зашифрованные файлы.

На выход джоба:

| Артефакт | Содержание |
| --- | --- |
| `raw/` | Ячейки, формулы, кэш, форматы |
| `ir/` | Шаблоны, AST, `edges.parquet` (как в формуле) и `cell_edges.parquet` (развёрнутые ячейки) |
| `layout.json` | Блоки отчётов, оси периодов, виды строк |
| `mapping.json` | Связь fact/flag/helper-строк с `concept_id` или отказ |
| `graph.json` | Сводка графа schema `1.2.0`: counts, `iterate`, циклы (`breakers`) / `circularity_hints`, `dangling_classes`, пути к parquet и JSON (без формул и полного списка рёбер) |
| `graph-edges.json` | Полный список cell→cell рёбер для JSON-аудита |
| `graph-dangling.json` | Все дыры без cap 32, с `class` (`empty_range_member` / `missing_cell` / `missing_sheet`) |
| `formulas.json` | A1, template и AST только для ячеек с формулой |
| `context.json` | Схема `1.6.0`: `timeline`, блоки с `values` (кэш + A1 `formula` + адрес), `unmapped`, `excluded`, полный `inventory`, `mapping_stats`, pointer `graph` (`iterate` + counts), `context_role` / `secondary_concepts`, hints `unit`/`currency`/`scale`/`sign` |
| `context.md` | Две метрики в шапке; `## Timeline`; таблицы периодов; `## Parameters / {sheet}` (включая выбранный сценарий); `## Excluded`; row navigator без значений |
| `unmapped.json` | Компактный список abstained-строк (после `run.sh`) |

Между джобами: `$DATA_DIR/glossary.json` (выученные high-confidence пары) и `taxonomy_embeddings.npz` (кэш эмбеддингов концептов).

## Пайплайн

`parse → compile → layout → mapping → graph → build → render`.

Каскад маппинга резолвит `fact` / `flag` / `helper`. Если детектор не собрал ни timeline, ни params (проза / навигация) или не нашёл лейблы статей, fact-строк нет: статус может быть `succeeded` при пустом контексте — это layout, не таксономия. Подробности: [layout.md](layout.md).

Заголовки секций (`abstract`) и счётчики (`index`) **не теряются**: они в `inventory` без периодных рядов. Значения периодной оси — у `fact` / `flag` / `helper` timeline-блоков; у params — role-tagged ячейки (`value` / `unit` / `scenario` / `total` / `note`). Формулы и adjacency — в IR / [graph.md](graph.md), не в inventory. Даунстрим в отчёте видит лейбл, `label_path`, соседей ±2, отпечаток формулы, A1-текст на периодных ячейках, hints, кандидатов и кэш по периодам. AST и полный граф — в IR / [graph.md](graph.md).

Опциональны embeddings и chat (OpenAI-совместимый endpoint, по умолчанию LM Studio). Без них остаются structure + lexical + glossary, затем `unknown` с кандидатами.

Две метрики в отчёте не смешивать: **content completeness** должна быть 100% (`len(inventory) ==` число layout-строк); **concept coverage** может быть ниже 100% честно (phasing 0.2/0.8 остаётся unknown).

## Статусы джоба

| Статус | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Нет открытых mapping-вопросов |
| `needs_input` | Контекст готов; часть fact-строк осталась `unknown` |
| `degraded` | Как `needs_input`, но LLM и embeddings не настроены |
| `failed` | Ошибка пайплайна, usable context нет |

`needs_input` — не падение: `context.json`, sidecar’ы графа и `context.md` всё равно отдаются.

## Два рычага покрытия

1. **Таксономия** (`src/finance_context/ontology/taxonomy.yaml`) — словарь смыслов, не решатель. Правится, когда появляется **новое** финансовое значение.
2. **Каскад сигналов** — признаки (лейбл, секция, соседи, форма формулы, граф). Новый тип совпадения — новый `Signal`, не широкий `anti_labels` и не ветка «если лист = Cash_Receipts».

Выученный glossary не заменяет yaml: он запоминает уже уверенные пары `(лейбл, родитель) → concept_id` для следующих книг.
