from __future__ import annotations

import re

_UNIT_TEXT = re.compile(
    r"^(k?[£$€₽]|gbp|usd|eur|euro|pound|руб\.?|долл\.?|евро|"
    r"%|years?|year|months?|month|days?|day|"
    r"veh/?year|per year|of margin|mw|mwh|£/year|\$/year|€/year|"
    r"text|date|1/2/3|k£)$",
    re.IGNORECASE,
)


_UNIT_TOKEN = re.compile(
    r"^(?:(?:k|m|mn|bn)\s*)?(?:eur|usd|gbp|rub|chf|[£$€₽])\s*"
    r"(?:'?000|k|m|mn|bn|thousands?|millions?)?"
    r"(?:\s*/\s*(?:mwh|mw|mwp|kwh|kw|year|yr|month|unit|veh))?(?:\s*p\.?\s*a\.?)?$"
    r"|^%(?:\s*p\.?\s*a\.?)?$|^x$|^#$"
    r"|^(?:mwh|mw|mwp|kwh|kw|gwh)(?:\s*/\s*(?:mw|mwp|kw|year|yr))?$",
    re.IGNORECASE,
)


def is_unit_text(text: str | None) -> bool:
    from finance_context.context.measure import unit_caption

    caption = unit_caption(text)
    if caption is None:
        return False
    if caption.casefold() == "index":
        return True
    return bool(_UNIT_TEXT.fullmatch(caption) or _UNIT_TOKEN.fullmatch(caption))


def unit_kind_from_text(text: str | None) -> str | None:
    from finance_context.context.measure import currency_code_from_text, unit_caption

    caption = unit_caption(text)
    if caption is None:
        return None
    blob = caption.casefold()
    if blob == "index":
        return "ratio"
    if blob in {"#", "№"}:
        return "count"
    if "%" in blob or blob in {"per year", "of margin"}:
        return "rate"
    if currency_code_from_text(caption):
        return "money"
    if any(token in blob for token in ("year", "month", "day", "veh", "mw", "count")):
        return "count"
    if blob in {"text", "date"}:
        return None
    if _UNIT_TEXT.fullmatch(blob):
        return "count"
    return None
