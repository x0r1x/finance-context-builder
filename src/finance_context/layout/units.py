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
    r"|^(?:mwh|mw|mwp|kwh|kw|gwh)(?:\s*/\s*(?:mw|mwp|kw|year|yr))?$"
    r"|^(?:(?:тыс|млн|млрд)\.?\s*|т(?:\.\s*|\s+)"
    r"|(?:тысяч[аи]?|миллион(?:а|ов)?|миллиард(?:а|ов)?)\s+)?"
    r"(?:руб(?:\.|лей)?|р\.|₽)$",
    re.IGNORECASE,
)


# A timeline caption. It is not the measure of the rows under that header.
_PERIOD_BANNER = re.compile(
    r"^(?:years?|months?|quarters?|weeks?|days?|dates?|год(?:а|ов)?|лет)$",
    re.IGNORECASE,
)
# A scale marker or a symbol. A bare word such as ``Euro`` or ``Year`` is a line item.
_UNIT_BANNER = re.compile(r"[£$€₽%#]|'000|\d|тыс|млн|млрд|руб", re.IGNORECASE)


def is_block_unit_caption(text: str | None) -> bool:
    """A caption that sets the measure for the following rows.

    ``RURm``, ``RUR``, ``k£``, ``EUR'000`` and ``тыс. руб.`` do. The header
    word ``Year`` and a currency list line ``Euro`` do not.
    """
    from finance_context.context.measure import is_iso_unit_caption, unit_caption

    caption = unit_caption(text)
    if caption is None or _PERIOD_BANNER.fullmatch(caption):
        return False
    if is_iso_unit_caption(caption):
        return True
    if not is_unit_text(caption):
        return False
    return _UNIT_BANNER.search(caption) is not None


def is_unit_text(text: str | None) -> bool:
    from finance_context.context.measure import unit_caption

    caption = unit_caption(text)
    if caption is None:
        return False
    if caption.casefold() == "index":
        return True
    if _UNIT_TEXT.fullmatch(caption) or _UNIT_TOKEN.fullmatch(caption):
        return True
    from finance_context.context.measure import is_iso_unit_caption

    return is_iso_unit_caption(caption)


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
