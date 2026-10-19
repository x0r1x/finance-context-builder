from __future__ import annotations

import re
from functools import lru_cache

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
_SPACE = re.compile(r"\s+")


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    return _normalize_label(str(label))


@lru_cache(maxsize=8192)
def _normalize_label(text: str) -> str:
    text = text.replace("Ё", "Е").replace("ё", "е")
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
    normalized = _SPACE.sub(" ", text).strip().casefold()
    normalized = _CASHFLOW.sub("cash flow", normalized)
    if extras:
        normalized = _SPACE.sub(" ", f"{normalized} {' '.join(extras)}").strip()
    return normalized


def _paren_tokens(blob: str) -> list[str]:
    kept: list[str] = []
    for raw in _ACRONYM.findall(blob):
        token = raw.casefold()
        if token in _QUALIFIERS or token in _METRIC_ACRONYMS or raw.isupper():
            kept.append(token)
    return kept
