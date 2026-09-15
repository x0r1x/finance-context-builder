# Обзор проекта

**finance-context-builder** — read-only сервис, который превращает Excel-модели cash-flow (`.xlsx` / `.xlsm`) в версионированный JSON и Markdown-контекст для людей и LLM.

Формулы не пересчитываются: в артефакты попадают кэшированные значения Excel. Строки фактов связываются с небольшой финансовой таксономией **с правом отказаться**: неверный тег хуже, чем `unknown`.

Запуск, Docker и API — в [README](../README.md). Технический снимок слоёв — в [architecture.md](architecture.md) (EN).

| Тема | Документ |
| --- | --- |
| Каскад маппинга, сигналы, пороги, glossary | [mapping.md](mapping.md) |
| Таксономия: поля, семейства id, как добавлять смысл | [taxonomy.md](taxonomy.md) |
| Разбор `unknown` / `unmapped.json` после прогона | [review.md](review.md) |

## Что на входе и на выходе

На вход — рабочая книга с уже посчитанными формулами. Отклоняются `.xls`, `.xlsb` и зашифрованные файлы.

На выход джоба:

| Артефакт | Содержание |
| --- | --- |
| `raw/` | Ячейки, формулы, кэш, форматы |
| `ir/` | Шаблоны формул, AST, рёбра зависимостей |
| `layout.json` | Блоки отчётов, оси периодов, виды строк |
| `mapping.json` | Связь fact-строк с `concept_id` или отказ |
| `context.json` | Канонический документ с provenance `Sheet!A1` |
| `context.md` | Таблицы периодов; несмапленное Concept = `unknown` |
| `unmapped.json` | Компактный список abstained-строк (после `run.sh`) |

Между джобами: `$DATA_DIR/glossary.json` (выученные high-confidence пары) и `taxonomy_embeddings.npz` (кэш эмбеддингов концептов).

## Пайплайн

`parse → compile → layout → mapping → build → render`.

Маппинг вызывается только для строк layout с `kind=fact`. Заголовки секций (`abstract`), счётчики недель (`index`) и helper-строки в метрики не попадают. LLM видит лейблы, путь секции и заголовки периодов — не числа модели.

Опциональны embeddings и chat (OpenAI-совместимый endpoint, по умолчанию LM Studio). Без них остаются structure + lexical + glossary, затем `unknown`.

## Статусы джоба

| Статус | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Нет открытых mapping-вопросов |
| `needs_input` | Контекст готов; часть fact-строк осталась `unknown` |
| `degraded` | Как `needs_input`, но LLM и embeddings не настроены |
| `failed` | Ошибка пайплайна, usable context нет |

`needs_input` — не падение: `context.json` и `context.md` всё равно отдаются.

## Два рычага покрытия

1. **Таксономия** (`src/finance_context/ontology/taxonomy.yaml`) — канонические смыслы. Правится, когда появляется **новое** финансовое значение.
2. **Каскад сигналов** — как строка книги находится этот смысл. Новый тип совпадения — новый `Signal`, не ветка «если лист = Cash_Receipts».

Выученный glossary не заменяет yaml: он запоминает уже уверенные пары `(лейбл, родитель) → concept_id` для следующих книг.
