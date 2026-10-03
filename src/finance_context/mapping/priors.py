from __future__ import annotations

from finance_context.mapping.book import BookView, _concept_at
from finance_context.mapping.models import Candidate, RowContext
from finance_context.mapping.normalize import normalize_label

_GRAPH_SINKS = {
    "cf.uses",
    "cf.sources",
    "cf.capex",
    "pnl.opex",
    "pnl.revenue",
}


_GRAPH_SKIP_LABELS = ("cfads", "fcfe", "fcf", "ebitda", "dscr", "irr")


def _graph_priors(ctx: RowContext, book: BookView) -> list[Candidate]:
    label = normalize_label(ctx.label)
    if any(token in label for token in (*_GRAPH_SKIP_LABELS, "duration", "toll")):
        return []
    out: list[Candidate] = []
    for dep in book.dependents.get((ctx.sheet, ctx.row), []):
        sheet, _, row_text = dep.partition("!")
        try:
            row_n = int(row_text)
        except ValueError:
            continue
        concept_id = _concept_at(book, sheet, row_n)
        if concept_id in _GRAPH_SINKS:
            out.append(
                Candidate(
                    concept_id=concept_id,
                    score=0.86,
                    signal="structure",
                    evidence=f"feeds mapped {concept_id} at {dep}",
                )
            )
    return out


def _neighbor_priors(ctx: RowContext, book: BookView) -> list[Candidate]:
    blob = normalize_label(" ".join([*ctx.prev_labels, *ctx.next_labels, *ctx.section_path]))
    label = normalize_label(ctx.label)
    out: list[Candidate] = []
    if "lease" in label and ctx.value_kind == "money" and "pnl.opex" in book.taxonomy:
        if any(token in blob for token in ("opex", "operating", "cost", "cashflow", "lease")):
            out.append(
                Candidate(
                    concept_id="pnl.opex",
                    score=0.9,
                    signal="structure",
                    evidence="lease cash line next to opex neighbors",
                )
            )
    if label in {"equity", "equity k"} or label.startswith("equity "):
        if any(token in blob for token in ("source", "construction", "irr", "injected")):
            if "cf.equity_issue" in book.taxonomy and "balance" not in blob:
                out.append(
                    Candidate(
                        concept_id="cf.equity_issue",
                        score=0.93,
                        signal="structure",
                        evidence="equity funding line in sources/construction",
                    )
                )
    return out
