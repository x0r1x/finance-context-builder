# Разбор несмапленных строк

Сначала убедиться, что layout вообще отдал fact-строки. `succeeded` + `Unmapped: 0` + пустой `context.md` почти всегда значит: нет блоков или нет лейблов статей, а не «таксономия покрыла всё». Чеклист — [layout.md](layout.md).

После прогона с ненулевым числом fact смотрят отказ **и** полный контент. Пустой `unmapped` / `unmapped.json` **не** значит, что у каждой строки `inventory` есть `concept_id`: заголовки (`kind=abstract`, `disposition=header`) и excluded не попадают в `unmapped`. Полнота семантики — `mapping_stats.concept_coverage` (annotatable facts), не длина массива `unmapped`.

| Где | Что видно |
| --- | --- |
| `context.md` шапка | `Content completeness` (должно быть 1.00) и `Concept coverage` (может быть < 1) |
| `context.md` блок | Timeline: строка с Concept `unknown` (плюс счётчик `Unmapped: N`). Params: `## Parameters / {sheet}` |
| `context.md` `## Excluded` | Helper / flag / check |
| `context.md` `## Row navigator / {sheet}` | Все layout-строки: kind, path, concept, formula fingerprint; без периодных значений и без row-graph refs |
| `context.json` → `mapping_stats` | `inventory_rows`, `mapped`, `abstained`, `excluded`, `abstract`, `unmapped_series`, completeness, coverage |
| `context.json` → `unmapped` | Серии с `values` по периодам (кэш + адрес) или `cells` (params), `candidates`, `hints`, `neighbors`. Это abstained fact-серии, не «inventory без concept_id» |
| `context.json` → `inventory` | Все kind, включая abstract; role-tagged `cells`; инвариант полноты |
| `context.json` → `graph` | Pointer на `graph.json` / parquet, counts и циклы |
| `graph.json` + `GET .../graph/trace` | Cell-level зависимости, `period_lag`, циклы; формулы из `ir/cells.parquet` |
| `unmapped.json` | Та же выжимка атрибутов **без** `values`, плюс `ref` как в колонке Ref |

`scripts/extract-unmapped.py` (его вызывает `scripts/run.sh`) берёт `unmapped` из context или `rows` из mapping, оставляет `concept_id is null` и `disposition != excluded`, выкидывает ряды значений. Счётчик должен совпадать с числом `unknown` в таблицах блоков Markdown, не с длиной navigator.

Excluded (check / helper / flag / technical) в `unmapped.json` не входят — они в `context.excluded` и в навигаторе.

## Цикл правки

1. Прогнать книгу (`uv run finance-context build …` или `bash scripts/run.sh path/to/model.xlsx` при живом `serve`). `run.sh` кладёт в `out/<run>/` ещё `graph.json` и `graph-trace.json`.
2. Открыть `unmapped.json` и ту же строку в `inventory` / navigator: `label`, `parent_label`, `label_path`, `neighbors`, `candidates`, `hints`, `sheet`, `ref`, `disposition`, `exclusion_reason`, `article_role`, `cells`, `unit`. Формулы и влияние на CFS — `GET .../graph/trace` ([graph.md](graph.md)).
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

В Markdown таблицы режутся (по умолчанию 16 колонок и 80 строк). Navigator тоже режется по `max_rows` на лист. Если видите `_Truncated in Markdown; full series remain in JSON._`, полнота — в JSON, не в MD. На число **строк**-атрибутов в `unmapped.json` это не влияет, пока N ≤ 80 на блок.

## Команды

```bash
uv run python scripts/extract-unmapped.py out/<run>/context.json -o out/<run>/unmapped.json
uv run python scripts/extract-unmapped.py data/jobs/<job-id>/mapping.json
uv run pytest tests/test_extract_unmapped.py tests/eval/test_cashflow_dispositions.py tests/eval/test_corpus_dispositions.py
```
