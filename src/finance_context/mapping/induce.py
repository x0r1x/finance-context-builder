from __future__ import annotations

import re

from finance_context.mapping.normalize import normalize_label

_SKIP = {"months per year", "thousand", "on", "off", "total"}


def should_mint(best_score: float | None, mint_score_max: float) -> bool:
    """Mint only when a neighbour exists and scores below the passed ceiling."""
    return best_score is not None and best_score < mint_score_max


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
