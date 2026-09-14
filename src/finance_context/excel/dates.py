from __future__ import annotations

import re
from datetime import date, timedelta

_ESCAPED = re.compile(r'\\.')
_BRACKET = re.compile(r"\[[^\]]*\]")
_QUOTED = re.compile(r'"[^"]*"')
_GENERAL = re.compile(r"^(general|standard|@)$", re.IGNORECASE)

# Excel 1900 serial 1 = 1900-01-01; serial 60 is the fake leap day.
_EXCEL_MIN = 1
_EXCEL_MAX = 2958465  # 9999-12-31 in the 1900 system


def is_date_format(fmt: str | None) -> bool:
    if fmt is None:
        return False
    first = fmt.split(";")[0].strip()
    if not first or _GENERAL.fullmatch(first):
        return False
    token = _QUOTED.sub("", _BRACKET.sub("", _ESCAPED.sub("", first))).lower()
    token = token.replace("e+", "").replace("e-", "")
    if not token:
        return False
    has_y = "y" in token
    has_d = "d" in token
    has_h = "h" in token
    has_s = "s" in token
    has_m = "m" in token
    if has_y or has_d:
        return True
    if has_m and not has_h and not has_s:
        return True
    return False


def serial_to_date(serial: float | int | str, *, date1904: bool = False) -> date | None:
    try:
        value = float(str(serial).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    if value != value:  # NaN
        return None
    day = int(value)
    if date1904:
        if day < 0:
            return None
        try:
            return date(1904, 1, 1) + timedelta(days=day)
        except OverflowError:
            return None
    if day == 60:
        return None
    if day < _EXCEL_MIN or day > _EXCEL_MAX:
        return None
    try:
        if day < 60:
            return date(1899, 12, 31) + timedelta(days=day)
        return date(1899, 12, 30) + timedelta(days=day)
    except OverflowError:
        return None
