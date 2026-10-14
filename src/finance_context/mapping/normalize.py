from __future__ import annotations

import re

from finance_context.layout.normalize import normalize_label

_YEARS_UNIT = re.compile(r"\byears?\b", re.IGNORECASE)
_PER_YEAR_UNIT = re.compile(r"per year|/year", re.IGNORECASE)


def memory_section(section_path: list[str] | None, parent_label: str | None) -> str:
    """Narrowest heading. An empty path falls back to the parent caption."""
    if section_path:
        return normalize_label(section_path[-1])
    return normalize_label(parent_label)


def memory_unit(unit_text: str | None, value_kind: str | None = None) -> str:
    """Unit token for a memory key: money, rate, years, or empty.

    A copied currency caption does not outrank a percent row. A missing caption
    stays empty, so a default money kind is not stored as money.
    """
    text = str(unit_text or "")
    blob = text.casefold()
    if (
        value_kind == "rate"
        or "%" in text
        or "percent" in blob
        or blob in {"per year", "of margin"}
        or _PER_YEAR_UNIT.search(blob)
    ):
        return "rate"
    if _YEARS_UNIT.search(text) and not _PER_YEAR_UNIT.search(blob):
        return "years"
    if text.strip():
        from finance_context.context.measure import currency_code_from_text

        if currency_code_from_text(text):
            return "money"
    return ""


def section_class(
    parent: str | None = None,
    section_path: list[str] | None = None,
    sheet: str | None = None,
) -> str:
    blob = normalize_label(" ".join([parent or "", *(section_path or []), sheet or ""]))
    if any(token in blob for token in ("revenue earned", "accrual")):
        return "accrual revenue"
    if any(token in blob for token in ("cash inflow", "receipt", "collection")):
        return "cash inflows"
    if any(
        token in blob
        for token in ("cash outflow", "disburs", "operating expense", "occupancy", "payroll")
    ):
        return "cash outflows"
    if any(token in blob for token in ("term loan", "revolver", "debt service", "amortisation")):
        return "debt service"
    if "tax" in blob:
        return "tax"
    if any(token in blob for token in ("covenant", "liquidity", "key ratio", "headline", "runway")):
        return "liquidity"
    if any(token in blob for token in ("working capital", "inventory", "accounts payable")):
        return "working capital"
    return blob
