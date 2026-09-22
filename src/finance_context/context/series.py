"""Series semantics on a layout row.

`values` stays a flat list of cached strings. Status, scale, and time
profile sit beside that list. They are not a second cell catalog.
"""

from __future__ import annotations

import re

from finance_context.vocab import PeriodPosition, SeriesAggregation, ValueStatus

SCALE_FACTORS: dict[str, int] = {
    "unit": 1,
    "k": 1_000,
    "m": 1_000_000,
    "bn": 1_000_000_000,
}

_TEMPORAL: dict[str, tuple[PeriodPosition, SeriesAggregation]] = {
    "flow": ("during_period", "sum"),
    "bop": ("beginning", "first"),
    "eop": ("end", "last"),
    "stock": ("end", "last"),
    "rate": ("during_period", "average"),
}

_OPERATION_GATE = frozenset({"traffic", "toll", "revenue", "opex"})
_CONSTRUCTION_GATE = frozenset({"capex"})


def scale_factor_for(scale: str | None) -> int | None:
    if scale is None:
        return None
    return SCALE_FACTORS.get(scale)


def temporal_profile(
    time_semantics: str | None,
) -> tuple[PeriodPosition | None, SeriesAggregation | None]:
    if not time_semantics:
        return None, None
    return _TEMPORAL.get(time_semantics, (None, None))


def phase_gate(label: str, concept_id: str | None) -> str | None:
    """Phase where an empty cell is not a missing number.

    A cached value, including an explicit zero, always wins over the gate.
    """

    blob = f"{label or ''} {concept_id or ''}"
    tokens = set(re.sub(r"[^a-z0-9]+", " ", blob.casefold()).split())
    if tokens & _CONSTRUCTION_GATE:
        return "construction"
    if tokens & _OPERATION_GATE:
        return "operation"
    return None


def value_status(text: str | None, *, applicable: bool) -> ValueStatus:
    if not applicable:
        return "not_applicable"
    if text in (None, ""):
        return "empty"
    if _is_zero(text):
        return "zero_explicit"
    return "cached"


def normalize_value(text: str | None, factor: int | None) -> str | None:
    if text in (None, "") or factor is None:
        return None
    number = _parse_number(text)
    if number is None:
        return None
    return _format_number(number * factor)


def _is_zero(text: str) -> bool:
    number = _parse_number(text)
    return number is not None and number == 0.0


def _parse_number(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "").replace(",", "")
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_number(number: float) -> str:
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.10f}".rstrip("0").rstrip(".")
