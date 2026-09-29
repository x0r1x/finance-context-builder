from __future__ import annotations

import re

from finance_context.mapping.normalize import normalize_label

_SKIP = {"months per year", "thousand", "on", "off", "total"}
_NEARBY = 0.5


def should_mint(best_score: float | None) -> bool:
    """A weak neighbour is not a new concept. Nothing nearby can be."""
    return best_score is not None and best_score < _NEARBY


def statement_prefix(parent: str, sheet: str) -> str:
    text = normalize_label(f"{sheet} {parent}")
    if "cash flow" in text or re.search(r"\bcfs\b", text):
        return "cf"
    if "balance" in text:
        return "bs"
    if "p and l" in text or "profit" in text or re.search(r"\bpnl\b", text):
        return "pnl"
    return "ops"


def concept_id_for_label(label: str, parent: str, sheet: str) -> str | None:
    norm = normalize_label(label)
    if not norm or norm in _SKIP:
        return None
    slug = norm.replace(" ", "-")
    return f"{statement_prefix(parent, sheet)}.{slug}"
