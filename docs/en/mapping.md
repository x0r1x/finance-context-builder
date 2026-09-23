# Mapping

[Русский](../ru/mapping.md) · **English**

Mapping is retrieve-and-align, not closed-set classification. The taxonomy is a **dictionary**, not a content funnel: a row is not dropped when no concept is found. The resolver accepts a `concept_id` only above a threshold; otherwise a fact stays `unknown`, but the context still keeps the label, hints, neighbors, formula, and top-3 candidates.

Code: `src/finance_context/mapping/`. The stage entry point is `mapping_workbook` (`stage.py`) → `map_layout` (`cascade.py`). Full content is assembled by `build_context` (`context/build.py`), schema `1.13.0`.

Related documents: [layout](layout.md), [taxonomy](taxonomy.md), [graph](graph.md), [unmapped review](review.md), [architecture](architecture.md).

## What takes part

Layout marks the block body with row kinds. The cascade resolves **`fact` / `flag` / `helper`**. `abstract` and `index` do not enter `mapping.json`, but **every** layout row is written to `blocks[].rows` of its block. How blocks and labels are built: [layout.md](layout.md). If there are no fact rows, the cascade is not at fault: first the axis **or** the params shape and the label zone.

| kind | Role |
| --- | --- |
| `fact` | Candidate for a `concept_id`; period values on a timeline-block row, or role-tagged cells in params |
| `abstract` | Section header, parent of the following facts; `disposition=header` on the block row |
| `index` | Counters such as `Week #`; a row of its block |
| `helper` | Check / tie-out / Spare placeholder; excluded |
| `flag` | 0/1 timing and scenarios; excluded, not a financial unknown |

Labels from `is_noise_label` (`Dashboard`, `Assumptions`, `* chart`, `* bridge`, …) do **not** create rows in `mapping.json`, but they remain a block row (without a period series).

`needs_input` is counted only from questions on fact rows.

## Cascade

Order in `map_layout`:

1. `BookView` is built from layout, IR cells, and edges: `ir/cell_edges.parquet` first, otherwise `ir/edges.parquet`. Then formula patterns (`analyze_structure`), row adjacency, and row context (`RowContext`: label, parent, section, sheet, grain, `value_kind`, formula templates, `prev_labels` / `next_labels` ±2, `time_semantics`).
2. **Exclusion.** If `exclusion_reason(ctx)` is non-empty, the row is not resolved, disposition is `excluded`, and there is no question.
3. Up to **four** passes of `glossary + lexical + structure`. Alias/SUM need a neighbor that was mapped on the previous iteration (structure fixpoint).
4. Unresolved facts plus an enabled EmbedPort → dense retrieve over concept labels, then fuse/decide again together with lexical/glossary/structure.
5. What remains plus ChatPort → rerank of a short list. It may return `unknown`. It **must not invent an id** outside the taxonomy.
6. A final `structure`-only pass (pull in what opened up after embed/chat).
7. Assemble `MappingDocument`: `rows` + `questions` + structural `relations` (`alias` / `aggregate` / `difference` / `roll_forward` for the cascade). This is **not** the full cell graph: dependency completeness is in `ir/cell_edges.parquet`, `graph.json` `links`, and trace, not in `context.blocks[].relations`.

Each HTTP book runs in its own process, with its own chat and embedding clients. There is no shared mapping lock. A miss on `taxonomy_embeddings.npz` does not wait for someone else's embed: the lock covers only the write, and only when the file is still empty or stale. `glossary.json` is still appended under its own lock.

A repeat POST of a finished book (`context.json`, `context.md`, `graph.json`, `graph.md`, and a terminal `meta.json`) returns the snapshot and does not delete files. A repeat POST while that book's process is alive does not start a second process. Rebuilding layout, mapping, and context is `POST /v1/context-jobs?remap=1`: the old process is stopped, then a new one starts. Parse (`raw/`) and formula IR (`ir/cells.parquet`, `ir/edges.parquet`, `ir/cell_edges.parquet`) are reused only when `ir/compile.json` matches the column schema; otherwise compile is written again. `ir/graph_edges.parquet` is deleted on remap together with `graph.json`. The CLI does not run the pipeline into the same directory when both `context.json` and `context.md` are already on disk. If one of them is missing, stages still skip from their own artifacts (`raw/workbook.json`, the `ir/compile.json` stamp, `layout.json`, `mapping.json`, `graph.json`). Deleting only `context.json` does not rebuild mapping.

## Signals

A new matching idea is a new `Signal.propose(ctx, book) -> list[Candidate]`, not an `if` for one workbook.

| Signal | `source` on the row | Role |
| --- | --- | --- |
| `glossary` | `glossary` | Learned pair `(normalize(label), normalize(parent)) → concept_id`. The same `skip_concept` as lexical: on CFS `Gross Revenues` does not stay `pnl.revenue`, and `Equity` in Sources does not stay `bs.equity`. `reconcile_glossary` does not overwrite a live statement pair (`pnl.revenue` ↔ `cf.receipts`, `bs.equity` ↔ `cf.equity_issue`) |
| `lexical` | `rule` | Phrases from `labels` / `aliases`, section, `skip_concept`; `anti_labels` is a hard guard, not the main score |
| `structure` | `structure` | Formula graph, neighbors, priors from dependents |
| `embed` | `embed` | Cosine to concept-label embeddings |
| `chat` | `chat` | Rerank of the pruned list |

Lexical indexes **both** `labels` and `aliases`. Before comparison the label is normalized (`normalize_label`): parentheses are stripped, but metric abbreviations (`EBITDA`, `CFADS`, `DSCR`) inside parentheses are kept; `cashflow` → `cash flow`; `&` → `and`; `/` → space (`Total Cash in/Cash out` is compared with `Total Cash in Cash out`). Weak one-word phrases (`revenue`, `debt`, `total`, `cash`, …) do not match when they are the **entire** label; inside a compound label (`REVENUE - Passenger Car`) they do. For `cash` the weak match is also disabled next to `flow` / `in` / `out` / `total` (`Cash Flow` does not become `bs.cash`), **except** `hand` / `hands` / `balance` (`Cash in hand` → `bs.cash`); for `debt`, next to `fee` / `up-front`. Plurals (`Drawdowns`, `revenues`) reduce to the form in yaml. An anti-label with a slash (`fcfe /`) matches the raw string, so replacing `/` with a space does not block a bare `FCFE`.

Lexical also knows stable constructions from the `patterns:` block in yaml. That is **not** the same as a structure aggregate:

- **Parent rollup** (children inherit the section): `section_contains` `capex`/`uses` → `cf.capex`; `opex`/`operating`/`costs` → `pnl.opex`; `revenue` → `pnl.revenue`; a D&A section → `pnl.da`. Candidate score 0.9. Non-money children (lifetime, MW, CPI, share premium, balance b/f) are cut by `unless.label_contains` in the same patterns — otherwise assumptions become opex/revenue. A pattern hit is **not** filtered by the concept's `anti_labels` before prune; the stop for a rollup is `unless`.
- **Skip-pattern** forbids a concept in a section: `bs.ap` in debt; `pnl.tax` / `pnl.opex` / `pnl.revenue` / `pnl.interest` on `cfs` / `cash flow`, so a cash-flow statement does not stay P&L; `bs.equity` for `injected` / sources / construction; `cf.disbursements` for CFADS / available-for-debt. Do not set `statement=cf` on the **whole** sheet: `Cash in hand` stays `bs.cash`. Revenue/OPEX/tax/interest on CFS → `cf.receipts` / `cf.opex_paid` / `cf.tax_paid` / `cf.interest_paid` (pattern + alias crosswalk).
- `section_contains` with a space (`cash flow`) requires **every** token of the phrase in `section_tokens` (label ∪ parent ∪ path ∪ sheet). A single word `cashflow` does not exist after normalization — it is two tokens.
- The exact string `cash flow` (without available/operating/net) → `cf.net`.
- When a concept has `section_hints`, the phrase is accepted only if a hint appears in the label / parent / section path / sheet (for example `Arrangement fee` on Ratios: the hints include `ratios` / `irr`).

`_semantic_ratio` / `_semantic_count` look at **tokens** of the normalized label, not a substring: `ratio` inside `generation` does not make MWh a ratio. Ratio tokens: `dscr`, `coverage`, `cpi`, `inflation`, `availability` (not when `generation` is next to it), `wacc`, `coc`, … Count: `lifetime`, `turbine`, `traffic`, `generation`, `capacity`, `mw`. A bare `lease` token is **not** a ratio: a money `Variable land lease` on CFS is opex/cash; a rate is only when the series is `%` or zeros as an input. An explicit unit column (`%` / `years` / `£` / `£/year`) sets `value_kind` and is **not** overridden by label semantics. `£/year` is money, not count. Prune then drops incompatible concepts.

The facet `basis=accrual` is set from `accrual` / `accrued` / `revenue earned`, not from a bare `earned` — otherwise `Dividends earned` is pruned off `cf.dividends`.

### Structure

`analyze_structure` looks at formula templates along the period columns and attaches a `RowPattern`:

| kind | When | What it proposes |
| --- | --- | --- |
| `alias` | A cell equals one cell of another row/sheet | The same `concept_id` as the source (score ~0.96), **except** CFS: a P&L→`cf.*` crosswalk (0.94) and except `bs.*` on Sources/Uses. Example: `Dashboard!C17 = Weekly_Forecast!C39` |
| `aggregate` | `SUM` of neighboring fact rows | The children's shared concept or their `broader`, **only when every range member is mapped**. One mapped child (Insurance inside EBITDA) does not copy its concept to the parent. A total does not become `cf.net` just because it says “Total”. A SUM of mixed-sign equity lines under IRR → `cf.equity_cashflow`, not `cf.receipts`. Example: `Total Inflows = SUM(collections)` → `cf.receipts` |
| `diff` | Difference of two rows | A parent from `calculations` with opposite weights |
| `roll` | Balance roll-forward | The same balance-sheet concept as the linked row |
| neighbor prior | Neighbors ±2 | A money lease next to opex / Variable land lease → `pnl.opex`, not `ops.lease_rate` |
| graph prior | The row feeds an already mapped sink (`cf.uses`, `pnl.opex`, …) | The sink's category as a prior (~0.86). Not for CFADS / available-for-debt |

Division by a named constant or a number is **proration**; `value_kind` stays `money`. A true ratio is tokens such as `dscr` / `coverage` / `leverage` / `runway` / `cpi` (not a substring: `generation` ≠ ratio). See `_semantic_ratio`.

Compatibility with declared `calculations`: a match **raises** the score; a mismatch **lowers** it. Abstain `calculation_conflict` remains for SUM vs a declared DIFF (for example Total Inflows ≠ `cf.net`). Keep-rule exception: exact `Cash Flow` under IRR/ratios is not cancelled.

Relations (`alias`, `aggregate`, `difference`, `roll_forward`) are mapping's semantic links, not the formula graph. They are written to `mapping.json` and to `context.json` blocks. Cell-level edges are in IR; see [graph.md](graph.md).

## Resolver

`Resolver.fuse` gathers candidates with the same `concept_id`: it keeps the best score and a small bonus when several signals agree, then `prune_candidates`.

Prune drops:

- an id outside the taxonomy;
- an incompatible `unit` / `value_kind`;
- a concept `anti_labels` hit inside the row label;
- a confidently inferred row facet that contradicts the concept facet (`statement=cov` is set from the row **label**: `dscr` / `llcr` / `plcr`, not from the section heading — otherwise CFADS under DSCR is cut).

`decide`:

- an empty list → refuse;
- best score **&lt; 0.82** (`ACCEPT_MIN`) → refuse;
- two leaders closer than **0.02** → take the more specific id (`broader` is set), otherwise refuse (ambiguous);
- for the `embed` signal, also: score ≥ **0.85** (`COSINE_MIN`) and a gap to the second ≥ **0.08** (`COSINE_GAP`); embedding top-k is 5.

Thresholds are not lowered to “close coverage”. A nearby wrong tag is worse than `unknown`. Top-3 candidates are stored in `alternatives` / `candidates` even on abstain: if prune emptied the fused list, the raw proposals remain (`no_candidate` no longer means `candidates: []`).

## Exclusion

`exclusion_reason` (`exclusion.py`) runs before the resolver:

| Code | Condition |
| --- | --- |
| `flag` | `kind == flag` (0/1 timing, scenario, selector) |
| `check` | `article_role == check` |
| `helper` | `kind == helper` |
| `noise` | `is_noise_label` (if the row still reached context) |
| `technical_bridge` | the label contains `from mf` / `circular` / `helper`, except `pre-revolver` |

Excluded: `concept_id = null`, `source = rule`, no question, `disposition=excluded` on the same block row. They do **not** enter `unmapped.json`.

KPIs and calculated business rows (`article_role = calculation`) are ordinary facts: map them or abstain honestly. Do not exclude them.

## Disposition of the finished row

| disposition | `concept_id` | Review |
| --- | --- | --- |
| `mapped` | set | no |
| `excluded` | null | no |
| `abstained` | null | a question; in MD Concept = `unknown`; candidates and hints are kept |

An `abstract` block row has `disposition=header` (that is not a mapping refusal and not an abstain).

On abstain, `exclusion_reason` stores the resolver's refusal (this is not exclude):

| Code | When |
| --- | --- |
| `no_candidate` | The list is empty after prune |
| `low_score` | There is a candidate, but it is below the threshold |
| `ambiguous` | Two close leaders |
| `facet_mismatch` | Otherwise (candidates did not pass decide) |
| `calculation_conflict` | The observed SUM/diff contradicts declared `calculations` and the keep-rule did not fire |

## Hints and block rows

Even when `concept_id = null`, a context row has `hints`: `nature` (flow/balance), `time_semantics` (flow / bop / eop / rate / stock), `statement`, `unit` (`money` / `count` / `rate` / `years`), `currency` (`GBP` / `EUR` / `USD` / `RUB`; the same handlers: symbol, ISO, local abbreviation — `£`/`gbp`/`pound`/`фунт`, `€`/`eur`/`euro`/`евро`, `$`/`usd`/`dollar`/`долл`, `₽`/`rub`/`руб`/`РУБ`), `scale` (`unit` / `k` / `m` / `bn`), `sign` (`inflow` / `outflow` / `stock`), plus `segment` (`pc`/`hv`) and `escalation` (`revenue`/`cost`). `k£` in the label or the Units column → `unit=money`, `currency=GBP`, `scale=k` (not `null`). Unit-cell text is also role `unit`, even when it is a formula: `EUR'000` / `CUR'000` → currency EUR, scale `k`, factor 1000; `EUR/MWh` → `hints.unit=price`, `hints.unit_per=MWh`; `x` → `ratio`; `Date` → `date`. `%` and a percent format → `rate` and do not hand the row the money of a `EUR'000` label on the same row; a bare `per year` without `%` is also `rate`, while `months per year` is `count`. Mapping `value_kind` for prune is still `count` on durations; in context a duration is `years`. Schema `1.13.0`, hint fields are additive (`unit_per`). Next to them on the row are `period_position` and `aggregation` (from `time_semantics`: flow → `during_period`/`sum`, bop → `beginning`/`first`, eop and stock → `end`/`last`, rate → `during_period`/`average`). A scalar and a params-block row are `instant` / `none`, including when the concept has `facets.period_type=instant` (`val.irr`, `val.npv`, capacity, turbine count). `scale_factor` (1 / 1000 / 1000000 / 1000000000) and `normalized_values` in base units. Noise `|x| < 1e-6 · max|series|` becomes `0` in normalized values; the Excel cache does not change. `numeric_summary` prints in the same format. `values` stays the Excel cache. `value_statuses` distinguishes `cached`, an explicit `zero_explicit`, an empty `empty`, and `not_applicable` outside the phase. In `context.md` that is the Time column and the cell text, not a second document. Meaning, role, and cash semantics are separate fields; see below. Unknown is immediately useful downstream.

Fact rows in a `params` block are `article_role=assumption` (the live-scenario INDEX does not make them calculation). ALL-CAPS sections with no number are `abstract`, `disposition=header`, not a concept. A **Scenario Chosen** row is `flag` / `context_role=scenario_selector`: the cascade does not tag it, but the block row must keep the index (cell D).

A block row is a record for **every** layout row: `kind`, `disposition`, `indent`, `hidden`, `label_path`, `neighbors`, one `formula` / exceptions, `numeric_summary`, `cells` (roles `value` / `unit` / `scenario` / `total` / `stub` / `note`, plus `header` — the column label: Start, Live Case, Min). `values` is the cache or `null` along the block axis. A generic `Total` / `Balance b/f` / `Balance c/f` resolves together with the last section of `label_path`. `Debt` in “Sources of funds” → `cf.drawdown`; `Share premium` in “Uses of funds” → `cf.uses`. A `roll_forward` relation sets b/f = bop, the movement = flow, c/f = eop. An empty coverage cell (`dscr`, `coverage`, `cfads`, `dividend`) in construction is `not_applicable`, not a “missing cached values” warning. Cell-level adjacency and AST are in IR; see [graph.md](graph.md). Invariant: the sum of `blocks[].rows` equals the layout rows of **accepted** blocks. Dropped Cover / Shortcuts are not in the denominator. A violation is the warning `Content completeness N/M`.

Top-3 `candidates` are written on abstain too: if prune emptied the fused list, the raw proposals stay in context.

## Glossary

File `$DATA_DIR/glossary.json`, key `(normalized_label, normalized_parent)`. Before the cascade, `reconcile_glossary` rewrites entries whose label now belongs to another concept (otherwise a split duration would stay on the old id). After the job, `learn_from_rows` appends only rows with `confidence = high` and `source` in `{glossary, rule, structure, lexical}`. Chat and embed are **not** stored.

A key with the section class (`section_class`) is also stored, so the same label in a similar section of another book is picked up.

This is a cache of confident matches, not a place for one model's workarounds. A new value is a concept in yaml.

## Meaning and role

The selected `concept_id` is the **reporting concept** of this row (calculations and gold expect it). It does not exhaust the meaning. One label lives at different levels: P&L `Gross revenues` → `pnl.revenue`; CFS `Gross Revenues` → `cf.receipts`, and a role in CFADS is `cfads_input`, not the concept `cf.cfads`. `Equity` on the balance sheet → `bs.equity`; `Equity (k£)` in Sources → `cf.equity_issue` plus the role `cf.sources`.

A context row (`1.13.0`) and a `MappedRow` have three fields. Top-3 candidates stay raw signals and are **not** treated as interchangeable concepts.

| Field | What it is |
| --- | --- |
| `semantic_identity` | `family` + the economic `concept_id` + `confidence`. For a cash projection whose label names an accrual (`Gross Revenues` on CFS), identity is `pnl.revenue` and the selected `concept_id` is `cf.receipts`. A narrower child beats the parent (`ops.inflation_revenue`, not `ops.inflation`). An issue in Sources is `cf.equity_issue`, not the stock `bs.equity` |
| `reporting_roles` | Where **this** row is used. The selected concept with `selected: true`, plus `context_role` (`uses`, `sources`, `cfads_input`, `assumption`, …) and the layout concepts `cf.uses` / `cf.sources`. `cf.cfads` is not copied here |
| `cash_semantics` | `recognition`: `accrual` / `cash` / `noncash` / `rate` / `stock`. `cash_movement`: `inflow` / `outflow` / `none`. `Capitalized Interest` stays `pnl.interest` (gold), but recognition is `noncash`: capitalization is not a P&L expense |

`secondary_concepts` still carries `cf.uses` / `cf.sources` for any row of the section, not only capex. Participation in CFADS is only `context_role=cfads_input`.

The section rollup `cf.capex` is not attached to a fee / arrangement: that is not capex, even when the row sits in Uses.

## Confidence

| source | confidence |
| --- | --- |
| glossary, rule, structure, lexical | `high` |
| embed | `high` when score ≥ 0.85, otherwise `medium` |
| chat | `medium` |
| question (abstain) | `low` |

In `mapping.json` the decision lives in `source` (`glossary`, `rule`, `lexical`, `structure`, `embed`, `chat`, `question`). On a `context.json` row the same decision is `mapping.source`, and `mapping.method` collapses it: `glossary` / `rule` / `lexical` → `rule`, `chat` → `llm`, `question` → `unmapped`. `structure` and `embed` are unchanged.

## Quality

`finance_context.mapping.eval`:

- **content completeness** — block rows / layout rows; must be 1.0;
- **concept coverage** — share of annotatable rows (mapped + abstained, excluding excluded) with an accepted concept. This is coverage of the `concept_id` slot, not semantic completeness; with zero abstains the value is 1.0;
- **mapping quality** — checks of accepted rows, the object `mapping_stats.mapping_quality`:
  - `label_coverage` — a non-empty label matches the concept's `labels` / `aliases` / `exact_labels`, or evidence contains `label matches`;
  - `semantic_coverage` — `semantic_identity` and `cash_semantics` are present; on CFS, revenue/opex/tax/interest are the cash twins, and identity keeps the economic `pnl.*` when the label names it; `bs.*` is stock and time `stock|bop|eop`; capitalized interest is `noncash`; `cf.repayment` is an outflow and stock `bs.debt` is in the same block; an accrual and a payment of one family are not collapsed into one `concept_id`;
  - `unit_coverage` — `hints.unit` matches the concept unit (`*_rate` and `facets.unit=rate` → rate, `pnl.volume` → count, money pnl/cf/bs → money, durations → years);
  - `temporal_coverage` — opening → `bop`, closing → `eop`, balance/`bs.*` is not `flow`, a rate concept → `rate`;
  - `formula_coverage` — a row with a formula has a fingerprint (`formula`); the value series is the cache, and a separate A1 per cell is not copied into context (no such rows → 1.0). If the period columns have no template, the fingerprint is taken from the scalar left of the axis;
  - `confidence_threshold_passed` — no semantic-check failures, and every accepted row has `confidence=high` and `score >= 0.82`.
- **selective risk** — errors among **accepted** mappings (abstain is not in the risk);
- **abstain rate** and the **risk–coverage** curve — the quality of the right to refuse.

The `context.md` header prints completeness, concept coverage, and the six quality fields separately.

Gold expectations for `resources/cashflow.xlsx`: `tests/fixtures/mapping/cashflow_dispositions.yaml`. Public corpus (MIT / CC-BY-NC-SA, not in git): `uv run python scripts/fetch-corpus.py` → `resources/corpus/` (Packt, RVI; three-statement in the lock may 404). Gold: `packt_project_finance_dispositions.yaml`, `rvi_project_finance_dispositions.yaml`. Besides “must be”, gold knows **negatives** `forbidden_concept_id` (`Equity Injected` ≠ `bs.equity`, CFS `Variable land lease` ≠ `ops.lease_rate`, generation ≠ `ops.availability`). Corpus tests skip when the books are not downloaded or `expectations` is empty.

## How to extend mapping

1. Start with the [taxonomy](taxonomy.md): is this a new meaning, or the same id with another label.
2. If the meaning exists and the cascade is silent, tighten `labels` / `aliases` / `section_hints` / `skip_concept` / `unless`, not a wide `anti_labels` (`cash in` kills `Cash in hand`; a bare `lease` kills land lease). If a false tag comes from the section, put `unless` on the parent rollup. Do not lower the threshold.
3. If the formula is unambiguous (a copy from another sheet, a SUM of children), that is a structure task, not chat.
4. A new *kind* of match is a new `Signal` plus tests in `tests/mapping/`.
5. Do not put a row in exclude when it is a business attribute.
6. Run `uv run pytest` and the scenario in [review.md](review.md).
