# Таксономия

Канонические финансовые смыслы живут в [`src/finance_context/ontology/taxonomy.yaml`](../src/finance_context/ontology/taxonomy.yaml). Загрузка: `load_taxonomy` → `validate_taxonomy` → `enrich_concept` (наследование фасетов).

Таксономия — не словарь синонимов одной Excel-книги. Концепт добавляют, когда появляется **новое значение**. Новый лейбл того же значения — `labels` или `aliases`. Id концепта стабилен (SKOS); версионируется документ (`version`), а не ключ.

Оси line item взяты из [FAST Standard 3.01](https://www.fast-standard.org/) и оформлены как атрибуты концепта, как `periodType` / `balance` в XBRL, а не как части составного ключа.

Как каскад использует эти поля — в [mapping.md](mapping.md). Как собираются блоки — в [layout.md](layout.md). Как закрывать дыры после прогона — в [review.md](review.md).

## Модель концепта

```yaml
version: 2
facet_defaults:
  cf: {statement: cf, nature: flow, basis: cash}
concepts:
  - id: cf.receipts.other
    labels: [Other income cash, Miscellaneous receipts]
    aliases: [Other Income]
    broader: cf.receipts
    facets: {direction: inflow}   # остальное наследуется
    definition: ...               # опционально
    exact_labels: [GMV]           # опционально, форс при точном лейбле
    deprecated: false
    replaced_by: null
    match: {}                     # зарезервировано под IFRS/US-GAAP crosswalk
calculations:
  - parent: cf.net
    terms: [{concept: cf.receipts, weight: 1}, {concept: cf.disbursements, weight: -1}]
```

| Поле | Назначение |
| --- | --- |
| `id` | Стабильный ключ. Префикс задаёт default-фасеты |
| `labels` | Канонические фразы для lexical и эмбеддингов |
| `aliases` | Дополнительные фразы той же сущности (часто «как в книге») |
| `broader` | Родитель в иерархии; SUM детей может унаследовать этот id |
| `facets` | Оси FAST/XBRL: statement, nature, basis, direction, position, series, unit |
| `value_kind` | Алиас `facets.unit` на время миграции |
| `statements` | Совместимость; заполняется из `facets.statement`, если пусто |
| `section_hints` | Lexical срабатывает, только если хинт виден в контексте строки |
| `anti_labels` | Жёсткий guard: подстрока в лейбле **выкидывает** этот концепт. Не скоринг и не место для широких фраз (`cash in`, `lease`) |
| `exact_labels` | Если лейбл строки совпал — концепт форсируется |
| `definition` | Текст для людей и для embed-запроса |
| `deprecated` / `replaced_by` | Снятие концепта без переименования id |
| `role` | Зарезервировано, на каскад не влияет |

Lexical индексирует **и** `labels`, **и** `aliases`. Embed строит векторы по `labels` (без aliases). Поэтому редкую формулировку, которую должен ловить kNN, лучше продублировать в `labels`.

## Фасеты (оси)

Наследуются: prefix `facet_defaults` → предки `broader` → явные поля концепта. Ребёнок не может противоречить родителю.

| Ось | Значения | Откуда |
| --- | --- | --- |
| `statement` | pnl, bs, cf, cov, val, ops, fx | XBRL statement / prefix |
| `nature` | flow, balance | FAST: flow vs stock; XBRL periodType |
| `basis` | cash, accrual, noncash | FAST: cash or not-cash |
| `direction` | inflow, outflow | FAST; XBRL balance (для flow) |
| `position` | opening, closing | FAST BF/CF (для balance) |
| `series` | constant, series | FAST: constant vs time series |
| `unit` | money, rate, ratio, count | FAST unit / бывший `value_kind` |

Коллизия `Other Income`: это два концепта с разным `basis` (accrual vs cash), а не один id с `anti_labels`.

## Что заполняется само

`enrich_concept`:

- фасеты — наследование, как выше;
- пустой `unit` → **`money`**;
- пустые `statements` — из `facets.statement`;
- пустой `definition` — `"{id}: {labels}"`.

Явно указывайте `facets.unit` / `facets.statement`, если дефолт префикса врёт (KPI ликвидности, `debt.scheduled_payment` как cash flow).

При загрузке проверяются уникальность id, существование `broader`, циклы, `deprecated` без `replaced_by`.

## Семейства id

Полный список — yaml. Ниже карта смыслов, не дамп.

**PnL (`pnl.*`)** — выручка, объём (в т.ч. Traffic), цена, GMV, COGS, маржа, OPEX, EBITDA/EBIT, D&A, процент и ставка, налог / deferred / tax rate / accrued, net income, other income, pre-tax / taxable. `Income Tax` на CFS/ОДДС — `cf.tax_paid`, не `pnl.tax`.

**Баланс (`bs.*`)** — итоги активов, PPE (в т.ч. Long term assets), обязательства, капитал (Net Assets как NAV), share capital / share premium, goodwill, cash, debt, AR/AP, запасы, RE, NWC, purchases к целевым дням запасов.

**Движение денег (`cf.*`)** — CFO, D&A add-back, capex, дивиденды, эмиссия, FCF, net CF (в т.ч. голый `Cash Flow`), CFADS, sources/uses, `cf.debt_service` (итог principal+interest; голое `Debt service` по-прежнему exact на `debt.scheduled_payment`); **поступления** `cf.receipts` и дети; **выплаты** `cf.disbursements` и дети; погашение и выборка; `cf.tax_paid`.

**Долг (`debt.*`)** — scheduled PMT, commitment / up-front fee (деньги) и `debt.upfront_fee_rate` (ставка), `debt.facility_amount` (размер линии, не остаток), revolver limit, available credit, sculpting. Остатки долга — `bs.debt`, не `debt.*`. DSRA — `bs.dsra`.

**Ликвидность (`liq.*`)** — min cash, pre-revolver cash, cash headroom, trough cash / week, total liquidity, runway, daily burn, conversion / operating cash ratio / liquidity coverage.

**Ковенанты (`cov.*`, `covenant.headroom`)** — факт DSCR / Average / Minimum DSCR по ряду (`cov.dscr`); порог `DSCR minimum` (`cov.dscr_limit`); LLCR, PLCR, leverage limit / headroom. Не путать с `liq.cash_headroom`.

**Операции (`ops.*`)** — lifetime, capacity / MW, число турбин (`ops.asset_count`, не headcount), generation / MWh, availability, CPI, inflation / PPA escalation. Не мапить phasing 0.2/0.8 на финансовый id.

**Прочее** — `val.npv` / `irr` / `wacc` / `val.coc` (Cost of capital, если это не тот же WACC) / `val.fcfe_equity` / `val.total_investment`; `ops.headcount`; `fx.*` (курс и переоценки).

Одинаковый человеческий лейбл может быть **двумя** концептами. Пример: `Other Income` в секции REVENUE EARNED → `pnl.other_income` (`basis: accrual`); в CASH INFLOWS → `cf.receipts.other` (`basis: cash`). `Income Tax` на P&L → `pnl.tax`; на CFS → `cf.tax_paid` (skip + pattern по секции, не `statement=cf` на весь лист). `DSCR minimum` (константа ковенанта) → `cov.dscr_limit`; `Minimum Debt Service Coverage Ratio` (статистика ряда) → `cov.dscr`. Разведение — фасеты и паттерны, не один общий id.

## Когда что менять

| Ситуация | Действие |
| --- | --- |
| В модели новое *значение* (runway, commitment fee, cash tax) | Новый `id` + labels + facets |
| Тот же смысл, другая формулировка (`IT & Telecom`, `Drawdowns`, `Cashflow …`) | `labels` или `aliases`; нормализатор уже знает plural и `cashflow` |
| Частный вид уже известного тотала (Product collections) | Дочерний id с `broader` |
| Лейбл сталкивается с чужим концептом (Headroom) | Сначала фасеты, секция, `skip_concept` / `unless`. `anti_labels` / `section_hints` — точечный guard, не широкая подстрока |
| Строка — check, circular, «from MF» без бизнеса | Exclusion, не концепт |
| Формула копирует уже замапленную строку | Ничего в yaml; это structure |

Не кладите в yaml per-workbook костыли вроде уникального id `cashflow.xlsx.row33`. Не добавляйте alias, который на другом отчёте значит другое.

## `broader` и итоги

Дети делят родителя:

- `cf.receipts.product` → `cf.receipts`
- `cf.disbursements.payroll` → `cf.disbursements`

Structure на `SUM` ищет общий id детей, общий `broader` или объявленный `calculations` parent — и только когда **все** fact-члены диапазона уже замаплены. Частичный SUM не копирует единственного ребёнка на родителя. Итог поступлений не должен стать `cf.net`. Знакопеременный equity IRR (`Total Cash in/Cash out`) — отдельный `cf.equity_cashflow`, не alias к `cf.receipts` / `cf.fcf`. Net объявлен как разность inflows−outflows в yaml. Несовпадение с наблюдённым SUM снижает score и обычно даёт `calculation_conflict`; keep-rule оставляет exact `Cash Flow` под IRR.

## `facets.unit` (`value_kind`)

Совместимость при prune: money только к money; count к count; rate к rate; ratio совместим с rate. Строка с денежным рядом не мапится на headcount.

- Процент / ставка налога / FX rate / WACC / CoC / IRR / эскалация → `rate` (ratio-строка совместима с rate-концептом)
- DSCR, leverage, conversion, runway, coverage, CPI, availability, FCFE/Equity → `ratio`
- Headcount, trough week, lifetime, turbines, traffic, generation, MW → `count`
- Остальное, включая proration (`сумма / дни в месяце`) → `money`

Если формула — деление, но лейбл про coverage — побеждает семантика лейбла (`_semantic_ratio`, **токены**, не подстрока: `ratio` ⊂ `generation` не считается).

## Чеклист PR

1. Нужен ли новый id или хватает alias.
2. Префикс id согласован с семьёй; отличия — в `facets`, не в новом префиксе.
3. `labels` на английском и, если живёт в книгах, русском; узкие формулировки модели — в `aliases`.
4. Коллизии закрыты фасетами; `section_hints` / `anti_labels` — только если фасета недостаточно.
5. Если есть родитель — `broader` указывает на существующий id; фасеты ребёнка не спорят с родителем.
6. Gold: «должно быть» и **негативы** `forbidden_concept_id` в `tests/fixtures/mapping/cashflow_dispositions.yaml` или корпусном fixture.
7. `uv run pytest`. Не понижать `ACCEPT_MIN`, чтобы тест позеленел. Корпус: `uv run python scripts/fetch-corpus.py`.

## Антипаттерны

- Один alias на два смысла без секции.
- Широкий `anti_labels` (`cash in` / `cash out`, голый `lease`) вместо unit + секция + соседи.
- Ближайший money-концепт для KPI, потому что «хоть что-то» (lifetime под OPEX, Cash Flow → `bs.cash`).
- Parent-rollup без `unless` на годы / MW / индексы / opening-closing.
- Подстрока в `_semantic_ratio` (`ratio` внутри `generation`).
- `statement=cf` на весь лист Cashflow, из-за которого выручка ОДДС уезжает с `pnl.revenue`.
- Ручная запись в `glossary.json` вместо yaml: glossary перезапишется со следующих high-confidence джобов и не попадёт в git.
- Exclude для «непонятной» бизнес-строки.
- Дублирование id с разным регистром или синонимы `cf.receipts` / `cf.inflows` без `broader`.
