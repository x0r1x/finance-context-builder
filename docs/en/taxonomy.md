# Taxonomy

[Русский](../ru/taxonomy.md) · **English**

Canonical financial meanings live in [`src/finance_context/ontology/taxonomy.yaml`](../../src/finance_context/ontology/taxonomy.yaml). Load path: `load_taxonomy` → `validate_taxonomy` → `enrich_concept` (facet inheritance).

The taxonomy is not a synonym list for one Excel book. Add a concept when a **new value** appears. A new label of the same value is `labels` or `aliases`. A concept id is stable (SKOS); the document is versioned (`version`), not the key.

Line-item axes come from the [FAST Standard 3.01](https://www.fast-standard.org/) and are concept attributes, like `periodType` / `balance` in XBRL, not parts of a composite key.

How the cascade uses these fields: [mapping.md](mapping.md). How blocks are built: [layout.md](layout.md). The cell-level formula graph: [graph.md](graph.md). How to close gaps after a run: [review.md](review.md).

## Concept model

```yaml
version: 2
facet_defaults:
  cf: {statement: cf, nature: flow, basis: cash}
concepts:
  - id: cf.receipts.other
    labels: [Other income cash, Miscellaneous receipts]
    aliases: [Other Income]
    broader: cf.receipts
    facets: {direction: inflow}   # the rest is inherited
    definition: ...               # optional
    exact_labels: [GMV]           # optional, forced on an exact label
    deprecated: false
    replaced_by: null
    match: {}                     # reserved for an IFRS/US-GAAP crosswalk
calculations:
  - parent: cf.net
    terms: [{concept: cf.receipts, weight: 1}, {concept: cf.disbursements, weight: -1}]
```

| Field | Purpose |
| --- | --- |
| `id` | Stable key. The prefix sets default facets |
| `labels` | Canonical phrases for lexical match and embeddings |
| `aliases` | Extra phrases of the same entity (often “as written in the book”) |
| `broader` | Parent in the hierarchy; a SUM of children may inherit this id |
| `facets` | FAST/XBRL axes: statement, nature, basis, direction, position, series, unit, period_type |
| `value_kind` | Alias of `facets.unit` during migration |
| `statements` | Compatibility; filled from `facets.statement` when empty |
| `section_hints` | Lexical fires only when the hint is visible in the row context |
| `anti_labels` | Hard guard: a substring in the label **drops** this concept. Not a score, and not a place for wide phrases (`cash in`, `lease`) |
| `exact_labels` | If the row label matches, the concept is forced |
| `definition` | Text for people and for the embed query |
| `deprecated` / `replaced_by` | Retire a concept without renaming the id |
| `role` | Reserved; it does not affect the cascade |

Lexical indexes **both** `labels` and `aliases`. Embed builds vectors from `labels` (without aliases). A rare wording that kNN should catch is better duplicated into `labels`.

## Facets (axes)

Inheritance: prefix `facet_defaults` → `broader` ancestors → explicit concept fields. A child cannot contradict its parent.

| Axis | Values | Origin |
| --- | --- | --- |
| `statement` | pnl, bs, cf, cov, val, ops, fx | XBRL statement / prefix |
| `nature` | flow, balance | FAST: flow vs stock; XBRL periodType |
| `basis` | cash, accrual, noncash | FAST: cash or not-cash |
| `direction` | inflow, outflow | FAST; XBRL balance (for a flow) |
| `position` | opening, closing | FAST BF/CF (for a balance) |
| `series` | constant, series | FAST: constant vs time series |
| `unit` | money, rate, ratio, count | FAST unit / former `value_kind`. In context `hints.unit` is wider: `price` (`EUR/MWh`) and `date` are not facet values |
| `period_type` | instant, duration | XBRL `periodType`. `instant` on scalars and inputs (`val.npv`, `val.irr`, `ops.capacity`, `ops.model_start`, rates). `duration` is a flow over a period. An empty value does not narrow the cascade |

The `Other Income` collision is two concepts with a different `basis` (accrual vs cash), not one id with `anti_labels`.

## What is filled in automatically

`enrich_concept`:

- facets, inherited as above;
- an empty `unit` → **`money`**;
- empty `statements` from `facets.statement`;
- an empty `definition` → `"{id}: {labels}"`.

Set `facets.unit` / `facets.statement` explicitly when the prefix default lies (a liquidity KPI, `debt.scheduled_payment` as a cash flow).

Load checks unique ids, that `broader` exists, cycles, and `deprecated` without `replaced_by`.

## Id families

The full list is the yaml. Below is a map of meanings, not a dump.

**PnL (`pnl.*`)** — revenue, volume (including Traffic), price, GMV, COGS, margin, OPEX, EBITDA/EBIT, D&A, interest and the rate, tax / deferred / tax rate / accrued, net income, other income, pre-tax / taxable. `Income Tax` on CFS is `cf.tax_paid`, not `pnl.tax`.

**Balance sheet (`bs.*`)** — asset totals, `bs.assets_noncurrent` / `bs.assets_current`, PPE (including Long term assets), liabilities, equity (Net Assets as NAV), share capital / share premium, goodwill, cash, debt, AR/AP, inventory, RE, NWC, purchases toward target inventory days. A generic `Total` with no label of its own resolves from the last section of `label_path` (`Total` under Non-current assets → `bs.assets_noncurrent`). A unit token (`% p.a.`) is not part of `label_path`.

**Cash movement (`cf.*`)** — CFO, D&A add-back, capex, dividends, issuance, FCF, net CF (including a bare `Cash Flow`), CFADS, sources/uses, `cf.debt_service` (principal+interest total; a bare `Debt service` is still exact on `debt.scheduled_payment`); **receipts** `cf.receipts` and children; **disbursements** `cf.disbursements` and children (`cf.opex_paid`, `cf.interest_paid`, `cf.tax_paid`); repayment and drawdown. Cashflow Statement rows with the same labels as P&L (Gross Revenues, OPEX, Income Tax, Interest) map to `cf.*`, not `pnl.*`. CFADS is its own id, not a synonym of revenue.

**Debt (`debt.*`)** — scheduled PMT; **distinct** fees: `debt.commitment_fee` (undrawn commitment), `debt.arrangement_fee` / `debt.arrangement_fee_rate`, `debt.engagement_fee` / `debt.engagement_fee_rate`; generic `debt.upfront_fee_rate` only for an unspecified up-front. `debt.margin_rate`, `debt.gearing`, `debt.facility_amount` (facility size, not the balance), revolver limit, available credit, sculpting. Debt balances are `bs.debt`, not `debt.*`. DSRA is `bs.dsra`.

**Liquidity (`liq.*`)** — min cash, pre-revolver cash, cash headroom, trough cash / week, total liquidity, runway, daily burn, conversion / operating cash ratio / liquidity coverage.

**Covenants (`cov.*`, `covenant.headroom`)** — observed DSCR / Average / Minimum DSCR along the series (`cov.dscr`); the threshold `DSCR minimum` (`cov.dscr_limit`); LLCR, PLCR, leverage limit / headroom. Do not confuse with `liq.cash_headroom`.

**Operations (`ops.*`)** — `ops.concession_duration` (Concession Duration), `ops.operating_period` (Operations Duration / Operating lifetime), `ops.construction_period`; generic `ops.lifetime` only for an unspecified Lifetime / Project life. Concession is declared as construction + operating in `calculations`. Capacity / MW, turbine count (`ops.asset_count`, not headcount), generation / MWh, availability, CPI, `ops.inflation` (generic / PPA) plus children `ops.inflation_revenue` and `ops.inflation_cost`, `ops.volume_growth` (Traffic Evolution). Do not map phasing 0.2/0.8 onto a financial id. PC/HV is `hints.segment`; cost vs revenue inflation are separate ids **and** `hints.escalation`.

**Other** — `val.npv` / `irr` / `wacc` / `val.coc` (Cost of capital, when it is not the same WACC) / `val.fcfe_equity` / `val.total_investment`; `ops.headcount`; `fx.*` (the rate and revaluations).

The same label in different sections is different concepts, even on one sheet. `Debt` in “Sources of funds” → `cf.drawdown` (flow, inflow), not the stock `bs.debt` (`bs.debt` has an anti-section `sources of funds`). `Share premium` in “Uses of funds” → `cf.uses` (flow, outflow), not `bs.share_premium`. A `roll_forward` relation sets time: b/f = bop, the movement between b/f and c/f = flow, c/f = eop (`Retained earnings`, `Additions / subtractions`).

The same human label can be **two** concepts. Example: `Other Income` in a REVENUE EARNED section → `pnl.other_income` (`basis: accrual`); in CASH INFLOWS → `cf.receipts.other` (`basis: cash`). `Income Tax` on P&L → `pnl.tax`; on CFS → `cf.tax_paid` (skip + pattern + statement crosswalk alias). `Gross Revenues` on CFS → `cf.receipts`, not `pnl.revenue` and not `cf.cfads`. `DSCR minimum` (a covenant constant) → `cov.dscr_limit`; `Minimum Debt Service Coverage Ratio` (a statistic of the series) → `cov.dscr`. The split is facets, patterns, and row roles (`context_role`, `secondary_concepts`, `semantic_identity`, `reporting_roles`, `cash_semantics`), not several cascade winners. The selected `concept_id` is the reporting slot. The economic meaning lives in `semantic_identity` and may be another id of the same family (`cf.receipts` with identity `pnl.revenue`).

## When to change what

| Situation | Action |
| --- | --- |
| The model has a new *value* (runway, commitment fee, cash tax) | A new `id` + labels + facets |
| The same meaning, another wording (`IT & Telecom`, `Drawdowns`, `Cashflow …`) | `labels` or `aliases`; the normalizer already knows plurals and `cashflow` |
| A specific kind of an already known total (Product collections) | A child id with `broader` |
| A label collides with someone else's concept (Headroom) | Facets, section, `skip_concept` / `unless` first. `anti_labels` / `section_hints` are a narrow guard, not a wide substring |
| The row is a check, circular, or “from MF” with no business meaning | Exclusion, not a concept |
| A formula copies an already mapped row | Structure alias, but on CFS a P&L id goes through the statement crosswalk; `bs.*` is not copied onto Sources/Uses |

Do not put per-workbook workarounds in yaml, such as a unique id `cashflow.xlsx.row33`. Do not add an alias that means something else on another statement.

## `broader` and totals

Children share a parent:

- `cf.receipts.product` → `cf.receipts`
- `cf.disbursements.payroll` → `cf.disbursements`

Structure on a `SUM` looks for the children's shared id, a shared `broader`, or a declared `calculations` parent — and only when **every** fact member of the range is already mapped. A partial SUM does not copy the single child onto the parent. A receipts total must not become `cf.net`. A mixed-sign equity IRR (`Total Cash in/Cash out`) is its own `cf.equity_cashflow`, not an alias of `cf.receipts` / `cf.fcf`. Net is declared as inflows−outflows in yaml. A mismatch with the observed SUM lowers the score and usually yields `calculation_conflict`; the keep-rule keeps exact `Cash Flow` under IRR.

## `facets.unit` (`value_kind`)

Compatibility at prune: money only with money; count with count; rate with rate; ratio is compatible with rate. A row with a money series does not map to headcount.

- A percent / tax rate / FX rate / WACC / CoC / IRR / escalation → `rate` (a ratio row is compatible with a rate concept)
- DSCR, leverage, conversion, runway, coverage, CPI, availability, FCFE/Equity → `ratio`
- Headcount, trough week, lifetime, turbines, traffic, generation, MW → `count`
- Everything else, including proration (`amount / days in the month`) → `money`

If the formula is a division but the label is about coverage, label semantics win (`_semantic_ratio`, **tokens**, not a substring: `ratio` ⊂ `generation` does not count).

## PR checklist

1. Is a new id needed, or is an alias enough.
2. The id prefix matches the family; differences live in `facets`, not in a new prefix.
3. `labels` in English and, when the books use it, in Russian; narrow model wordings go in `aliases`.
4. Collisions are closed by facets; `section_hints` / `anti_labels` only when a facet is not enough.
5. If there is a parent, `broader` points at an existing id; the child's facets do not argue with the parent.
6. Gold: “must be” and **negatives** `forbidden_concept_id` in `tests/fixtures/mapping/cashflow_dispositions.yaml` or a corpus fixture.
7. `uv run pytest`. Do not lower `ACCEPT_MIN` to make a test green. Corpus: `uv run python scripts/fetch-corpus.py`.

## Antipatterns

- One alias for two meanings with no section.
- A wide `anti_labels` (`cash in` / `cash out`, a bare `lease`) instead of unit + section + neighbors.
- The nearest money concept for a KPI, because “something is better than nothing” (lifetime under OPEX, Cash Flow → `bs.cash`).
- A parent rollup with no `unless` for years / MW / indexes / opening-closing.
- A substring in `_semantic_ratio` (`ratio` inside `generation`).
- Copying a P&L id onto the Cashflow Statement through a structure alias with no crosswalk (`pnl.tax` instead of `cf.tax_paid`).
- `statement=cf` on the whole Cashflow sheet, which pulls `Cash in hand` / share premium off `bs.*`.
- A hand edit of `glossary.json` instead of yaml: glossary is rewritten from the next high-confidence jobs and is not in git.
- Exclude for a business row that is merely “unclear”.
- Duplicating an id with different case, or synonyms `cf.receipts` / `cf.inflows` without `broader`.
