# Маппинг

Маппинг — retrieve-and-align, не классификация на закрытом множестве. Таксономия — **словарь**, не воронка контента: строка не выбрасывается, если концепт не найден. Резолвер принимает `concept_id` только выше порога; иначе fact остаётся `unknown`, но в context остаются подпись, hints, соседи, формула и top-3 кандидатов.

Код: `src/finance_context/mapping/`. Точка входа стадии — `mapping_workbook` (`stage.py`) → `map_layout` (`cascade.py`). Сборка полного контента — `build_context` (`context/build.py`), схема `1.3.0`.

Связанные документы: [layout](layout.md), [таксономия](taxonomy.md), [разбор unmapped](review.md), [архитектура](architecture.md).

## Что участвует

Layout помечает тело блока видами строк. Каскад резолвит **`fact` / `flag` / `helper`**. `abstract` и `index` в `mapping.json` не попадают, но **все** layout-строки пишутся в `context.inventory`. Как собираются блоки и лейблы — [layout.md](layout.md). Если fact-строк нет, каскад не виноват: сначала ось **или** params-шейп и зона лейблов.

| kind | Роль |
| --- | --- |
| `fact` | Кандидат на `concept_id`; периодные значения в timeline-блоках / unmapped, либо role-tagged ячейки в params |
| `abstract` | Заголовок секции, родитель следующих fact; только inventory |
| `index` | Счётчики вроде `Week #`; только inventory |
| `helper` | Check / tie-out / плейсхолдер Spare; excluded |
| `flag` | 0/1 тайминг и сценарии; excluded, не financial unknown |

Лейблы из `is_noise_label` (`Dashboard`, `Assumptions`, `* chart`, `* bridge`, …) **не создают** строк в `mapping.json`, но остаются в inventory (без периодного ряда).

`needs_input` считается только по вопросам на fact-строках.

## Каскад

Порядок в `map_layout`:

1. По layout, IR cells и `ir/edges.parquet` строится `BookView`: паттерны формул (`analyze_structure`), row-adjacency, контекст строки (`RowContext`: лейбл, родитель, секция, лист, зерно, `value_kind`, шаблоны формул, `prev_labels` / `next_labels` ±2, `time_semantics`).
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
| `lexical` | `rule` | Фразы из `labels` / `aliases`, секция, `skip_concept`; `anti_labels` — жёсткий guard, не основной скоринг |
| `structure` | `structure` | Граф формул, соседи, priors по dependents |
| `embed` | `embed` | Косинус к эмбеддингам лейблов концептов |
| `chat` | `chat` | Rerank pruned-списка |

Lexical индексирует **и** `labels`, **и** `aliases`. Перед сравнением лейбл нормализуется (`normalize_label`): скобки снимаются, но аббревиатуры метрик (`EBITDA`, `CFADS`, `DSCR`) из скобок сохраняются; `cashflow` → `cash flow`; `&` → `and`; `/` → пробел (`Total Cash in/Cash out` сравнивается с `Total Cash in Cash out`). Однословные слабые фразы (`revenue`, `debt`, `total`, `cash`, …) не матчятся, если это **всё** содержимое лейбла; в составном лейбле (`REVENUE - Passenger Car`) — да. Для `cash` слабый матч ещё отключается, если рядом `flow` / `in` / `out` / `total` (`Cash Flow` не становится `bs.cash`), **кроме** `hand` / `hands` / `balance` (`Cash in hand` → `bs.cash`); для `debt` — если рядом `fee` / `up-front`. Множественное число (`Drawdowns`, `revenues`) сводится к форме из yaml. Anti-лейбл со слэшем (`fcfe /`) матчится по сырой строке, чтобы после замены `/` на пробел не блокировать голый `FCFE`.

Lexical дополнительно знает устойчивые конструкции из блока `patterns:` в yaml. Это **не** то же самое, что structure-агрегат:

- **Parent rollup** (дети наследуют секцию): `section_contains` `capex`/`uses` → `cf.capex`; `opex`/`operating`/`costs` → `pnl.opex`; `revenue` → `pnl.revenue`; D&A-секция → `pnl.da`. Кандидат с score 0.9. Не-денежные дети (срок жизни, MW, CPI, share premium, balance b/f) отсекаются `unless.label_contains` в тех же паттернах — иначе assumptions становятся opex/revenue. Pattern-хит **не** фильтруется `anti_labels` концепта до prune; стоп для rollup — `unless`.
- **Skip-pattern** запрещает концепт в секции: `bs.ap` в debt; `pnl.tax` на листе/пути `cfs` или `cash flow`, чтобы `Income Tax` в ОДДС не оставался P&L-налогом; `bs.equity` для `injected` / sources / construction; `cf.disbursements` для CFADS / available-for-debt. Глобально ставить `statement=cf` по имени листа нельзя: выручка на Cashflow Statement в PF-моделях остаётся `pnl.revenue`.
- `section_contains` с пробелом (`cash flow`) требует **все** токены фразы в `section_tokens` (лейбл ∪ родитель ∪ путь ∪ лист). Одно слово `cashflow` после нормализации не существует — это два токена.
- Точная строка `cash flow` (без available/operating/net) → `cf.net`.
- Если у концепта заданы `section_hints`, фраза принимается только при попадании хинта в лейбл / родителя / путь секции / лист (например `Arrangement fee` на Ratios: в hints есть `ratios` / `irr`).

`_semantic_ratio` / `_semantic_count` смотрят **токены** нормализованного лейбла, не подстроку: `ratio` внутри `generation` не делает MWh коэффициентом. Ratio-токены: `dscr`, `coverage`, `cpi`, `inflation`, `availability` (не если рядом `generation`), `wacc`, `coc`, … Count: `lifetime`, `turbine`, `traffic`, `generation`, `capacity`, `mw`. Голый токен `lease` **не** ratio: денежный `Variable land lease` в CFS — opex/cash, ставка — только когда ряд % или нули как input. Явная колонка единиц (`%` / `years` / `£` / `£/year`) задаёт `value_kind` и **не** перебивается семантикой лейбла. `£/year` — money, не count. Prune тогда отбрасывает несовместимые концепты.

Фасет `basis=accrual` ставится по `accrual` / `accrued` / `revenue earned`, не по голому `earned` — иначе `Dividends earned` прунится с `cf.dividends`.

### Structure

`analyze_structure` смотрит шаблоны формул по периодным колонкам и вешает `RowPattern`:

| kind | Когда | Что предлагает |
| --- | --- | --- |
| `alias` | Ячейка = одна ячейка другой строки/листа | Тот же `concept_id`, что у источника (score ~0.96). Пример: `Dashboard!C17 = Weekly_Forecast!C39` |
| `aggregate` | `SUM` соседних fact-строк | Общий концепт детей или их `broader`, **только если замаплены все члены диапазона**. Один смапленный ребёнок (Insurance внутри EBITDA) концепт родителю не копирует. Итог не становится `cf.net` только потому что «Total». SUM разнонаправленных equity-линий под IRR → `cf.equity_cashflow`, не `cf.receipts`. Пример: `Total Inflows = SUM(collections)` → `cf.receipts` |
| `diff` | Разность двух строк | Родитель из `calculations` с противоположными весами |
| `roll` | Roll-forward остатка | Тот же балансный концепт, что у связанной строки |
| neighbor prior | Соседи ±2 | Денежный lease рядом с opex / Variable land lease → `pnl.opex`, не `ops.lease_rate` |
| graph prior | Строка питает уже размеченный sink (`cf.uses`, `pnl.opex`, …) | Категория sink как prior (~0.86). Не для CFADS / available-for-debt |

Деление на именованную константу или число — **proration**, `value_kind` остаётся `money`. Настоящий ratio — токены вроде `dscr` / `coverage` / `leverage` / `runway` / `cpi` (не подстрока: `generation` ≠ ratio), см. `_semantic_ratio`.

Совместимость с объявленными `calculations`: совпадение **поднимает** score; несовпадение **снижает**. Abstain `calculation_conflict` остаётся для SUM vs declared DIFF (например Total Inflows ≠ `cf.net`). Исключение keep-rule: exact `Cash Flow` под IRR/ratios не аннулируется.

Relations (`alias`, `aggregate`, `difference`, `roll_forward`) пишутся в `mapping.json` и в блоки `context.json`. В Markdown их нет; row-level refs видны в навигаторе.

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

Пороги не снижают, чтобы «закрыть покрытие». Близкий неверный тег хуже `unknown`. Top-3 кандидата кладутся в `alternatives` / `candidates` и при abstain: если prune опустошил fused-список, остаются сырые proposals (`no_candidate` больше не значит `candidates: []`).

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
| `abstained` | null | вопрос; в MD Concept = `unknown`; candidates и hints сохраняются |

При abstain в `exclusion_reason` пишется причина отказа резолвера (это не exclude):

| Код | Когда |
| --- | --- |
| `no_candidate` | После prune список пуст |
| `low_score` | Есть кандидат, но ниже порога |
| `ambiguous` | Два близких лидера |
| `facet_mismatch` | Иначе (кандидаты не прошли decide) |
| `calculation_conflict` | Наблюдённый SUM/diff противоречит объявленному `calculations` и keep-rule не сработал |

## Hints и inventory

Даже при `concept_id = null` у строки в context есть `hints`: `nature` (flow/balance), `time_semantics` (flow / bop / eop / rate), `statement`, `unit`, плюс `segment` (`pc`/`hv`) и `escalation` (`revenue`/`cost`) когда это видно из лейбла. Unknown сразу полезен даунстриму.

Fact-строки в `params`-блоке — `article_role=assumption` (INDEX живого сценария не делает их calculation). ALL-CAPS секции без числа — `abstract`, не concept.

`inventory` — лёгкие записи на **каждую** layout-строку: `kind`, `indent`, `hidden`, `label_path`, `neighbors`, `formula_fingerprint` / exceptions, `numeric_summary`, `precedents_rows` / `dependents_rows`, `cells` (роли `value` / `unit` / `scenario` / `total` / `note`), `precedent_cells` (до 8 ссылок с графа, которых нет среди уже экспортированных ячеек строки). Period values не дублируются на inventory. Инвариант: `len(inventory) ==` сумма layout-строк **принятых** блоков. Отброшенные Cover / Shortcuts в знаменатель не входят. Нарушение — warning `Content completeness N/M`.

Top-3 `candidates` пишутся и при abstain: если prune опустошил fused-список, в context остаются сырые proposals.

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

- **content completeness** — `inventory` / layout rows; должна быть 1.0;
- **concept coverage** — доля annotatable (mapped + abstained, без excluded) с принятым концептом; может быть < 100%;
- **selective risk** — ошибки среди **принятых** маппингов (abstain в риск не входит);
- **abstain rate** и **risk–coverage** кривая — качество права отказаться.

В шапке `context.md` печатаются completeness и coverage отдельно.

Золотые ожидания для `resources/cashflow.xlsx`: `tests/fixtures/mapping/cashflow_dispositions.yaml`. Публичный корпус (MIT / CC-BY-NC-SA, не в git): `uv run python scripts/fetch-corpus.py` → `resources/corpus/` (Packt, RVI; three-statement в lock может 404). Gold: `packt_project_finance_dispositions.yaml`, `rvi_project_finance_dispositions.yaml`. Помимо «должно быть» gold знает **негативы** `forbidden_concept_id` (`Equity Injected` ≠ `bs.equity`, CFS `Variable land lease` ≠ `ops.lease_rate`, generation ≠ `ops.availability`). Тесты корпуса скипятся, если книги не скачаны или `expectations` пусты.

## Как расширять маппинг

1. Сначала [таксономия](taxonomy.md): есть ли смысл, или это тот же id с другим лейблом.
2. Если смысл есть, а каскад молчит — уточнить `labels` / `aliases` / `section_hints` / `skip_concept` / `unless`, не широкий `anti_labels` (`cash in` убивает `Cash in hand`; голый `lease` убивает land lease). Если ложный тег от секции — `unless` на parent-rollup, не понижать порог.
3. Если формула однозначна (копия с другого листа, SUM детей) — это задача structure, не chat.
4. Новый *тип* совпадения — новый `Signal` + тесты в `tests/mapping/`.
5. Не включать строку в exclude, если это бизнес-атрибут.
6. Прогнать `uv run pytest` и сценарий из [review.md](review.md).
