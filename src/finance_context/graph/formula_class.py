"""One financial class for a formula link. The AST stays in the IR."""

from __future__ import annotations

import re

from finance_context.vocab import FormulaClass

_CONDITIONAL = re.compile(r"\b(?:IF|IFS|SWITCH)\(", re.IGNORECASE)
_AGGREGATION = re.compile(
    r"\b(?:SUM|SUMPRODUCT|AVERAGE|MIN|MAX|COUNT|COUNTA|COUNTIF|COUNTIFS|SUBTOTAL|AGGREGATE)\(",
    re.IGNORECASE,
)
_COL_OFFSET = re.compile(r"C\[(-?\d+)\]", re.IGNORECASE)
_ROW_OFFSET = re.compile(r"R\[(-?\d+)\]", re.IGNORECASE)
_A1_REF = re.compile(
    r"(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!\$?[A-Z]{1,3}\$?\d+|\$?[A-Z]{1,3}\$?\d+",
    re.IGNORECASE,
)


def classify_formula(
    formula: str | None,
    refs: list[str] | None = None,
    lags: list[str] | None = None,
) -> FormulaClass | None:
    if formula is None or not str(formula).strip():
        return None
    text = str(formula).strip()
    if _CONDITIONAL.search(text):
        return "conditional"
    if _AGGREGATION.search(text):
        return "aggregation"
    offsets = _lag_numbers(lags or [])
    if not offsets:
        offsets = [int(item) for item in _COL_OFFSET.findall(text)]
        offsets.extend(int(item) for item in _ROW_OFFSET.findall(text))
    prior = [item for item in offsets if item < 0]
    forward = [item for item in offsets if item > 0]
    if prior or forward:
        simple_prior = bool(prior) and not forward and all(item == -1 for item in prior)
        arithmetic = "*" in text or "/" in text
        return "rollforward" if simple_prior and not arithmetic else "cross_period"
    if refs or _A1_REF.search(text):
        return "same_period"
    return "hardcoded"


def _lag_numbers(lags: list[str]) -> list[int]:
    numbers: list[int] = []
    for lag in lags:
        if lag in {"", "same", "unaligned"}:
            continue
        try:
            numbers.append(int(str(lag)))
        except ValueError:
            continue
    return numbers
