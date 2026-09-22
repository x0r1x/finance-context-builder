from __future__ import annotations

import re
from dataclasses import dataclass

_CURRENCY_SYM = {"GBP": "£", "EUR": "€", "USD": "$", "RUB": "₽"}
_CURRENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "GBP",
        re.compile(
            r"£|\bgbp\b|\bpounds?\b|\bsterling\b|\bфунт(?:ов|а|ы)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "EUR",
        re.compile(
            r"€|\beur\b|\beuros?\b|\bевро\b",
            re.IGNORECASE,
        ),
    ),
    (
        "USD",
        re.compile(
            r"\$|\busd\b|\bdollars?\b|\bus\$|\bдолл(?:ар(?:ов|а|ы)?)?\.?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "RUB",
        re.compile(
            r"₽|\brubs?\b|\bруб\.?\b|\bрубл(?:ей|я|ь)?\b",
            re.IGNORECASE,
        ),
    ),
)
_INFLOW_IDS = (
    "pnl.revenue",
    "pnl.other_income",
    "cf.receipts",
    "cf.receipts.other",
)
_OUTFLOW_ID_TOKENS = {
    "opex",
    "capex",
    "uses",
    "tax",
    "interest",
    "fee",
    "maintenance",
}
_REPAYMENT_TOKENS = {"repayment", "principal"}
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
_COUNT_TEXT = re.compile(r"\b(months?|days?|hours?|veh|mw|mwh|count)\b", re.I)
_PER_YEAR = re.compile(r"per year|/year", re.I)
_COUNT_PER = re.compile(r"^\s*(months?|days?|hours?|weeks?)\s+(per|in a|/)\s+", re.I)
_PER_UNIT = re.compile(r"/\s*(mwh|mw|mwp|kwh|kw|gwh|unit|veh|tonne|t|bbl|m3)\b", re.I)
_THOUSANDS = re.compile(r"'000(?!')|\b000s\b|\(000\)", re.I)
_MILLIONS = re.compile(r"'000'000|\bmn\b|\bmm\b", re.I)
_RATIO_TEXT = re.compile(r"^\s*x\s*$", re.I)
_DATE_TEXT = re.compile(r"^\s*date\s*$", re.I)


@dataclass(frozen=True)
class Measure:
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None
    sign: str | None = None
    per: str | None = None

    def display(self) -> str:
        if self.unit == "rate":
            return "%"
        if self.unit == "years":
            return "years"
        if self.unit == "count":
            return "count"
        if self.unit == "ratio":
            return "x"
        if self.unit == "date":
            return "date"
        if self.unit in {"money", "price"}:
            symbol = _CURRENCY_SYM.get(self.currency or "", "")
            prefix = "" if self.scale in (None, "unit") else self.scale
            composed = f"{prefix}{symbol}"
            if self.unit == "price":
                return f"{composed or 'money'}/{self.per or 'unit'}"
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
    direction: str | None = None,
) -> Measure:
    measure = Measure()
    measure = _fill(measure, _parse_text(unit_text or ""))
    measure = _fill(measure, _parse_text(label or ""))
    for fmt in number_formats or []:
        measure = _fill(measure, _parse_format(fmt))
    if measure.unit in {"money", "price"} and measure.scale is None:
        measure = Measure(
            unit=measure.unit,
            currency=measure.currency,
            scale="unit",
            sign=measure.sign,
            per=measure.per,
        )
    sign = _infer_sign(
        concept_id=concept_id,
        statement=statement,
        nature=nature,
        time_semantics=time_semantics,
        label=label or "",
        direction=direction,
    )
    return Measure(
        unit=measure.unit,
        currency=measure.currency,
        scale=measure.scale,
        sign=sign,
        per=measure.per,
    )


def _fill(base: Measure, extra: Measure) -> Measure:
    if base.unit:
        return Measure(
            unit=base.unit,
            currency=base.currency or extra.currency,
            scale=base.scale or extra.scale,
            sign=base.sign or extra.sign,
            per=base.per,
        )
    return Measure(
        unit=extra.unit,
        currency=base.currency or extra.currency,
        scale=base.scale or extra.scale,
        sign=base.sign or extra.sign,
        per=extra.per,
    )


def _parse_text(text: str) -> Measure:
    if not text.strip():
        return Measure()
    currency = currency_code_from_text(text)
    scale = _scale_from(text)
    unit = _unit_from(text, currency)
    per = None
    if unit == "money":
        match = _PER_UNIT.search(text)
        if match:
            unit = "price"
            per = _per_label(match.group(1))
    return Measure(unit=unit, currency=currency, scale=scale, per=per)


def _per_label(token: str) -> str:
    upper = {"mwh": "MWh", "mw": "MW", "mwp": "MWp", "kwh": "kWh", "kw": "kW", "gwh": "GWh"}
    return upper.get(token.casefold(), token.casefold())


def _parse_format(fmt: str) -> Measure:
    if not fmt:
        return Measure()
    if "%" in fmt:
        return Measure(unit="rate")
    currency = currency_code_from_text(fmt)
    if currency:
        return Measure(unit="money", currency=currency)
    return Measure()


def currency_code_from_text(text: str | None) -> str | None:
    if not text:
        return None
    for code, pattern in _CURRENCY_PATTERNS:
        if pattern.search(text):
            return code
    return None


def _scale_from(text: str) -> str | None:
    blob = text.casefold()
    if re.search(r"\bbn\b|\bbillion", blob) or "bn£" in blob or "£bn" in blob:
        return "bn"
    if re.search(r"\bmillion", blob) or re.search(r"m[£$€₽]|[£$€₽]m\b", blob):
        return "m"
    if currency_code_from_text(text) and _MILLIONS.search(text):
        return "m"
    if re.search(r"\bthousands?\b", blob) or re.search(r"(^|[^a-z])k[£$€₽]|[£$€₽]k\b", blob):
        return "k"
    if currency_code_from_text(text) and _THOUSANDS.search(text):
        return "k"
    return None


def _unit_from(text: str, currency: str | None) -> str | None:
    if "%" in text or "percent" in text.casefold():
        return "rate"
    if _RATIO_TEXT.match(text):
        return "ratio"
    if _DATE_TEXT.match(text):
        return "date"
    if currency or re.search(r"[£$€₽]", text):
        return "money"
    if _COUNT_PER.match(text):
        return "count"
    if re.search(r"\bper year\b|\bof margin\b", text, re.I):
        return "rate"
    if _COUNT_TEXT.search(text) and _PER_YEAR.search(text):
        return "count"
    if _YEARS_TEXT.search(text) and not _PER_YEAR.search(text):
        return "years"
    if _COUNT_TEXT.search(text):
        return "count"
    return None


def _concept_id_tokens(concept_id: str) -> set[str]:
    protected = concept_id.replace("pre_tax", "pretax").replace("pre-tax", "pretax")
    return set(re.split(r"[._]", protected)) - {""}


def _infer_sign(
    *,
    concept_id: str | None,
    statement: str | None,
    nature: str | None,
    time_semantics: str | None,
    label: str,
    direction: str | None = None,
) -> str | None:
    if (
        nature == "balance"
        or statement == "bs"
        or (concept_id or "").startswith("bs.")
        or time_semantics in {"stock", "bop", "eop"}
    ):
        return "stock"
    if direction in {"inflow", "outflow"}:
        return direction
    cid = concept_id or ""
    if cid in _INFLOW_IDS or cid.startswith("cf.receipts"):
        return "inflow"
    if cid == "cf.repayment" or cid.startswith("cf.repayment."):
        return "outflow"
    if "debt.service" in cid or _concept_id_tokens(cid) & _OUTFLOW_ID_TOKENS:
        return "outflow"
    tokens = set(re.sub(r"[^a-z0-9]+", " ", label.casefold()).split())
    if tokens & _REPAYMENT_TOKENS:
        return "outflow"
    if tokens & _INFLOW_TOKENS and not tokens & _OUTFLOW_TOKENS:
        return "inflow"
    if tokens & _OUTFLOW_TOKENS:
        return "outflow"
    return None
