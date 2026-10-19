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
            r"₽|\brubs?\b|\bруб\.?\b|\bрубл(?:ей|я|ь)?\b|(?<!\w)р\.(?!\w)",
            re.IGNORECASE,
        ),
    ),
)
# Number-format tag `[$₽-419]`: the currency symbol, then an optional locale id.
# `[$-419]` and `[$-F800]` carry only a locale, so they name no currency.
_FORMAT_CURRENCY_TAG = re.compile(r"\[\$([^\]\-]*)(?:-[^\]]*)?\]")
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
# ISO codes that are not ordinary English words. RUR is the old rouble code.
# TRY/ALL/PEN and the other word-like codes are recognized only next to a scale.
_SAFE_ISO = (
    "USD|EUR|GBP|RUB|RUR|CHF|JPY|CNY|CNH|CAD|AUD|NZD|SEK|NOK|DKK|PLN|CZK|HUF|"
    "RON|BGN|UAH|KZT|BYN|AZN|AMD|INR|IDR|MYR|THB|VND|PHP|SGD|HKD|TWD|KRW|AED|"
    "SAR|QAR|ILS|EGP|ZAR|BRL|MXN|ARS|CLP|ISK|RSD|XAU|XAG"
)
_SCALED_ISO = _SAFE_ISO + "|TRY|ALL|PEN|COP|GEL|MAD|TOP|SOS|CUP"
# Bare `000` is a number (`1000 USD`), not a scale. The quoted form is `RUB'000`.
_SCALE_TOKEN = r"mn|mm|bn|k|m|'000"
_ISO_SCALED = re.compile(
    rf"(?<![A-Za-z])(?:(?P<pre>{_SCALE_TOKEN})\s*)?(?P<code>{_SCALED_ISO})"
    rf"(?:\s*(?P<post>{_SCALE_TOKEN}))(?![A-Za-z0-9])"
    rf"|(?<![A-Za-z])(?P<pre2>{_SCALE_TOKEN})\s*(?P<code2>{_SCALED_ISO})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_ISO_BARE = re.compile(
    rf"(?<![A-Za-z])(?P<code>{_SAFE_ISO})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# "тыс. руб.", "т.р.", "млн ₽", "в тысячах рублей": the multiplier stands before the currency.
# Whole words only: "тысячная доля" and "миллионер" are not a scale.
_RU_THOUSANDS = re.compile(r"\bтыс\b|\bтысяч(?:а|и|ах)?\b|\bт(?:\.\s*|\s+)(?=руб|р\.)", re.I)
_RU_MILLIONS = re.compile(r"\bмлн\b|\bмиллион(?:а|ов|ах)?\b", re.I)
_RU_BILLIONS = re.compile(r"\bмлрд\b|\bмиллиард(?:а|ов|ах)?\b", re.I)
# A label that is only the multiplier word names the multiplier row (`Thousand` = 1000).
_MULTIPLIER_WORD = re.compile(
    r"thousands?|millions?|billions?|тысяч[аи]?|миллион(?:а|ов)?|миллиард(?:а|ов)?"
)
_RATIO_TEXT = re.compile(r"^\s*x\s*$", re.I)
_DATE_TEXT = re.compile(r"^\s*date\s*$", re.I)
# A trailing period marker is not a second unit: "MWh p.a." is the same count as "MWh".
_PER_ANNUM_SUFFIX = re.compile(
    r"(?:\s+(?:p\.?\s*a\.?|pa|per\s+annum)|(?<=\S)p\.a\.)\s*$",
    re.IGNORECASE,
)


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
    nature: str | None = None,
    time_semantics: str | None = None,
) -> Measure:
    measure = Measure()
    measure = _fill(measure, _parse_text(unit_text or ""))
    measure = _fill(measure, _parse_text(label or ""))
    # Base units only when the unit cell or label names the currency with no multiplier.
    # A number format names the currency, not the scale of the stored amount.
    currency_in_text = measure.currency is not None
    for fmt in number_formats or []:
        measure = _fill(measure, _parse_format(fmt))
    if measure.unit in {"money", "price"} and measure.scale is None and currency_in_text:
        measure = Measure(
            unit=measure.unit,
            currency=measure.currency,
            scale="unit",
            sign=measure.sign,
            per=measure.per,
        )
    sign = _infer_sign(
        nature=nature,
        time_semantics=time_semantics,
        label=label or "",
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


def unit_caption(text: str | None) -> str | None:
    """Unit-cell text with a trailing per-annum marker removed.

    The whole cell ``index`` is kept so callers can read it as a ratio.
    ``Base Index`` is a heading, not this caption. A cell that is only the
    period marker is not a unit.
    """
    blob = (text or "").strip()
    if not blob:
        return None
    if blob.casefold() == "index":
        return blob
    stripped = _PER_ANNUM_SUFFIX.sub("", blob).strip()
    if not stripped or stripped.casefold() in {"p.a.", "p.a", "pa", "per annum"}:
        return None
    return stripped


def currency_code_from_text(text: str | None) -> str | None:
    if not text:
        return None
    for symbol in _FORMAT_CURRENCY_TAG.findall(text):
        code = _currency_code(symbol)
        if code:
            return code
    return _currency_code(_FORMAT_CURRENCY_TAG.sub(" ", text))


def _currency_code(text: str) -> str | None:
    for code, pattern in _CURRENCY_PATTERNS:
        if pattern.search(text):
            return code
    scaled = _ISO_SCALED.search(text)
    if scaled:
        return _normalize_iso(scaled.group("code") or scaled.group("code2") or "")
    bare = _ISO_BARE.search(text)
    if bare:
        return _normalize_iso(bare.group("code"))
    return None


def _normalize_iso(code: str) -> str:
    upper = code.upper()
    if upper == "RUR":
        return "RUB"
    return upper


def is_iso_unit_caption(text: str | None) -> bool:
    """A cell that is only an ISO currency, optionally with a scale (`RURm`, `USD bn`)."""
    blob = (text or "").strip()
    if not blob:
        return False
    return bool(_ISO_SCALED.fullmatch(blob) or _ISO_BARE.fullmatch(blob))


def _iso_scale(text: str) -> str | None:
    match = _ISO_SCALED.search(text)
    if match is None:
        return None
    token = (
        match.group("pre") or match.group("post") or match.group("pre2") or ""
    ).casefold()
    if token in {"m", "mn", "mm"}:
        return "m"
    if token in {"k", "'000", "000"}:
        return "k"
    if token == "bn":
        return "bn"
    return None


def _scale_from(text: str) -> str | None:
    blob = text.casefold().strip()
    # The row *is* the multiplier (`Thousand` = 1000), not an amount in thousands.
    if _MULTIPLIER_WORD.fullmatch(blob):
        return None
    if re.search(r"\bbn\b|\bbillion", blob) or "bn£" in blob or "£bn" in blob:
        return "bn"
    if _RU_BILLIONS.search(blob):
        return "bn"
    if re.search(r"\bmillion", blob) or re.search(r"m[£$€₽]|[£$€₽]m\b", blob):
        return "m"
    if _RU_MILLIONS.search(blob):
        return "m"
    if currency_code_from_text(text) and _MILLIONS.search(text):
        return "m"
    if re.search(r"\bthousands?\b", blob) or re.search(r"(^|[^a-z])k[£$€₽]|[£$€₽]k\b", blob):
        return "k"
    if currency_code_from_text(text) and _THOUSANDS.search(text):
        return "k"
    if _RU_THOUSANDS.search(blob):
        return "k"
    return _iso_scale(text)


def _unit_from(text: str, currency: str | None) -> str | None:
    caption = unit_caption(text)
    if caption is None:
        return None
    if caption.casefold() == "index":
        return "ratio"
    text = caption
    if text.strip() in {"#", "№"}:
        return "count"
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


def _infer_sign(
    *,
    nature: str | None,
    time_semantics: str | None,
    label: str,
) -> str | None:
    if nature == "balance" or time_semantics in {"stock", "bop", "eop"}:
        return "stock"
    tokens = set(re.sub(r"[^a-z0-9]+", " ", label.casefold()).split())
    if tokens & _REPAYMENT_TOKENS:
        return "outflow"
    if tokens & _INFLOW_TOKENS and not tokens & _OUTFLOW_TOKENS:
        return "inflow"
    if tokens & _OUTFLOW_TOKENS:
        return "outflow"
    return None
