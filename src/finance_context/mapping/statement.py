from __future__ import annotations

import re

from finance_context.mapping.models import RowContext
from finance_context.mapping.normalize import normalize_label

_PNL_TO_CF: dict[str, str] = {
    "pnl.revenue": "cf.receipts",
    "pnl.opex": "cf.opex_paid",
    "pnl.tax": "cf.tax_paid",
    "pnl.interest": "cf.interest_paid",
}

_CF_CROSSWALK_SCORE = 0.94
_ALIAS_SCORE = 0.96


def context_tokens(
    *,
    sheet: str = "",
    section_path: list[str] | None = None,
    parent: str | None = None,
    label: str = "",
) -> set[str]:
    parts = [label, parent or "", *(section_path or []), sheet]
    blob = normalize_label(" ".join(parts))
    tokens = set(blob.split())
    for part in parts:
        if part:
            tokens |= set(re.findall(r"[a-z0-9]+", str(part).casefold()))
    return tokens


def is_cashflow_context(
    *,
    sheet: str = "",
    section_path: list[str] | None = None,
    parent: str | None = None,
    label: str = "",
) -> bool:
    tokens = context_tokens(
        sheet=sheet, section_path=section_path, parent=parent, label=""
    )
    if "cfs" in tokens:
        return True
    if {"cash", "flow"} <= tokens and "statement" in tokens:
        return True
    return False


def is_balance_sheet_context(
    *,
    sheet: str = "",
    section_path: list[str] | None = None,
    parent: str | None = None,
) -> bool:
    tokens = context_tokens(sheet=sheet, section_path=section_path, parent=parent)
    return "balance" in tokens and "sheet" in tokens


def is_pnl_context(
    *,
    sheet: str = "",
    section_path: list[str] | None = None,
    parent: str | None = None,
) -> bool:
    raw = " ".join([parent or "", *(section_path or []), sheet]).casefold()
    tokens = context_tokens(sheet=sheet, section_path=section_path, parent=parent)
    if "p&l" in raw or "pnl" in tokens:
        return True
    return "profit" in tokens and "loss" in tokens


def is_sources_or_uses(ctx: RowContext) -> bool:
    tokens = context_tokens(
        sheet=ctx.sheet,
        section_path=ctx.section_path,
        parent=ctx.parent_label,
        label=ctx.label,
    )
    return bool(tokens & {"uses", "sources", "source"})


def layout_statement(
    *,
    sheet: str = "",
    section_path: list[str] | None = None,
    parent: str | None = None,
    label: str = "",
) -> str | None:
    if is_balance_sheet_context(sheet=sheet, section_path=section_path, parent=parent):
        return "bs"
    if is_cashflow_context(
        sheet=sheet, section_path=section_path, parent=parent, label=label
    ):
        return "cf"
    if is_pnl_context(sheet=sheet, section_path=section_path, parent=parent):
        return "pnl"
    return None


def remap_alias_concept(concept_id: str, ctx: RowContext) -> tuple[str, str | None, float]:
    """Return (concept_id, extra_evidence, score) for a structure alias."""
    if concept_id.startswith("bs.") and is_sources_or_uses(ctx):
        return "", "skip bs alias on sources/uses", 0.0
    if is_cashflow_context(
        sheet=ctx.sheet,
        section_path=ctx.section_path,
        parent=ctx.parent_label,
        label=ctx.label,
    ):
        mapped = _PNL_TO_CF.get(concept_id)
        if mapped:
            return mapped, f"alias+crosswalk {concept_id}->{mapped}", _CF_CROSSWALK_SCORE
    return concept_id, None, _ALIAS_SCORE


def statement_for_row(
    *,
    concept_id: str | None,
    sheet: str = "",
    section_path: list[str] | None = None,
    parent: str | None = None,
    label: str = "",
) -> str | None:
    prefix = concept_id.split(".", 1)[0] if concept_id else None
    layout = layout_statement(
        sheet=sheet, section_path=section_path, parent=parent, label=label
    )
    if prefix == "bs":
        return "bs"
    if layout == "cf" and prefix == "pnl":
        return "cf"
    if prefix:
        return prefix
    return layout
