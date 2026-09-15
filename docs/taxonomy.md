# Таксономия

Канонические финансовые смыслы живут в [`src/finance_context/ontology/taxonomy.yaml`](../src/finance_context/ontology/taxonomy.yaml). Загрузка: `load_taxonomy` → каждый элемент валидируется как `Concept` и дополняется `enrich_concept`.

Таксономия — не словарь синонимов одной Excel-книги. Концепт добавляют, когда появляется **новое значение**. Новый лейбл того же значения — `labels` или `aliases`.

Как каскад использует эти поля — в [mapping.md](mapping.md). Как закрывать дыры после прогона — в [review.md](review.md).

## Модель концепта

```yaml
- id: cf.receipts.other
  labels: [Other income cash, Miscellaneous receipts]
  aliases: [Other Income]
  broader: cf.receipts
  statements: [cf]
  section_hints: [cash inflow, collection]
  anti_labels: [accrual, revenue earned]
  definition: ...          # опционально
  value_kind: money        # опционально, иначе из id / таблицы
  role: ...                # опционально
```

| Поле | Назначение |
| --- | --- |
| `id` | Стабильный ключ. Префикс задаёт семью и default `statements` |
| `labels` | Канонические фразы для lexical и эмбеддингов |
| `aliases` | Дополнительные фразы той же сущности (часто «как в книге») |
| `broader` | Родитель в иерархии; SUM детей может унаследовать этот id |
| `statements` | Допустимые типы отчёта: `pnl`, `bs`, `cf`, `cov`, `val`, … |
| `value_kind` | `money` / `rate` / `ratio` / `count` |
| `section_hints` | Lexical срабатывает, только если хинт виден в контексте строки |
| `anti_labels` | Блок: подстрока в лейбле строки выкидывает этот концепт |
| `definition` | Текст для людей и для embed-запроса; если пусто — собирается из id и labels |
| `role` | Зарезервировано, на каскад сейчас почти не влияет |

Lexical индексирует **и** `labels`, **и** `aliases`. Embed строит векторы по `labels` (без aliases). Поэтому редкую формулировку, которую должен ловить kNN, лучше продублировать в `labels`.

## Что заполняется само

`enrich_concept` (`facets.py`):

- пустые `statements` — из префикса id;
- пустой `value_kind` — из таблицы исключений (`pnl.tax_rate` → `rate`, `cov.dscr` → `ratio`, …) иначе **`money`**;
- пустой `definition` — `"{id}: {labels}"`.

Префикс → default statement:

| Префикс id | statement |
| --- | --- |
| `pnl` | `pnl` |
| `bs` | `bs` |
| `cf`, `liq` | `cf` |
| `debt` | `bs` |
| `cov`, `covenant` | `cov` |
| `val` | `val` |
| `ops` | `ops` |
| `fx` | `fx` |

Явно указывайте `statements` и `value_kind`, если дефолт врёт (например KPI ликвидности с `value_kind: ratio`).

## Семейства id

Полный список — yaml. Ниже карта смыслов, не дамп.

**PnL (`pnl.*`)** — выручка, объём, цена, GMV, COGS, маржа, OPEX, EBITDA/EBIT, D&A, процент и ставка, налог / deferred / tax rate / accrued, net income, other income, pre-tax / taxable.

**Баланс (`bs.*`)** — итоги активов, PPE, обязательства, капитал, cash, debt, AR/AP, запасы, RE, NWC, purchases к целевым дням запасов.

**Движение денег (`cf.*`)** — CFO, D&A add-back, capex, дивиденды, эмиссия, FCF, net CF; **поступления** `cf.receipts` и дети (product / service / subscription / other); **выплаты** `cf.disbursements` и дети (payroll, rent, utilities, insurance, occupancy, supplier, marketing, professional, IT, travel, other); погашение и выборка (`cf.repayment`, `cf.drawdown`); `cf.tax_paid` (cash tax, broader = disbursements).

**Долг (`debt.*`)** — scheduled PMT, commitment fee, revolver limit, available credit. Остатки долга — `bs.debt`, не `debt.*`.

**Ликвидность (`liq.*`)** — min cash, pre-revolver cash, cash headroom, trough cash / week, total liquidity, runway, daily burn, conversion / operating cash ratio / liquidity coverage.

**Ковенанты (`cov.*`, `covenant.headroom`)** — DSCR, LLCR, PLCR, leverage limit / headroom, запас до ковенанта. Не путать с `liq.cash_headroom`.

**Прочее** — `val.npv` / `irr` / `wacc`; `ops.headcount`; `fx.*` (курс и переоценки).

Одинаковый человеческий лейбл может быть **двумя** концептами. Пример: `Other Income` в секции REVENUE EARNED → `pnl.other_income`; в CASH INFLOWS → `cf.receipts.other`. Разведение — `section_hints` + `anti_labels` + `aliases`, не один общий id.

## Когда что менять

| Ситуация | Действие |
| --- | --- |
| В модели новое *значение* (runway, commitment fee, cash tax) | Новый `id` + labels + facets |
| Тот же смысл, другая формулировка (`IT & Telecom`) | `labels` или `aliases` существующего id |
| Частный вид уже известного тотала (Product collections) | Дочерний id с `broader` |
| Лейбл сталкивается с чужим концептом (Headroom) | `anti_labels` / `section_hints` на обоих |
| Строка — check, circular, «from MF» без бизнеса | Exclusion, не концепт |
| Формула копирует уже замапленную строку | Ничего в yaml; это structure |

Не кладите в yaml per-workbook костыли вроде уникального id `cashflow.xlsx.row33`. Не добавляйте alias, который на другом отчёте значит другое.

## `broader` и итоги

Дети делят родителя:

- `cf.receipts.product` → `cf.receipts`
- `cf.disbursements.payroll` → `cf.disbursements`

Structure на `SUM` ищет общий id детей или общий `broader`. Итог поступлений не должен стать `cf.net`. Net — отдельный концепт или diff inflows−outflows.

## `value_kind`

Совместимость при prune: money только к money; count к count; rate к rate; ratio совместим с rate. Строка с денежным рядом не мапится на headcount.

- Процент / ставка налога / FX rate / WACC / IRR → `rate`
- DSCR, leverage, conversion, runway, coverage → `ratio`
- Headcount, trough week → `count`
- Остальное, включая proration (`сумма / дни в месяце`) → `money`

Если формула — деление, но лейбл про coverage — побеждает семантика лейбла (`_semantic_ratio`).

## Чеклист PR

1. Нужен ли новый id или хватает alias.
2. Префикс id согласован с семьёй; при необходимости явные `statements` и `value_kind`.
3. `labels` на английском и, если живёт в книгах, русском; узкие формулировки модели — в `aliases`.
4. Коллизии закрыты `section_hints` / `anti_labels`.
5. Если есть родитель — `broader` указывает на существующий id.
6. Gold: строка в `tests/fixtures/mapping/cashflow_dispositions.yaml` (label + parent, если важен контекст).
7. `uv run pytest` (как минимум mapping/eval + extract). Не понижать `ACCEPT_MIN`, чтобы тест позеленел.

## Антипаттерны

- Один alias на два смысла без секции.
- Ближайший money-концепт для KPI, потому что «хоть что-то».
- Ручная запись в `glossary.json` вместо yaml: glossary перезапишется со следующих high-confidence джобов и не попадёт в git.
- Exclude для «непонятной» бизнес-строки.
- Дублирование id с разным регистром или синонимы `cf.receipts` / `cf.inflows` без `broader`.
