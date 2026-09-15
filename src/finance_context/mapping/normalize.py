from __future__ import annotations

import re

_PARENS = re.compile(r"\([^)]*\)|（[^）]*）")
_UNCLOSED = re.compile(r"\([^)]*$|（[^）]*$")
_QUALIFIERS = {
    "fixed",
    "variable",
    "accrual",
    "lumpy",
    "cash",
    "proxy",
    "constant",
}


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    text = str(label)
    text = text.replace("&", " and ").replace("−", " ").replace("–", " ").replace("+", " ")
    extras: list[str] = []
    for match in _PARENS.finditer(text):
        extras.extend(_qualifier_tokens(match.group(0)))
    text = _PARENS.sub(" ", text)
    unclosed = _UNCLOSED.search(text)
    if unclosed:
        extras.extend(_qualifier_tokens(unclosed.group(0)))
        text = _UNCLOSED.sub(" ", text)
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    if extras:
        normalized = re.sub(r"\s+", " ", f"{normalized} {' '.join(extras)}").strip()
    return normalized


def _qualifier_tokens(blob: str) -> list[str]:
    tokens = re.findall(r"[a-zа-яё0-9%]+", blob.casefold(), flags=re.IGNORECASE)
    return [token for token in tokens if token in _QUALIFIERS]


def section_class(
    parent: str | None = None,
    section_path: list[str] | None = None,
    sheet: str | None = None,
) -> str:
    blob = normalize_label(" ".join([parent or "", *(section_path or []), sheet or ""]))
    if any(token in blob for token in ("cash inflow", "receipt", "collection", "revenue earned")):
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
