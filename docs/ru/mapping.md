# Маппинг

**Русский** · [English](../en/mapping.md)

Маппинг — retrieve-and-align, не классификация на закрытом множестве. Таксономия — **словарь**, не воронка контента: строка не выбрасывается, если концепт не найден. Резолвер принимает `concept_id` только выше порога; иначе fact остаётся `unknown`, но в context остаются подпись, hints, соседи, формула и top-3 кандидатов.

Код: `src/finance_context/mapping/`. Точка входа стадии — `mapping_workbook` (`stage.py`) → `map_layout` (`cascade.py`). Сборка полного контента — `build_context` (`context/build.py`), схема `1.13.0`.

Связанные документы: [layout](layout.md), [таксономия](taxonomy.md), [граф](graph.md), [разбор unmapped](review.md), [архитектура](architecture.md).

## Что участвует

Layout помечает тело блока видами строк. Каскад резолвит **`fact` / `flag` / `helper`**. `abstract` и `index` в `mapping.json` не попадают, но **все** layout-строки пишутся в `blocks[].rows` своего блока. Как собираются блоки и лейблы — [layout.md](layout.md). Если fact-строк нет, каскад не виноват: сначала ось **или** params-шейп и зона лейблов.

| kind | Роль |
| --- | --- |
| `fact` | Кандидат на `concept_id`; периодные значения в строке timeline-блока, либо role-tagged ячейки в params |
| `abstract` | Заголовок секции, родитель следующих fact; `disposition=header` на строке блока |
| `index` | Счётчики вроде `Week #`; строка своего блока |
| `helper` | Check / tie-out / плейсхолдер Spare; excluded |
| `flag` | 0/1 тайминг и сценарии; excluded, не financial unknown |

Лейблы из `is_noise_label` (`Dashboard`, `Assumptions`, `* chart`, `* bridge`, …) **не создают** строк в `mapping.json`, но остаются строкой блока (без периодного ряда).

Опубликованная джоба получает `succeeded` и тогда, когда не у каждой fact есть концепт. Строка без концепта остаётся `abstained`.

## Каскад

Порядок в `map_layout`:

1. По layout, IR cells и рёбрам строится `BookView`: сначала `ir/cell_edges.parquet`, если его нет — `ir/edges.parquet`. Дальше паттерны формул (`analyze_structure`), row-adjacency, контекст строки (`RowContext`: лейбл, родитель, секция, лист, зерно, `value_kind`, шаблоны формул, `prev_labels` / `next_labels` ±2, `time_semantics`).
2. **Исключение.** Если `exclusion_reason(ctx)` не пустой — строка не резолвится, disposition = `excluded`.
3. До **четырёх** проходов сигналов `glossary + lexical + structure`. Нужно, чтобы alias/SUM подтянули концепт, когда соседняя строка замапилась на предыдущей итерации (structure fixpoint).
4. Нерезолвнутые fact + включённый EmbedPort → dense retrieve по фразам концепта (`labels`, `aliases`, `definition` и лейбл `broader`), затем снова fuse/decide вместе с lexical/glossary/structure.
5. Оставшиеся + ChatPort → rerank короткого списка. Может вернуть `unknown`. **Не имеет права изобрести id** вне таксономии.
6. Финальный проход только `structure` (подтянуть то, что открылось после embed/chat).
7. Сборка `MappingDocument`: `rows` и structural `relations` (`alias` / `aggregate` / `difference` / `roll_forward` для каскада). `questions` остаётся пустым. Это **не** полный cell-граф: completeness зависимостей смотреть в `ir/cell_edges.parquet`, `graph.json` `links` и trace, не в `context.blocks[].relations`.

Пустой список на embed или chat пишет INFO `model_skip`: `reason=resolved`, если нерезолвнутых строк нет, и `reason=unconfigured`, если порт не задан, а строки остались. Попадание в `shared/embeddings/{model}-{taxhash}.npz` пишет INFO `embed_cache` с `reason=cache` и модель не вызывает.

Каждая книга по HTTP считается в своём процессе, со своими клиентами чата и эмбеддингов. Общего замка на mapping нет. Промах файла эмбеддингов не ждёт чужой эмбеддинг: замок только на запись, и только если файл всё ещё пуст или протух. `shared/label_memory.json` дописывается под своим замком и виден всем сессиям. Замок не держится во время эмбеддингов и модели.

Повторный POST готовой книги (`context.json`, `context.md`, `graph.json`, `graph.md` и терминальный `meta.json` с тем же `publisher`) отдаёт снимок и файлы не удаляет. Повторный POST, пока процесс этой книги жив, второй процесс не стартует. Живой процесс останавливается, только если штамп `publisher` есть и отличается. Пустой штамп или штамп без карты `stages` пересобирает всё от layout вниз и сохраняет `raw/` и formula IR. `meta.stages` выбирает хвост: `compile` удаляет `raw/`, formula IR и всё после них; `layout` удаляет layout, mapping, graph и context; `mapping` удаляет mapping, graph и context; `graph` удаляет graph и context; `publish` удаляет только `context.json` и `context.md`. Formula IR пишется заново, если `ir/compile.json` не совпал со схемой колонок. `ir/graph_edges.parquet` удаляется вместе с `graph.json`, когда устарела стадия graph или более ранняя. CLI печатает `reused`, только если опубликованные документы и тот же `publisher` уже на диске. Если одного из них нет, стадии всё равно скипаются по своим артефактам (`raw/workbook.json`, штамп `ir/compile.json`, `layout.json`, `mapping.json`, `graph.json`). Удаление только `context.json` не пересобирает mapping.

## Сигналы

Новая идея совпадения — новая реализация `Signal.propose(ctx, book) -> list[Candidate]`, не `if` на конкретную книгу.

| Сигнал | `source` в строке | Роль |
| --- | --- | --- |
| `glossary` | `glossary` | Сначала тройка общей памяти: `(normalize(label), ближайшая секция, единица) → concept_id`, единица — `money`, `rate`, `years` или пусто. Затем сессионная пара `(normalize(label), normalize(parent))`, включая запасной `section_class`. Тот же `skip_concept`, что у lexical: на CFS `Gross Revenues` не остаётся `pnl.revenue`, `Equity` в Sources не остаётся `bs.equity`. `reconcile_glossary` не затирает живую пару отчётов (`pnl.revenue` ↔ `cf.receipts`, `bs.equity` ↔ `cf.equity_issue`) |
| `lexical` | `rule` | Фразы из `labels` / `aliases`, секция, `skip_concept`; `anti_labels` — жёсткий guard, не основной скоринг |
| `structure` | `structure` | Граф формул, соседи, priors по dependents |
| `embed` | `embed` | Косинус к среднему вектору фраз концепта: `labels`, `aliases`, `definition`, лейбл `broader` |
| `chat` | `chat` | Rerank pruned-списка |

Lexical индексирует **и** `labels`, **и** `aliases`. Перед сравнением лейбл нормализуется (`normalize_label`): скобки снимаются, но аббревиатуры метрик (`EBITDA`, `CFADS`, `DSCR`) из скобок сохраняются; `cashflow` → `cash flow`; `&` → `and`; `/` → пробел (`Total Cash in/Cash out` сравнивается с `Total Cash in Cash out`). Однословные слабые фразы (`revenue`, `debt`, `total`, `cash`, …) не матчятся, если это **всё** содержимое лейбла; в составном лейбле (`REVENUE - Passenger Car`) — да. Для `cash` слабый матч ещё отключается, если рядом `flow` / `in` / `out` / `total` (`Cash Flow` не становится `bs.cash`), **кроме** `hand` / `hands` / `balance` (`Cash in hand` → `bs.cash`); для `debt` — если рядом `fee` / `up-front`. Множественное число (`Drawdowns`, `revenues`) сводится к форме из yaml. Anti-лейбл со слэшем (`fcfe /`) матчится по сырой строке, чтобы после замены `/` на пробел не блокировать голый `FCFE`.

Lexical дополнительно знает устойчивые конструкции из блока `patterns:` в yaml. Это **не** то же самое, что structure-агрегат:

- **Parent rollup** (дети наследуют секцию): `section_contains` `capex`/`uses` → `cf.capex`; `opex`/`operating`/`costs` → `pnl.opex`; `revenue` → `pnl.revenue`; D&A-секция → `pnl.da`. Кандидат с score 0.9. Не-денежные дети (срок жизни, MW, CPI, share premium, balance b/f) отсекаются `unless.label_contains` в тех же паттернах — иначе assumptions становятся opex/revenue. Pattern-хит **не** фильтруется `anti_labels` концепта до prune; стоп для rollup — `unless`.
- **Skip-pattern** запрещает концепт в секции: `bs.ap` в debt; `pnl.tax` / `pnl.opex` / `pnl.revenue` / `pnl.interest` на `cfs` / `cash flow`, чтобы ОДДС не оставался P&L; `bs.equity` для `injected` / sources / construction; `cf.disbursements` для CFADS / available-for-debt. Не ставить `statement=cf` на **весь** лист: `Cash in hand` остаётся `bs.cash`. Выручка/OPEX/налог/процент на CFS → `cf.receipts` / `cf.opex_paid` / `cf.tax_paid` / `cf.interest_paid` (pattern + alias crosswalk).
- `section_contains` с пробелом (`cash flow`) требует **все** токены фразы в `section_tokens` (лейбл ∪ родитель ∪ путь ∪ лист). Одно слово `cashflow` после нормализации не существует — это два токена.
- Точная строка `cash flow` (без available/operating/net) → `cf.net`.
- Если у концепта заданы `section_hints`, фраза принимается только при попадании хинта в лейбл / родителя / путь секции / лист (например `Arrangement fee` на Ratios: в hints есть `ratios` / `irr`).

`_semantic_ratio` / `_semantic_count` смотрят **токены** нормализованного лейбла, не подстроку: `ratio` внутри `generation` не делает MWh коэффициентом. Ratio-токены: `dscr`, `coverage`, `cpi`, `inflation`, `availability` (не если рядом `generation`), `wacc`, `coc`, … Count: `lifetime`, `turbine`, `traffic`, `generation`, `capacity`, `mw`. Голый токен `lease` **не** ratio: денежный `Variable land lease` в CFS — opex/cash, ставка — только когда ряд % или нули как input. Явная колонка единиц (`%` / `years` / `£` / `£/year` / `Index` / `MWh p.a.`) задаёт `value_kind` и **не** перебивается семантикой лейбла. `years` и `MWh p.a.` для prune — count, целая ячейка `Index` — ratio, `£/year` — money. Prune тогда отбрасывает несовместимые концепты.

Фасет `basis=accrual` ставится по `accrual` / `accrued` / `revenue earned`, не по голому `earned` — иначе `Dividends earned` прунится с `cf.dividends`.

### Structure

`analyze_structure` смотрит шаблоны формул по периодным колонкам и вешает `RowPattern`:

| kind | Когда | Что предлагает |
| --- | --- | --- |
| `alias` | Ячейка = одна ячейка другой строки/листа | Тот же `concept_id`, что у источника (score ~0.96), **кроме** CFS: P&L→`cf.*` crosswalk (0.94) и кроме `bs.*` на Sources/Uses. Пример: `Dashboard!C17 = Weekly_Forecast!C39` |
| `aggregate` | `SUM` соседних fact-строк | Общий концепт детей или их `broader`, **только если замаплены все члены диапазона**. Один смапленный ребёнок (Insurance внутри EBITDA) концепт родителю не копирует. Итог не становится `cf.net` только потому что «Total». SUM разнонаправленных equity-линий под IRR → `cf.equity_cashflow`, не `cf.receipts`. Пример: `Total Inflows = SUM(collections)` → `cf.receipts` |
| `diff` | Разность двух строк | Родитель из `calculations` с противоположными весами |
| `roll` | Roll-forward остатка | Тот же балансный концепт, что у связанной строки |
| neighbor prior | Соседи ±2 | Денежный lease рядом с opex / Variable land lease → `pnl.opex`, не `ops.lease_rate` |
| graph prior | Строка питает уже размеченный sink (`cf.uses`, `pnl.opex`, …) | Категория sink как prior (~0.86). Не для CFADS / available-for-debt |

Деление на именованную константу или число — **proration**, `value_kind` остаётся `money`. Настоящий ratio — токены вроде `dscr` / `coverage` / `leverage` / `runway` / `cpi` (не подстрока: `generation` ≠ ratio), см. `_semantic_ratio`.

Совместимость с объявленными `calculations`: совпадение **поднимает** score; несовпадение **снижает**. Abstain `calculation_conflict` остаётся для SUM vs declared DIFF (например Total Inflows ≠ `cf.net`). Исключение keep-rule: exact `Cash Flow` под IRR/ratios не аннулируется.

Relations (`alias`, `aggregate`, `difference`, `roll_forward`) — семантические связи mapping, не формульный граф. Они пишутся в `mapping.json` и в блоки `context.json`. Cell-level рёбра — в IR, см. [graph.md](graph.md).

<a id="thresholds"></a>

## Пороги

Пять значений живут в полях Settings. Пустая или отсутствующая переменная оставляет значение из таблицы. Балл должен быть больше 0 и не больше 1, отрыв от 0 до 1, `EMBED_TOP_K` — целое от 1 до 32. Число вне диапазона останавливает процесс.

| Поле | Переменная | Значение | Смысл |
| --- | --- | --- | --- |
| `concept_accept_min` | `CONCEPT_ACCEPT_MIN` | 0.82 | Строка получает концепт, когда балл лучшего кандидата не ниже этого числа. Ниже него в отчёте стоит `unknown`, а кандидаты остаются. Флаг качества `confidence_threshold_passed` требует, чтобы каждая принятая строка достигла этого балла. |
| `embed_score_min` | `EMBED_SCORE_MIN` | 0.85 | Совпадение по эмбеддингу, косинус между текстом строки и концептом, берётся по более жёсткой планке, чем остальные сигналы. Оно выбирается, когда косинус не ниже этого числа, затем считается уверенным и запоминается для следующих книг. |
| `embed_score_gap` | `EMBED_SCORE_GAP` | 0.08 | Этот эмбеддинг выбирается, когда его косинус обгоняет следующий минимум на это число. Более близкий второй кандидат оставляет строку `unknown`. |
| `embed_top_k` | `EMBED_TOP_K` | 5 | Сколько ближайших концептов остаётся на строке для поиска по эмбеддингу и для короткого списка, который видит модель. В опубликованных context и Markdown видны первые три. |
| `mint_score_max` | `MINT_SCORE_MAX` | 0.5 | Новый концепт таксономии создаётся для строки `unknown`, у которой уже есть ближайший существующий концепт, когда балл этого концепта ниже этого числа. |

## Resolver

`Resolver.fuse` собирает кандидатов с одинаковым `concept_id`: берёт лучший score и небольшой бонус за согласие нескольких сигналов, затем `prune_candidates`.

Prune отбрасывает:

- id вне таксономии;
- несовместимый `unit` / `value_kind`;
- попадание `anti_labels` концепта в лейбл строки;
- уверенно выведенный фасет строки, который противоречит фасету концепта (`statement=cov` ставится по **лейблу** строки: `dscr` / `llcr` / `plcr`, не по заголовку секции — иначе CFADS под DSCR отсекается).

`decide`:

- пустой список → отказ;
- лучший score **&lt; `concept_accept_min`** → отказ;
- два лидера ближе чем **0.02** → берётся более специфичный id (`broader` задан), иначе отказ (ambiguous);
- для сигнала `embed` дополнительно: score ≥ **`embed_score_min`** и отрыв от второго ≥ **`embed_score_gap`**. На строке остаётся `embed_top_k` альтернатив.

Пороги не снижают, чтобы «закрыть покрытие». Близкий неверный тег хуже `unknown`. Top-3 кандидата кладутся в `alternatives` / `candidates` и при abstain: если prune опустошил fused-список, остаются сырые proposals (`no_candidate` больше не значит `candidates: []`).

## Exclusion

`exclusion_reason` (`exclusion.py`) до резолвера:

| Код | Условие |
| --- | --- |
| `flag` | `kind == flag` (0/1 тайминг, сценарий, selector) |
| `check` | `article_role == check` |
| `helper` | `kind == helper` |
| `noise` | `is_noise_label` (если строка всё же попала в контекст) |
| `technical_bridge` | в лейбле `from mf` / `circular` / `helper`, кроме `pre-revolver` |

Excluded: `concept_id = null`, `source = rule`, `disposition=excluded` на той же строке блока. В `unmapped.json` **не** попадают.

KPI и расчётные бизнес-строки (`article_role = calculation`) — обычные fact: их нужно мапить или честно abstain, не exclude.

## Disposition итоговой строки

| disposition | `concept_id` | Review |
| --- | --- | --- |
| `mapped` | задан | нет |
| `excluded` | null | нет |
| `abstained` | null | концепта нет; в MD Concept = `unknown`; candidates и hints сохраняются |

У `abstract` на строке блока стоит `disposition=header` (это не отказ маппинга и не abstain).

При abstain в `exclusion_reason` пишется причина отказа резолвера (это не exclude):

| Код | Когда |
| --- | --- |
| `no_candidate` | После prune список пуст |
| `low_score` | Есть кандидат, но ниже порога |
| `ambiguous` | Два близких лидера |
| `facet_mismatch` | Иначе (кандидаты не прошли decide) |
| `calculation_conflict` | Наблюдённый SUM/diff противоречит объявленному `calculations` и keep-rule не сработал |

## Hints и строки блока

Даже при `concept_id = null` у строки в context есть `hints`: `nature` (flow/balance), `time_semantics` (flow / bop / eop / rate / stock / instant), `statement`, `unit` (`money` / `count` / `rate` / `ratio` / `years`, плюс hints контекста `price` и `date`), `currency` (`GBP` / `EUR` / `USD` / `RUB`; те же обработчики: символ, ISO, локальное сокращение — `£`/`gbp`/`pound`/`фунт`, `€`/`eur`/`euro`/`евро`, `$`/`usd`/`dollar`/`долл`, `₽`/`rub`/`руб`/`РУБ`), `scale` (`unit` / `k` / `m` / `bn`), `sign` (`inflow` / `outflow` / `stock`), плюс `segment` (`pc`/`hv`) и `escalation` (`revenue`/`cost`). `k£` в лейбле или колонке Units → `unit=money`, `currency=GBP`, `scale=k` (не `null`). Текст ячейки единицы тоже роль `unit`, даже если это формула: `EUR'000` / `CUR'000` → currency EUR, scale `k`, factor 1000; `EUR/MWh` → `hints.unit=price`, `hints.unit_per=MWh`; `x` → `ratio`; `Date` → `date`. Целая ячейка `Index` — `ratio`; `Base Index` единицей не является, а заголовок колонки `Index` остаётся header. Хвост `p.a.`, `pa` или `per annum` снимается первым (`MWh p.a.` остаётся count, `EUR p.a.` остаётся money, `% p.a.` остаётся rate). `#` и `№` — count. `%` и percent-format → `rate` и не отдаёт деньги подписи `EUR'000` в той же строке; percent-format оставляет явную подпись count, такую как `MWh p.a.`, значением count. Голый `per year` без `%` тоже `rate`, а `months per year` — `count`. Mapping `value_kind` для prune по-прежнему `count` на длительностях; в context длительность — `years`. Schema `1.13.0`, поля hints аддитивны (`unit_per`). Колонка Time в Markdown — `hints.time_semantics` / `period_position` / `aggregation`. `flow` → `during_period`/`sum`, `bop` → `beginning`/`first`, `eop` и `stock` → `end`/`last`, `rate` → `during_period`/`average`, `instant` → `instant`/`none`. Статическая строка — весь блок `params` либо fact, helper или flag со значением слева от линейки и без кэша в периодах — получает позицию `instant` и агрегирование `none`, поэтому точка печатается `instant/instant/none`. Статическая ставка сохраняет смысл `rate` и печатается `rate/instant/none`. Заполненный денежный год остаётся `flow/during_period/sum`. Заполненная годовая ставка остаётся `rate/during_period/average`. Колонки сценария — альтернативы, и агрегирование `none` не даёт их суммировать. Слово `none` — это агрегирование. Instant-концепт (`val.irr`, `val.npv`, мощность, число турбин) использует этот точечный профиль, когда колонки периодов пусты, и `stock`, когда в этих колонках лежит ряд. `scale_factor` (1 / 1000 / 1000000 / 1000000000) и `normalized_values` в базовых единицах. Шум `|x| < 1e-6 · max|series|` в normalized становится `0`; кэш Excel не меняется. `numeric_summary` печатает тем же форматом. `values` остаётся кэшем Excel. `value_statuses` отличает `cached`, явный `zero_explicit`, пустую `empty` и `not_applicable` вне фазы. В `context.md` это колонка Time и текст ячейки, не второй документ. Смысл, роль и денежная семантика — отдельные поля, см. выше. Unknown сразу полезен даунстриму.

Fact-строки в `params`-блоке — `article_role=assumption` (INDEX живого сценария не делает их calculation). ALL-CAPS секции без числа — `abstract`, `disposition=header`, не concept. Строка **Scenario Chosen** — `flag` / `context_role=scenario_selector`: каскад её не тегирует, но строка блока обязана держать индекс (ячейка D).

Строка блока — запись на **каждую** layout-строку: `kind`, `disposition`, `indent`, `hidden`, `label_path`, `neighbors`, одна `formula` / exceptions, `numeric_summary`, `cells` (роли `value` / `unit` / `scenario` / `total` / `stub` / `note`, плюс `header` — подпись колонки: Start, Live Case, Min). `values` — кэш или `null` по оси блока. Generic `Total` / `Balance b/f` / `Balance c/f` резолвится вместе с последней секцией `label_path`. `Debt` в «Sources of funds» → `cf.drawdown`; `Share premium` в «Uses of funds» → `cf.uses`. Relation `roll_forward` ставит b/f = bop, движение = flow, c/f = eop. Пустая ячейка покрытия (`dscr`, `coverage`, `cfads`, `dividend`) в construction — `not_applicable`, не warning «missing cached values». Cell-level adjacency и AST — в IR, см. [graph.md](graph.md). Инвариант: сумма `blocks[].rows` равна числу layout-строк **принятых** блоков. Отброшенные Cover / Shortcuts в знаменатель не входят. Нарушение — warning `Content completeness N/M`.

Top-3 `candidates` пишутся и при abstain: если prune опустошил fused-список, в context остаются сырые proposals.

## Glossary

Общий файл `$DATA_DIR/shared/label_memory.json`. Ключ `(normalized_label, section, unit)`. `section` — ближайший заголовок (`section_path[-1]`, иначе родитель). `unit` — `money`, `rate`, `years` или пусто. Запись: `concept_id`, `score`, `source`. Все сессии его читают и дописывают. Старый `$DATA_DIR/sessions/{session}/glossary.json` остаётся парой `(label, parent)`, с запасным `section_class`; пара без балла считается баллом 1.0. Новые пары в файл сессии не пишутся. Перед каскадом `reconcile_glossary` переписывает сессионную пару, чей лейбл теперь принадлежит другому концепту (иначе split duration остался бы на старом id). Живая пара отчётов (`pnl.revenue` и `cf.receipts`) остаётся на своём ключе. В общем файле такие лейблы расходятся секцией и единицей.

После джоба в общую память попадают строки с `confidence = high` и `source` из `{glossary, rule, structure, lexical, embed}`. У rule, structure, lexical и glossary балл 1.0. У embed балл — косинус, и он пишется только при `high` (косинус не ниже `embed_score_min`). Chat не пишется. Второй концепт или воздержавшаяся строка на той же тройке ключ не пишет и стирает уже лежащую тройку. Под замком файл читается заново: балл строго выше заменяет `concept_id`, равный или более низкий оставляет прежнюю запись. При сохранении книга стирает старые пары, которых сама касается: `(label, parent)` и `(label, section_class)`.

Ключ `section_class` в общий файл не пишется. Голый `Total` под конкретной секцией в память не ищется.

Это кэш уверенных совпадений, не место для костылей одной модели. Воздержавшаяся строка, чей ближайший альтернативный балл ниже `mint_score_max`, дописывается в `shared/taxonomy.json`. Пустой список альтернатив в этот файл не попадает, и руками файл не правят. Параметры `Months per year`, `Thousand`, `On`, `Off` и голый `Total` остаются без id.

## Смысл и роль

Выбранный `concept_id` — **отчётный концепт** этой строки (его ждут расчёты и gold). Он не исчерпывает смысл. Один лейбл живёт на разных уровнях: P&L `Gross revenues` → `pnl.revenue`; CFS `Gross Revenues` → `cf.receipts`, участие в CFADS — роль `cfads_input`, не концепт `cf.cfads`. `Equity` на балансе → `bs.equity`; `Equity (k£)` в Sources → `cf.equity_issue` плюс роль `cf.sources`.

На строке context (`1.13.0`) и в `MappedRow` три поля. Кандидаты top-3 остаются сырыми сигналами и **не** считаются взаимозаменяемыми концептами.

| Поле | Что это |
| --- | --- |
| `semantic_identity` | `family` + экономический `concept_id` + `confidence`. Для денежной проекции, чей лейбл называет начисление (`Gross Revenues` на CFS), identity — `pnl.revenue`, а выбранный `concept_id` — `cf.receipts`. Более узкий ребёнок побеждает родителя (`ops.inflation_revenue`, не `ops.inflation`). Эмиссия в Sources — `cf.equity_issue`, не остаток `bs.equity` |
| `reporting_roles` | Где используется **эта** строка. Выбранный концепт с `selected: true`, плюс `context_role` (`uses`, `sources`, `cfads_input`, `assumption`, …) и layout-концепты `cf.uses` / `cf.sources`. `cf.cfads` сюда не копируется |
| `cash_semantics` | `recognition`: `accrual` / `cash` / `noncash` / `rate` / `stock`. `cash_movement`: `inflow` / `outflow` / `none`. `Capitalized Interest` остаётся `pnl.interest` (gold), но recognition `noncash`: капитализация — не расход P&L |

`secondary_concepts` по-прежнему несёт `cf.uses` / `cf.sources` для любой строки секции, не только для capex. Участие в CFADS — только `context_role=cfads_input`.

Секционный rollup `cf.capex` не вешается на fee / arrangement: это не capex, даже если строка лежит в Uses.

## Уверенность

| source | confidence |
| --- | --- |
| glossary, rule, structure, lexical | `high` |
| embed | `high` при score ≥ `embed_score_min`, иначе `medium` |
| chat | `medium` |
| question (abstain) | `low` |

В `mapping.json` решение лежит в `source` (`glossary`, `rule`, `lexical`, `structure`, `embed`, `chat`, `question`). В строке `context.json` то же решение — `mapping.source`, а `mapping.method` схлопывает его: `glossary` / `rule` / `lexical` → `rule`, `chat` → `llm`, `question` → `unmapped`. `structure` и `embed` не меняются.

## Качество

`finance_context.mapping.eval`:

- **content completeness** — строки блоков / layout rows; должна быть 1.0;
- **concept coverage** — доля annotatable (mapped + abstained, без excluded) с принятым концептом. Это покрытие слота `concept_id`, не семантическая полнота; при нуле abstain значение равно 1.0;
- **mapping quality** — проверки принятых строк, объект `mapping_stats.mapping_quality`:
  - `label_coverage` — лейбл совпадает с фразой концепта (`labels`, `aliases`, `exact_labels`), evidence содержит `label matches`, заголовок поддерживает концепт в обе стороны, роль презентации (`total`, `opening`, `closing`, `balance b/f`, `balance c/f`, `brought forward`, `carried forward`, `additions`) подтверждена evidence, либо лейбл называет пару отчёта. Лейбл, равный полной фразе другого концепта, заголовок не спасает, кроме пар revenue/receipts, opex/opex paid, tax/tax paid, interest/interest paid и equity/equity issue. Evidence, где сказано только `alias of`, поддержкой не является. Закрытые стемы: operations→operating, expenses→expense, costs→cost, revenues→revenue, dividends→dividend. `normalize_label` остаётся ключом памяти и этой проверкой не является;
  - `semantic_coverage` — есть `semantic_identity` и `cash_semantics`; на CFS revenue/opex/tax/interest стоят денежные близнецы, а identity хранит экономический `pnl.*`, если лейбл его называет; `bs.*` — stock и время `stock|bop|eop`; capitalized interest — `noncash`; `cf.repayment` — outflow и stock `bs.debt` в том же блоке; начисление и выплата одного family не схлопываются в один `concept_id`;
  - `unit_coverage` — `hints.unit` совпадает с единицей концепта. Ставка и коэффициент — одна семья pure, подсказка years подходит к `count`, подпись ячейки `index` и хвост `p.a.` читаются как единица этой ячейки, пустая ячейка не проходит. Id концепта ожидаемую единицу не назначает (`pnl.volume` → count, денежные pnl/cf/bs/debt → money). Концепт без объявленной единицы вне этих отчётов не считается деньгами. Ожидаемая единица — фасет, записанный до того, как enrich ставит пустой единице money, поэтому дата на `ops.model_start` проходит. Price удовлетворяет money. Окончание id `_rate` ожидаемую единицу не назначает;
  - `temporal_coverage` — opening — это `bop`, closing — `eop`; balance или `bs.*` — `stock`, `bop`, `eop` или `instant`, а движение roll-forward может остаться `flow`; `period_type=instant` принимает `instant`, `stock`, `bop`, `eop` или `rate` раньше проверки ставки; ratio принимает `stock`, `instant`, `bop`, `eop`, `flow` или `rate`; rate-концепт без instant требует `time_semantics=rate`;
  - `formula_coverage` — у строки с формулой есть fingerprint (`formula`); ряд значений — кэш, отдельный A1 на ячейку в context не копируется (нет таких строк → 1.0). Если по периодным колонкам шаблона нет, fingerprint берётся из скаляра слева от оси;
  - `confidence_threshold_passed` — нет провалов semantic-проверок, у каждой принятой строки `confidence=high` и `score >= concept_accept_min`.
- **selective risk** — ошибки среди **принятых** маппингов (abstain в риск не входит);
- **abstain rate** и **risk–coverage** кривая — качество права отказаться.

В шапке `context.md` печатаются completeness, concept coverage и шесть полей quality отдельно.

Золотые ожидания для `resources/cashflow.xlsx`: `tests/fixtures/mapping/cashflow_dispositions.yaml`. Публичный корпус (MIT / CC-BY-NC-SA, не в git): `uv run python scripts/fetch-corpus.py` → `resources/corpus/` (Packt, RVI; three-statement в lock может 404). Gold: `packt_project_finance_dispositions.yaml`, `rvi_project_finance_dispositions.yaml`. Помимо «должно быть» gold знает **негативы** `forbidden_concept_id` (`Equity Injected` ≠ `bs.equity`, CFS `Variable land lease` ≠ `ops.lease_rate`, generation ≠ `ops.availability`). Тесты корпуса скипятся, если книги не скачаны или `expectations` пусты.

## Как расширять маппинг

1. Сначала [таксономия](taxonomy.md): есть ли смысл, или это тот же id с другим лейблом.
2. Если смысл есть, а каскад молчит — уточнить `labels` / `aliases` / `section_hints` / `skip_concept` / `unless`, не широкий `anti_labels` (`cash in` убивает `Cash in hand`; голый `lease` убивает land lease). Если ложный тег от секции — `unless` на parent-rollup, не понижать порог.
3. Если формула однозначна (копия с другого листа, SUM детей) — это задача structure, не chat.
4. Новый *тип* совпадения — новый `Signal` + тесты в `tests/mapping/`.
5. Не включать строку в exclude, если это бизнес-атрибут.
6. Прогнать `uv run pytest` и сценарий из [review.md](review.md).
