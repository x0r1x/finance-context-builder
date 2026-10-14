from __future__ import annotations

from finance_context.layout.relations import _relation_from_pattern
from finance_context.mapping.book import BookView
from finance_context.mapping.models import RowContext
from finance_context.mapping.normalize import normalize_label

__all__ = ["_relation_from_pattern"]


def _shared_concept(
    child_ids: list[str],
    book: BookView,
    *,
    mapped: int,
    members: int,
) -> str | None:
    if members <= 0 or mapped < members:
        return None
    unique = list(dict.fromkeys(child_ids))
    if len(unique) == 1:
        return unique[0]
    calc_parent = _aggregate_parent(unique, book)
    if calc_parent:
        return calc_parent
    broaders: list[str] = []
    prefixes: list[str] = []
    for cid in unique:
        concept = book.taxonomy.get(cid)
        if concept and concept.broader:
            broaders.append(concept.broader)
        parts = cid.split(".")
        if len(parts) >= 2:
            prefixes.append(".".join(parts[:2]))
    if broaders and len(set(broaders)) == 1:
        shared = broaders[0]
        if shared in book.taxonomy:
            return shared
    if prefixes and len(set(prefixes)) == 1:
        shared = prefixes[0]
        if shared in book.taxonomy:
            return shared
    return None


def _equity_cashflow_sum(ctx: RowContext, child_ids: list[str], book: BookView) -> str | None:
    if "cf.equity_cashflow" not in book.taxonomy:
        return None
    blob = normalize_label(
        " ".join([ctx.label, ctx.parent_label or "", *ctx.section_path, ctx.sheet])
    )
    if "irr" not in blob and "equity" not in blob:
        return None
    kinds = set(child_ids)
    if "cf.equity_issue" in kinds and kinds & {"cf.dividends", "cf.disbursements", "bs.cash"}:
        return "cf.equity_cashflow"
    return None


def _diff_parent(left_id: str | None, right_id: str | None, book: BookView) -> str | None:
    if not left_id or not right_id or left_id == right_id:
        return None
    observed = {left_id, right_id}
    for calc in book.calculations:
        if len(calc.terms) != 2:
            continue
        weights = {term.concept: term.weight for term in calc.terms}
        if set(weights) != observed:
            continue
        if weights[left_id] * weights[right_id] < 0 and calc.parent in book.taxonomy:
            return calc.parent
    return None


def _aggregate_parent(child_ids: list[str], book: BookView) -> str | None:
    observed = set(child_ids)
    for calc in book.calculations:
        if any(term.weight < 0 for term in calc.terms):
            continue
        terms = {term.concept for term in calc.terms}
        if not terms:
            continue
        if observed == terms and calc.parent in book.taxonomy:
            return calc.parent
    return None
