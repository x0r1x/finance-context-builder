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
    "cost",
    "costs",
}
_METRIC_ACRONYMS = {
    "capex",
    "cfads",
    "coc",
    "dscr",
    "dsra",
    "ebit",
    "ebitda",
    "fcf",
    "fcfe",
    "fcff",
    "irr",
    "llcr",
    "npv",
    "nwc",
    "opex",
    "plcr",
    "wacc",
}
_ACRONYM = re.compile(r"\b[A-Za-z]{2,10}\b")
_CASHFLOW = re.compile(r"\bcashflows?\b")


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    text = str(label)
    text = text.replace("&", " and ").replace("−", " ").replace("–", " ").replace("+", " ")
    text = text.replace("/", " ")
    extras: list[str] = []
    for match in _PARENS.finditer(text):
        extras.extend(_paren_tokens(match.group(0)))
    text = _PARENS.sub(" ", text)
    unclosed = _UNCLOSED.search(text)
    if unclosed:
        extras.extend(_paren_tokens(unclosed.group(0)))
        text = _UNCLOSED.sub(" ", text)
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    normalized = _CASHFLOW.sub("cash flow", normalized)
    if extras:
        normalized = re.sub(r"\s+", " ", f"{normalized} {' '.join(extras)}").strip()
    return normalized


def _qualifier_tokens(blob: str) -> list[str]:
    return _paren_tokens(blob)


def _paren_tokens(blob: str) -> list[str]:
    kept: list[str] = []
    for raw in _ACRONYM.findall(blob):
        token = raw.casefold()
        if token in _QUALIFIERS or token in _METRIC_ACRONYMS or raw.isupper():
            kept.append(token)
    return kept


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
