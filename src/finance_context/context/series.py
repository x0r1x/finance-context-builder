"""Series values on a layout row.

`values` stays a flat list of cached strings. Status and scale sit beside
that list. They are not a second cell catalog.
"""

from __future__ import annotations

import math

from finance_context.layout.cells import parse_number
from finance_context.vocab import ValueStatus

SCALE_FACTORS: dict[str, int] = {
    "unit": 1,
    "k": 1_000,
    "m": 1_000_000,
    "bn": 1_000_000_000,
}

_NOISE_RATIO = 1e-6


def scale_factor_for(scale: str | None) -> int | None:
    if scale is None:
        return None
    return SCALE_FACTORS.get(scale)


def value_status(text: str | None, *, applicable: bool) -> ValueStatus:
    if not applicable:
        return "not_applicable"
    if text in (None, ""):
        return "empty"
    if _is_zero(text):
        return "zero_explicit"
    return "cached"


def normalize_value(text: str | None, factor: int | None) -> str | None:
    """Canonical decimal. Scale multiplies only when the factor is not 1.

    `factor is None` still parses scientific notation and float residue.
    A date or other non-number stays `None`. The cached `values[]` string
    is left unchanged by the caller. A finite cache times `k` / `m` / `bn`
    that leaves the float range is `None` too: an empty string would later
    be read as magnitude 0.
    """
    if text in (None, ""):
        return None
    number = _parse_number(text)
    if number is None:
        return None
    multiplier = 1 if factor in (None, 1) else factor
    scaled = number * multiplier
    if not math.isfinite(scaled):
        return None
    return format_number(scaled)


def normalize_series(values: list[str | None], factor: int | None) -> list[str | None]:
    """Base-unit amounts. A float residue far below the series scale is 0.

    `1.9e-11` next to `60000` is rounding left by the workbook, not a balance.
    """
    out = [normalize_value(value, factor) for value in values]
    numbers = [abs(_parse_number(item) or 0.0) for item in out if item is not None]
    scale = max(numbers, default=0.0)
    if scale == 0.0:
        return out
    return [
        "0"
        if item is not None and abs(_parse_number(item) or 0.0) < _NOISE_RATIO * scale
        else item
        for item in out
    ]


def _is_zero(text: str) -> bool:
    number = _parse_number(text)
    return number is not None and number == 0.0


def _parse_number(text: str) -> float | None:
    """Same number as layout: a comma is the decimal mark, grouping spaces drop.

    A trailing percent sign is the unit, not part of the number.
    """
    cleaned = text.strip()
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()
    if not cleaned:
        return None
    return parse_number(cleaned)


def format_number(number: float) -> str:
    if not math.isfinite(number):
        return ""
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.10f}".rstrip("0").rstrip(".")
