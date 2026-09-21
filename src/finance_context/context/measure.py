from __future__ import annotations

import re
from dataclasses import dataclass
_CURRENCY_SYM = {"GBP": "£", "EUR": "€", "USD": "$", "RUB": "₽"}
_INFLOW_IDS = (
    "pnl.revenue",
    "pnl.other_income",
    "cf.receipts",
    "cf.receipts.other",
)
_OUTFLOW_FRAGMENTS = (
    "opex",
    "capex",
    "uses",
    "tax",
    "interest",
    "fee",
    "debt.service",
    "maintenance",
)
_INFLOW_TOKENS = {"revenue", "receipts", "receipt", "inflow", "inflows", "income"}
_OUTFLOW_TOKENS = {
    "opex",
    "capex",
    "cost",
    "costs",
    "uses",
    "tax",
    "outflow",
    "outflows",
    "expense",
    "expenses",
    "fee",
    "maintenance",
}
_YEARS_TEXT = re.compile(r"\byears?\b", re.I)
_COUNT_TEXT = re.compile(r"\b(months?|days?|veh|mw|mwh|count)\b", re.I)
_PER_YEAR = re.compile(r"per year|/year", re.I)


@dataclass(frozen=True)
class Measure:
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None
    sign: str | None = None

    def display(self) -> str:
        if self.unit == "rate":
            return "%"
        if self.unit == "years":
            return "years"
        if self.unit == "count":
            return "count"
        if self.unit == "money":
            symbol = _CURRENCY_SYM.get(self.currency or "", "")
            prefix = "" if self.scale in (None, "unit") else self.scale
            composed = f"{prefix}{symbol}"
            return composed or "money"
        return ""


def parse_measure(
    unit_text: str | None = None,
    label: str | None = None,
    number_formats: list[str] | None = None,
    *,
    concept_id: str | None = None,
    statement: str | None = None,
    nature: str | None = None,
    time_semantics: str | None = None,
) -> Measure:
    measure = Measure()
    measure = _fill(measure, _parse_text(unit_text or ""))
    measure = _fill(measure, _parse_text(label or ""))
    for fmt in number_formats or []:
        measure = _fill(measure, _parse_format(fmt))
    if measure.unit == "money" and measure.scale is None:
        measure = Measure(
            unit=measure.unit,
            currency=measure.currency,
            scale="unit",
            sign=measure.sign,
        )
    sign = _infer_sign(
        concept_id=concept_id,
        statement=statement,
        nature=nature,
        time_semantics=time_semantics,
        label=label or "",
    )
    return Measure(
        unit=measure.unit,
        currency=measure.currency,
        scale=measure.scale,
        sign=sign,
    )


def _fill(base: Measure, extra: Measure) -> Measure:
    return Measure(
        unit=base.unit or extra.unit,
        currency=base.currency or extra.currency,
        scale=base.scale or extra.scale,
        sign=base.sign or extra.sign,
    )


def _parse_text(text: str) -> Measure:
    if not text.strip():
        return Measure()
    currency = _currency_from(text)
    scale = _scale_from(text)
    unit = _unit_from(text, currency)
    return Measure(unit=unit, currency=currency, scale=scale)


def _parse_format(fmt: str) -> Measure:
    if not fmt:
        return Measure()
    if "%" in fmt:
        return Measure(unit="rate")
    currency = _currency_from(fmt)
    if currency:
        return Measure(unit="money", currency=currency)
    return Measure()


def _currency_from(text: str) -> str | None:
    blob = text.casefold()
    if "£" in text or "gbp" in blob or "pound" in blob:
        return "GBP"
    if "€" in text or "eur" in blob:
        return "EUR"
    if "₽" in text or "rub" in blob:
        return "RUB"
    if "$" in text or "usd" in blob:
        return "USD"
    return None


def _scale_from(text: str) -> str | None:
    blob = text.casefold()
    if re.search(r"\bbn\b|\bbillion", blob) or "bn£" in blob or "£bn" in blob:
        return "bn"
    if re.search(r"\bmillion", blob) or re.search(r"m[£$€₽]|[£$€₽]m\b", blob):
        return "m"
    if re.search(r"\bthousands?\b", blob) or re.search(r"(^|[^a-z])k[£$€₽]|[£$€₽]k\b", blob):
        return "k"
    return None


def _unit_from(text: str, currency: str | None) -> str | None:
    if "%" in text or "percent" in text.casefold():
        return "rate"
    if currency or re.search(r"[£$€₽]", text):
        return "money"
    if re.search(r"\bper year\b|\bof margin\b", text, re.I):
        return "rate"
    if _COUNT_TEXT.search(text) and _PER_YEAR.search(text):
        return "count"
    if _YEARS_TEXT.search(text) and not _PER_YEAR.search(text):
        return "years"
    if _COUNT_TEXT.search(text):
        return "count"
    return None


def _infer_sign(
    *,
    concept_id: str | None,
    statement: str | None,
    nature: str | None,
    time_semantics: str | None,
    label: str,
) -> str | None:
    if (
        nature == "balance"
        or statement == "bs"
        or (concept_id or "").startswith("bs.")
        or time_semantics in {"stock", "bop", "eop"}
    ):
        return "stock"
    cid = concept_id or ""
    if cid in _INFLOW_IDS or cid.startswith("cf.receipts"):
        return "inflow"
    if any(fragment in cid for fragment in _OUTFLOW_FRAGMENTS):
        return "outflow"
    tokens = set(re.sub(r"[^a-z0-9]+", " ", label.casefold()).split())
    if tokens & _INFLOW_TOKENS and not tokens & _OUTFLOW_TOKENS:
        return "inflow"
    if tokens & _OUTFLOW_TOKENS:
        return "outflow"
    return None
