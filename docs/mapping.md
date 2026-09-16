# Маппинг

Маппинг — retrieve-and-align, не классификация на закрытом множестве. Резолвер принимает концепт только выше порога; иначе строка остаётся `unknown` и (для настоящих fact-строк) порождает вопрос.

Код: `src/finance_context/mapping/`. Точка входа стадии — `mapping_workbook` (`stage.py`) → `map_layout` (`cascade.py`).

Связанные документы: [layout](layout.md), [таксономия](taxonomy.md), [разбор unmapped](review.md), [архитектура](architecture.md).

## Что участвует

Layout помечает тело блока видами строк. В маппинг идут **только** `kind=fact` (в периоде есть числа или формулы). Как собираются блоки и лейблы — [layout.md](layout.md). Если fact-строк нет, каскад не виноват: сначала ось и зона лейблов.

| kind | Роль |
| --- | --- |
| `fact` | Кандидат на `concept_id` |
| `abstract` | Заголовок секции, родитель следующих fact |
| `index` | Счётчики вроде `Week #` |
| `helper` | Check / tie-out / плейсхолдер Spare |
| `flag` | 0/1 тайминг и сценарии; excluded, не financial unknown |

Лейблы из `is_noise_label` (`Dashboard`, `Assumptions`, `* chart`, `* bridge`, …) **не создают** строк в `mapping.json`.

`needs_input` считается только по вопросам на fact-строках.

## Каскад

Порядок в `map_layout`:

1. По layout и IR строится `BookView`: паттерны формул (`analyze_structure`), контекст строки (`RowContext`: лейбл, родитель, секция, лист, зерно, `value_kind`, шаблоны формул).
2. **Исключение.** Если `exclusion_reason(ctx)` не пустой — строка не резолвится, disposition = `excluded`, вопроса нет.
3. До **четырёх** проходов сигналов `glossary + lexical + structure`. Нужно, чтобы alias/SUM подтянули концепт, когда соседняя строка замапилась на предыдущей итерации (structure fixpoint).
4. Нерезолвнутые fact + включённый EmbedPort → dense retrieve по лейблам концептов, затем снова fuse/decide вместе с lexical/glossary/structure.
5. Оставшиеся + ChatPort → rerank короткого списка. Может вернуть `unknown`. **Не имеет права изобрести id** вне таксономии.
6. Финальный проход только `structure` (подтянуть то, что открылось после embed/chat).
7. Сборка `MappingDocument`: `rows` + `questions` + structural `relations`.

Повторная загрузка той же книги на HTTP пересобирает compile, layout, mapping и context; parse (`raw/`) переиспользуется. Сам `mapping.json` при повторном CLI-прогоне в тот же каталог **скипается**, если файл уже лежит на диске.

## Сигналы

Новая идея совпадения — новая реализация `Signal.propose(ctx, book) -> list[Candidate]`, не `if` на конкретную книгу.

| Сигнал | `source` в строке | Роль |
| --- | --- | --- |
| `glossary` | `glossary` | Выученная пара `(normalize(label), normalize(parent)) → concept_id` |
| `lexical` | `rule` | Фразы из `labels` / `aliases`, с учётом секции и `anti_labels` |
| `structure` | `structure` | Граф формул |
| `embed` | `embed` | Косинус к эмбеддингам лейблов концептов |
| `chat` | `chat` | Rerank pruned-списка |

Lexical индексирует **и** `labels`, **и** `aliases`. Перед сравнением лейбл нормализуется (`normalize_label`): скобки снимаются, но аббревиатуры метрик (`EBITDA`, `CFADS`, `DSCR`) из скобок сохраняются; `cashflow` → `cash flow`; `&` → `and`. Однословные слабые фразы (`revenue`, `debt`, `total`, `cash`, …) не матчятся, если это **всё** содержимое лейбла; в составном лейбле (`REVENUE - Passenger Car`) — да. Для `cash` слабый матч ещё отключается, если рядом `flow` / `in` / `out` / `total` (`Cash Flow` не становится `bs.cash`); для `debt` — если рядом `fee` / `up-front`. Множественное число (`Drawdowns`, `revenues`) сводится к форме из yaml.

Lexical дополнительно знает устойчивые конструкции из блока `patterns:` в yaml. Это **не** то же самое, что structure-агрегат:

- **Parent rollup** (дети наследуют секцию): `section_contains` `capex`/`uses` → `cf.capex`; `opex`/`operating`/`costs` → `pnl.opex`; `revenue` → `pnl.revenue`; D&A-секция → `pnl.da`. Кандидат с score 0.9. Не-денежные дети (срок жизни, MW, CPI, share premium, balance b/f) отсекаются `unless.label_contains` в тех же паттернах — иначе assumptions становятся opex/revenue. Pattern-хит **не** фильтруется `anti_labels` концепта до prune; стоп для rollup — `unless`.
- **Skip-pattern** запрещает концепт в секции: `bs.ap` в debt; `pnl.tax` на листе/пути `cfs` или `cash flow`, чтобы `Income Tax` в ОДДС не оставался P&L-налогом. Глобально ставить `statement=cf` по имени листа нельзя: выручка на Cashflow Statement в PF-моделях остаётся `pnl.revenue`.
- `section_contains` с пробелом (`cash flow`) требует **все** токены фразы в `section_tokens` (лейбл ∪ родитель ∪ путь ∪ лист). Одно слово `cashflow` после нормализации не существует — это два токена.
- Точная строка `cash flow` (без available/operating/net) → `cf.net`.
- Если у концепта заданы `section_hints`, фраза принимается только при попадании хинта в лейбл / родителя / путь секции / лист (например `Arrangement fee` на Ratios: в hints есть `ratios` / `irr`).

`_semantic_ratio` / `_semantic_count` смотрят **токены** нормализованного лейбла, не подстроку: `ratio` внутри `generation` не делает MWh коэффициентом. Ratio-токены: `dscr`, `coverage`, `cpi`, `inflation`, `availability`, `wacc`, `coc`, … Count: `lifetime`, `turbine`, `traffic`, `generation`, `capacity`, `mw`. Prune тогда отбрасывает money-концепты.

Фасет `basis=accrual` ставится по `accrual` / `accrued` / `revenue earned`, не по голому `earned` — иначе `Dividends earned` прунится с `cf.dividends`.

### Structure

`analyze_structure` смотрит шаблоны формул по периодным колонкам и вешает `RowPattern`:

| kind | Когда | Что предлагает |
| --- | --- | --- |
| `alias` | Ячейка = одна ячейка другой строки/листа | Тот же `concept_id`, что у источника (score ~0.96). Пример: `Dashboard!C17 = Weekly_Forecast!C39` |
| `aggregate` | `SUM` соседних fact-строк | Общий концепт детей или их `broader`, **только если замаплены все члены диапазона**. Один смапленный ребёнок (Insurance внутри EBITDA) концепт родителю не копирует. Итог не становится `cf.net` только потому что «Total». Пример: `Total Inflows = SUM(collections)` → `cf.receipts` |
| `diff` | Разность двух строк | Родитель из `calculations` с противоположными весами |
| `roll` | Roll-forward остатка | Тот же балансный концепт, что у связанной строки |

Деление на именованную константу или число — **proration**, `value_kind` остаётся `money`. Настоящий ratio — токены вроде `dscr` / `coverage` / `leverage` / `runway` / `cpi` (не подстрока: `generation` ≠ ratio), см. `_semantic_ratio`.

Relations (`alias`, `aggregate`, `difference`, `roll_forward`) пишутся в `mapping.json` и в блоки `context.json`. В Markdown их нет.

## Resolver

`Resolver.fuse` собирает кандидатов с одинаковым `concept_id`: берёт лучший score и небольшой бонус за согласие нескольких сигналов, затем `prune_candidates`.

Prune отбрасывает:

- id вне таксономии;
- несовместимый `unit` / `value_kind`;
- попадание `anti_labels` концепта в лейбл строки;
- уверенно выведенный фасет строки, который противоречит фасету концепта (`statement=cov` ставится по **лейблу** строки: `dscr` / `llcr` / `plcr`, не по заголовку секции — иначе CFADS под DSCR отсекается).

`decide`:

- пустой список → отказ;
- лучший score **&lt; 0.82** (`ACCEPT_MIN`) → отказ;
- два лидера ближе чем **0.02** → берётся более специфичный id (`broader` задан), иначе отказ (ambiguous);
- для сигнала `embed` дополнительно: score ≥ **0.85** (`COSINE_MIN`) и отрыв от второго ≥ **0.08** (`COSINE_GAP`); top-k эмбеддингов = 5.

Пороги не снижают, чтобы «закрыть покрытие». Близкий неверный тег хуже `unknown`.

## Exclusion

`exclusion_reason` (`exclusion.py`) до резолвера:

| Код | Условие |
| --- | --- |
| `check` | `article_role == check` |
| `helper` | `kind == helper` |
| `noise` | `is_noise_label` (если строка всё же попала в контекст) |
| `technical_bridge` | в лейбле `from mf` / `circular` / `helper`, кроме `pre-revolver` |

Excluded: `concept_id = null`, `source = rule`, вопросов нет, в `context.excluded`. В `unmapped.json` и в секцию Unmapped Markdown **не** попадают.

KPI и расчётные бизнес-строки (`article_role = calculation`) — обычные fact: их нужно мапить или честно abstain, не exclude.

## Disposition итоговой строки

| disposition | `concept_id` | Review |
| --- | --- | --- |
| `mapped` | задан | нет |
| `excluded` | null | нет |
| `abstained` | null | вопрос; в MD Concept = `unknown` |

При abstain в `exclusion_reason` пишется причина отказа резолвера (это не exclude):

| Код | Когда |
| --- | --- |
| `no_candidate` | После prune список пуст |
| `low_score` | Есть кандидат, но ниже порога |
| `ambiguous` | Два близких лидера |
| `facet_mismatch` | Иначе (кандидаты не прошли decide) |
| `calculation_conflict` | Наблюдённый SUM/diff противоречит объявленному `calculations` |

## Glossary

Файл `$DATA_DIR/glossary.json`, ключ `(normalized_label, normalized_parent)`. После джоба `learn_from_rows` дописывает только строки с `confidence = high` и `source` из `{glossary, rule, structure, lexical}`. Chat и embed **не** сохраняются.

Дополнительно кладётся ключ с классом секции (`section_class`), чтобы тот же лейбл в похожей секции другой книги подхватился.

Это кэш уверенных совпадений, не место для костылей одной модели. Новое значение — концепт в yaml.

## Уверенность

| source | confidence |
| --- | --- |
| glossary, rule, structure, lexical | `high` |
| embed | `high` при score ≥ 0.85, иначе `medium` |
| chat | `medium` |
| question (abstain) | `low` |

## Качество

`finance_context.mapping.eval`:

- **coverage** — доля fact-строк с принятым концептом;
- **selective risk** — ошибки среди **принятых** маппингов (abstain в риск не входит);
- **abstain rate** и **risk–coverage** кривая — качество права отказаться.

Золотые ожидания для `resources/cashflow.xlsx`: `tests/fixtures/mapping/cashflow_dispositions.yaml`. Публичный корпус (MIT / CC-BY-NC-SA, не в git): `uv run python scripts/fetch-corpus.py` → `resources/corpus/` (Packt, RVI; three-statement в lock может 404). Gold: `packt_project_finance_dispositions.yaml`, `rvi_project_finance_dispositions.yaml`. Тесты корпуса скипятся, если книги не скачаны или `expectations` пусты.

## Как расширять маппинг

1. Сначала [таксономия](taxonomy.md): есть ли смысл, или это тот же id с другим лейблом.
2. Если смысл есть, а каскад молчит — уточнить `labels` / `aliases` / `section_hints` / `anti_labels`, не общий alias на два значения. Если ложный тег от секции — `unless` на parent-rollup, не понижать порог.
3. Если формула однозначна (копия с другого листа, SUM детей) — это задача structure, не chat.
4. Новый *тип* совпадения — новый `Signal` + тесты в `tests/mapping/`.
5. Не включать строку в exclude, если это бизнес-атрибут.
6. Прогнать `uv run pytest` и сценарий из [review.md](review.md).
