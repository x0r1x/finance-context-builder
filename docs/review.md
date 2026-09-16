# Разбор несмапленных строк

Сначала убедиться, что layout вообще отдал fact-строки. `succeeded` + `Unmapped: 0` + пустой `context.md` почти всегда значит: нет блоков или нет лейблов статей, а не «таксономия покрыла всё». Чеклист — [layout.md](layout.md).

После прогона с ненулевым числом fact смотрят три представления одного и того же отказа:

| Где | Что видно |
| --- | --- |
| `context.md` | В блоке строка с Concept `unknown` (плюс счётчик `Unmapped: N`) |
| `context.json` → `unmapped` | Полные серии с `values` по периодам |
| `unmapped.json` | Та же выжимка атрибутов **без** `values`, плюс `ref` как в колонке Ref |

`scripts/extract-unmapped.py` (его вызывает `scripts/run.sh`) берёт `unmapped` из context или `rows` из mapping, оставляет `concept_id is null` и `disposition != excluded`, выкидывает ряды значений. Счётчик должен совпадать с числом `unknown` в Markdown.

Excluded (check / helper / technical) в этот список не входят — они в `context.excluded`.

## Цикл правки

1. Прогнать книгу (`uv run finance-context build …` или `bash scripts/run.sh path/to/model.xlsx` при живом `serve`).
2. Открыть `unmapped.json`: `label`, `parent_label`, `sheet`, `ref`, `disposition`, `exclusion_reason`, `article_role`.
3. Для каждой строки решить класс:

| Класс | Действие |
| --- | --- |
| Новое финансовое значение | Концепт в [taxonomy.yaml](taxonomy.md) + gold |
| Тот же смысл, другой лейбл / секция | `labels` / `aliases` / `section_hints` / `anti_labels` |
| Ребёнок под CAPEX/OPEX/Revenue, но это годы / MW / индекс | `unless` на parent-rollup + `facets.unit`, не money-id родителя |
| Однозначная формула (alias, SUM) | Проверить structure: SUM копирует концепт, только если замаплены все дети |
| Пустой прогон, нули в metrics | Layout, не yaml |
| Технический мост, check, шум | Exclusion; не плодить концепт |
| Реальная неоднозначность | Оставить `unknown` (`no_candidate` / `ambiguous` / `low_score`) |

4. Обновить gold: `tests/fixtures/mapping/cashflow_dispositions.yaml` для эталонной `cashflow.xlsx`; для корпуса — `packt_project_finance_dispositions.yaml` / `rvi_project_finance_dispositions.yaml`.
5. `uv run pytest` и при необходимости повторный прогон — `unmapped.json` должен сжаться только за счёт честных mapped, не за счёт exclude.

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

В Markdown таблицы режутся (по умолчанию 16 колонок и 80 строк). Если видите `_Truncated in Markdown; full series remain in JSON._`, полнота — в JSON, не в MD. На число **строк**-атрибутов в `unmapped.json` это не влияет, пока N ≤ 80 на блок.

## Команды

```bash
uv run python scripts/extract-unmapped.py out/<run>/context.json -o out/<run>/unmapped.json
uv run python scripts/extract-unmapped.py data/jobs/<job-id>/mapping.json
uv run pytest tests/test_extract_unmapped.py tests/eval/test_cashflow_dispositions.py tests/eval/test_corpus_dispositions.py
```
