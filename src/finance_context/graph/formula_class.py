"""One financial class for a formula link. The AST stays in the IR."""

from __future__ import annotations

import re

from finance_context.vocab import FormulaClass

_CONDITIONAL = re.compile(r"\b(?:IF|IFS|SWITCH)\(", re.IGNORECASE)
_AGGREGATION = re.compile(
    r"\b(?:SUM|SUMPRODUCT|AVERAGE|MIN|MAX|COUNT|COUNTA|COUNTIF|COUNTIFS|SUBTOTAL|AGGREGATE)\(",
    re.IGNORECASE,
)
_A1_REF = re.compile(
    r"(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!\$?[A-Z]{1,3}\$?\d+|\$?[A-Z]{1,3}\$?\d+",
    re.IGNORECASE,
)


# Bits that classify the same way as a lag list. ``classify_formula`` only cares
# whether a prior exists, whether every prior is -1, and whether a forward exists.
_LAG_NEG1 = 1
_LAG_OTHER = 2
_LAG_POS = 4


def note_lag(bits: int, lag: str) -> int:
    """Fold one lag into the bits ``classify_formula_bits`` understands."""
    if lag in {"", "same", "unaligned"}:
        return bits
    try:
        number = int(lag)
    except (TypeError, ValueError):
        return bits
    if number == -1:
        return bits | _LAG_NEG1
    if number < 0:
        return bits | _LAG_OTHER
    if number > 0:
        return bits | _LAG_POS
    return bits


def classify_formula_bits(
    formula: str | None,
    refs: list[str] | None,
    bits: int,
) -> FormulaClass | None:
    """Same class as ``classify_formula`` for the lags those bits summarize."""
    lags: list[str] = []
    if bits & _LAG_NEG1:
        lags.append("-1")
    if bits & _LAG_OTHER:
        lags.append("-2")
    if bits & _LAG_POS:
        lags.append("1")
    return classify_formula(formula, refs, lags)


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
