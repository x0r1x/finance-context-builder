# Reviewing unmapped rows

[Русский](../ru/review.md) · **English**

First check that layout produced fact rows at all. `succeeded` + `Unmapped: 0` + an empty `context.md` almost always means there are no blocks or no article labels, not “the taxonomy covered everything”. Checklist: [layout.md](layout.md).

After a run with a non-zero fact count, look at the refusal **and** the full content. An empty `unmapped.json` does **not** mean every block row has a `concept_id`: headers (`kind=abstract`, `disposition=header`) and excluded rows are not in the extract. `mapping_stats.concept_coverage` is the share of annotatable facts with an accepted `concept_id`. Semantics, units, time, and formulas are in `mapping_stats.mapping_quality`, not in the length of `unmapped.json` and not in `concept_coverage`.

| Where | What you see |
| --- | --- |
| `context.md` header | `Content completeness` (must be 1.00), `Concept coverage` (share of accepted slots, may be < 1), and the six `mapping_quality` fields |
| `context.md` block | Every row: `Row` (`row_key`), `Path` (`label_path`), Kind, Disposition, Concept (`unknown` on abstain), `Cells` (role cells: `L total: -86400`, `G Start: 01.01.2024`), the formula, and every axis value. A period header is `Y23 (AA)` (key and column letter). Under the table, `relations` use the same `row_key`. An empty formula on a row that `graph.md` links to is a defect. Params: `## Parameters / {sheet}`, scenarios as columns `Live Case (L)` / `Case 1 (N)` |
| `context.json` → `blocks[].rows` | Every kind, including abstract; `disposition`, role-tagged `cells`, the `values` series; the completeness invariant |
| `context.json` → `mapping_stats` | `inventory_rows`, `mapped`, `abstained`, `excluded`, `abstract`, `unmapped_series`, completeness, `concept_coverage`, `mapping_quality` |
| `context.json` → `graph` | Pointer to `graph.json` / parquet, counts, `empty_range_members` |
| `graph.json` / `graph.md` + `GET .../graph/trace` | Summary and formula-level `links` (`cell`, A1, `row_key`, `period_id`; a range is one ref). The link `row_key` is a row in `context.md`. Cell-level edges and AST stay in `ir/*.parquet` |
| `unmapped.json` | Abstained rows **without** `values` |

`scripts/extract-unmapped.py` (called by `scripts/run.sh`) reads `blocks[].rows` from context (or `rows` from mapping), keeps `disposition=abstained`, and drops value series (`values`, `value_statuses`, `normalized_values` on the row and on each series). `cells[].header` stays. The count must match the number of `unknown` cells in the Markdown block tables. `scripts/check-graph.py` also requires unique period keys, forbids a `total` / `stub` / `scenario` column inside an axis, checks `start_date ≤ end_date` and date continuity, and checks that `Start` / `End` rows and the role-cell cache are in `context.md`. A total's link has `period_id=null`.

Excluded rows (check / helper / flag / technical) are not in `unmapped.json`. They are the same block rows with `disposition=excluded`.

## Fix loop

1. Run the book (`uv run finance-context build …` or `bash scripts/run.sh path/to/model.xlsx` with `serve` already up). `run.sh` writes documents to `out/<run>/json/` and `out/<run>/md/` (`context`, `graph`, `trace`) and `unmapped.json` at the run root.
2. Open `unmapped.json` and the same row in `blocks[].rows` / the block table: `label`, `parent_label`, `label_path`, `neighbors`, `candidates`, `hints`, `sheet`, `disposition`, `exclusion_reason`, `article_role`, `cells`, `unit`, `formula`. Effect on CFS: `GET .../graph/trace` ([graph.md](graph.md)).
3. Decide the class of each row:

| Class | Action |
| --- | --- |
| New financial value | A concept in [taxonomy.yaml](taxonomy.md) + gold |
| Same meaning, different label / section | `labels` / `aliases` / `section_hints` / `skip_concept` / `unless`; not a wide `anti_labels` |
| Child under CAPEX/OPEX/Revenue, but it is years / MW / an index | `unless` on the parent rollup + `facets.unit`, not the parent's money id |
| Neighbors and the graph already hint (lease next to opex) | That is a structure feature; do not glue on a rate alias |
| Unambiguous formula (alias, SUM) | Check structure: SUM copies a concept only when every child is mapped |
| Empty run, zeros in metrics, completeness < 1 | Layout / build, not yaml. Cover and Shortcuts are not in the denominator; Input Assumptions should be params |
| Technical bridge, check, noise | Exclusion; do not invent a concept |
| Real ambiguity | Leave `unknown` (`no_candidate` / `ambiguous` / `low_score`); candidates are already in the JSON |

4. Update gold: `tests/fixtures/mapping/cashflow_dispositions.yaml` for the reference `cashflow.xlsx`; for the corpus, `packt_project_finance_dispositions.yaml` / `rvi_project_finance_dispositions.yaml` (including `forbidden_concept_id`).
5. `uv run pytest` and, if needed, another run. `unmapped.json` should shrink only because of honest mapped rows, not because of exclude. Completeness stays 1.0.

## Reading `exclusion_reason` on an abstained row

| Code | Typical fix |
| --- | --- |
| `no_candidate` | No id in yaml, or lexical/hints do not admit the phrase |
| `low_score` | The phrase is too generic; strengthen labels or structure, not the threshold |
| `ambiguous` | Two concepts sit next to each other — split them with hints/anti or a tighter broader |
| `facet_mismatch` | `unit` / anti_labels / facets cut the only candidate (including a false `statement=cov` on a non-DSCR row) |
| `calculation_conflict` | The formula total did not match `calculations` in yaml |

## Example

On a run such as `out/<timestamp>/`, leftover unknown with a live taxonomy is normal when it is not a false high tag:

- construction shares 0.2/0.8 (phasing) are not a flag (only 0/1) and not a financial fact;
- rows with no stable id (do not glue them to the nearest money concept).

Either add a new concept (if the meaning repeats across books) or leave `unknown`. A false `pnl.opex` on Operating lifetime is worse than unknown.

Markdown repeats JSON: every axis column and every block row. Tables are not truncated.

## Commands

```bash
uv run python scripts/extract-unmapped.py out/<run>/json/context.json -o out/<run>/unmapped.json
uv run python scripts/extract-unmapped.py data/jobs/<job-id>/mapping.json
uv run pytest tests/test_extract_unmapped.py tests/eval/test_cashflow_dispositions.py tests/eval/test_corpus_dispositions.py
```
