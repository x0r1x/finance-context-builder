from __future__ import annotations

from finance_context.layout.models import Block, LayoutRow
from finance_context.mapping.book import BookView, RowPattern, _block_id
from finance_context.mapping.models import RowContext, RowRelation
from finance_context.mapping.normalize import normalize_label


def _relation_from_pattern(
    book: BookView,
    sheet: str,
    block: Block,
    layout_row: LayoutRow,
    pattern: RowPattern,
) -> RowRelation | None:
    source = book.row_key(sheet, layout_row.row, block.block_id)
    if pattern.kind == "alias" and pattern.alias_row is not None:
        target_block = _block_id(book, pattern.alias_sheet or sheet, pattern.alias_row)
        if target_block is None:
            return None
        return RowRelation(
            kind="alias",
            source_row_key=source,
            target_row_key=book.row_key(
                pattern.alias_sheet or sheet, pattern.alias_row, target_block
            ),
            evidence=pattern.template,
        )
    if pattern.kind == "aggregate" and pattern.aggregate_rows:
        members = []
        for row_n in pattern.aggregate_rows:
            block_id = _block_id(book, sheet, row_n)
            if block_id:
                members.append(book.row_key(sheet, row_n, block_id))
        return RowRelation(
            kind="aggregate",
            source_row_key=source,
            member_row_keys=members,
            evidence=pattern.template,
        )
    if pattern.kind == "diff" and pattern.diff_rows:
        members = []
        for row_n in pattern.diff_rows:
            block_id = _block_id(book, sheet, row_n)
            if block_id:
                members.append(book.row_key(sheet, row_n, block_id))
        return RowRelation(
            kind="difference",
            source_row_key=source,
            member_row_keys=members,
            evidence=pattern.template,
        )
    if pattern.kind == "roll" and pattern.roll_from_row is not None:
        block_id = _block_id(book, sheet, pattern.roll_from_row)
        if block_id is None:
            return None
        return RowRelation(
            kind="roll_forward",
            source_row_key=source,
            target_row_key=book.row_key(sheet, pattern.roll_from_row, block_id),
            evidence=pattern.template,
        )
    return None


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
