# Разбор несмапленных строк

Сначала убедиться, что layout вообще отдал fact-строки. `succeeded` + `Unmapped: 0` + пустой `context.md` почти всегда значит: нет блоков или нет лейблов статей, а не «таксономия покрыла всё». Чеклист — [layout.md](layout.md).

После прогона с ненулевым числом fact смотрят отказ **и** полный контент. Пустой `unmapped.json` **не** значит, что у каждой строки блока есть `concept_id`: заголовки (`kind=abstract`, `disposition=header`) и excluded не попадают в выжимку. `mapping_stats.concept_coverage` — доля annotatable facts с принятым `concept_id`. Семантику, единицы, время и формулы смотрят в `mapping_stats.mapping_quality`, не в длине `unmapped.json` и не в `concept_coverage`.

| Где | Что видно |
| --- | --- |
| `context.md` шапка | `Content completeness` (должно быть 1.00), `Concept coverage` (доля принятых слотов, может быть < 1) и шесть полей `mapping_quality` |
| `context.md` блок | Каждая строка: `Row` (`row_key`), Kind, Disposition, Concept (`unknown` у abstain), формула и все значения оси. Заголовок периода — `Y23 (AA)` (ключ и буква колонки). Под таблицей `relations` с теми же `row_key`. Пустая формула у строки, на которую есть ссылка в `graph.md`, — дефект. Params: `## Parameters / {sheet}` |
| `context.json` → `blocks[].rows` | Все kind, включая abstract; `disposition`, role-tagged `cells`, ряд `values`; инвариант полноты |
| `context.json` → `mapping_stats` | `inventory_rows`, `mapped`, `abstained`, `excluded`, `abstract`, `unmapped_series`, completeness, `concept_coverage`, `mapping_quality` |
| `context.json` → `graph` | Pointer на `graph.json` / parquet, counts, `empty_range_members` |
| `graph.json` / `graph.md` + `GET .../graph/trace` | Сводка и formula-level `links` (`cell`, A1, `row_key`, `period_id`; диапазон одной ссылкой). `row_key` ссылки есть в строке `context.md`. Cell-level рёбра и AST — в `ir/*.parquet` |
| `unmapped.json` | Abstained-строки **без** `values` |

`scripts/extract-unmapped.py` (его вызывает `scripts/run.sh`) берёт `blocks[].rows` из context (или `rows` из mapping), оставляет `disposition=abstained` и выкидывает ряды значений. Счётчик должен совпадать с числом `unknown` в таблицах блоков Markdown.

Excluded (check / helper / flag / technical) в `unmapped.json` не входят — они те же строки блока с `disposition=excluded`.

## Цикл правки

1. Прогнать книгу (`uv run finance-context build …` или `bash scripts/run.sh path/to/model.xlsx` при живом `serve`). `run.sh` кладёт документы в `out/<run>/json/` и `out/<run>/md/` (`context`, `graph`, `trace`) и `unmapped.json` в корень прогона.
2. Открыть `unmapped.json` и ту же строку в `blocks[].rows` / таблице блока: `label`, `parent_label`, `label_path`, `neighbors`, `candidates`, `hints`, `sheet`, `disposition`, `exclusion_reason`, `article_role`, `cells`, `unit`, `formula`. Влияние на CFS — `GET .../graph/trace` ([graph.md](graph.md)).
3. Для каждой строки решить класс:

| Класс | Действие |
| --- | --- |
| Новое финансовое значение | Концепт в [taxonomy.yaml](taxonomy.md) + gold |
| Тот же смысл, другой лейбл / секция | `labels` / `aliases` / `section_hints` / `skip_concept` / `unless`; не широкий `anti_labels` |
| Ребёнок под CAPEX/OPEX/Revenue, но это годы / MW / индекс | `unless` на parent-rollup + `facets.unit`, не money-id родителя |
| Соседи и граф уже намекают (lease рядом с opex) | Это structure-признак; не клеить alias ставки |
| Однозначная формула (alias, SUM) | Проверить structure: SUM копирует концепт, только если замаплены все дети |
| Пустой прогон, нули в metrics, completeness < 1 | Layout / build, не yaml. Cover и Shortcuts в знаменатель не входят; Input Assumptions должен быть params |
| Технический мост, check, шум | Exclusion; не плодить концепт |
| Реальная неоднозначность | Оставить `unknown` (`no_candidate` / `ambiguous` / `low_score`); кандидаты уже в JSON |

4. Обновить gold: `tests/fixtures/mapping/cashflow_dispositions.yaml` для эталонной `cashflow.xlsx`; для корпуса — `packt_project_finance_dispositions.yaml` / `rvi_project_finance_dispositions.yaml` (в т.ч. `forbidden_concept_id`).
5. `uv run pytest` и при необходимости повторный прогон — `unmapped.json` должен сжаться только за счёт честных mapped, не за счёт exclude. Completeness при этом остаётся 1.0.

## Как читать `exclusion_reason` у abstained

| Код | Типичный фикс |
| --- | --- |
| `no_candidate` | Нет id в yaml или lexical/hints не пускают фразу |
| `low_score` | Фраза слишком общая; усилить labels или structure, не порог |
| `ambiguous` | Два концепта рядом — развести hints/anti или уточнить broader |
| `facet_mismatch` | `unit` / anti_labels / фасеты отрезали единственного кандидата (в т.ч. ложный `statement=cov` у не-DSCR строки) |
| `calculation_conflict` | Итог по формуле не совпал с `calculations` в yaml |

## Пример

На прогоне вроде `out/<timestamp>/` остаток unknown при живой таксономии — нормален, если это не ложный high-тег:

- доли строительства 0.2/0.8 (phasing) — не flag (только 0/1) и не финансовый факт;
- строки, для которых нет стабильного id (не клеить к ближайшему money).

Их либо заводят как новый концепт (если смысл повторяется между книгами), либо оставляют `unknown`. Ложный `pnl.opex` на Operating lifetime хуже, чем unknown.

Markdown повторяет JSON: все колонки оси и все строки блока. Обрезки таблиц нет.

## Команды

```bash
uv run python scripts/extract-unmapped.py out/<run>/json/context.json -o out/<run>/unmapped.json
uv run python scripts/extract-unmapped.py data/jobs/<job-id>/mapping.json
uv run pytest tests/test_extract_unmapped.py tests/eval/test_cashflow_dispositions.py tests/eval/test_corpus_dispositions.py
```
