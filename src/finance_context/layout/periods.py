from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from finance_context.excel.dates import is_date_format, serial_to_date
from finance_context.layout.models import ColumnRole, PeriodHit

_YEAR = re.compile(r"^(?:FY\s*)?((?:19|20)\d{2})([EFAPеЕфФпПaA])?$", re.IGNORECASE)
_YEAR_FLOAT = re.compile(r"^((?:19|20)\d{2})(?:\.0+)?$")
_QUARTER_RU = re.compile(
    r"^([1-4])\s*кв\.?\s*((?:19|20)\d{2})([EFAPеЕ])?$",
    re.IGNORECASE,
)
_QUARTER_EN = re.compile(r"^Q([1-4])\s*((?:19|20)\d{2})([EFAP])?$", re.IGNORECASE)
_QUARTER_TOKEN = re.compile(
    r"^(?:([1-4])\s*кв\.?|Q([1-4]))$",
    re.IGNORECASE,
)
_TOTAL = re.compile(r"^(итого|всего|total|sum)$", re.IGNORECASE)
_STUB = re.compile(r"^(stub|частичн\w*|partial)$", re.IGNORECASE)
_SCENARIO = re.compile(
    r"^(base(?:\s*case)?|upside|downside|негативн\w*|позитивн\w*|stress|сценарий(?:\s+\w+)?)$",
    re.IGNORECASE,
)
_FACT = re.compile(r"^(факт\w*|actual|hist(?:orical)?)$", re.IGNORECASE)
_PLAN = re.compile(r"^(план\w*|прогноз\w*|forecast|budget)$", re.IGNORECASE)
_TAG = re.compile(
    r"^(.*?)\s+(факт\w*|actual|hist(?:orical)?|план\w*|прогноз\w*|forecast|budget)$",
    re.IGNORECASE,
)
_FORECAST_SFX = {"E", "F", "P", "Е", "П"}
_ISO_YMD = re.compile(
    r"^((?:19|20)\d{2})-(\d{2})-(\d{2})([EFAPеЕфФпП])?$"
)
_ISO_YM = re.compile(r"^((?:19|20)\d{2})-(\d{2})([EFAPеЕфФпП])?$")
_DATE_DMY = re.compile(
    r"^([0-3]?\d)[./]([01]?\d)[./]((?:19|20)\d{2}|\d{2})([EFAPеЕфФпП])?$"
)
_NAMED_MONTH = re.compile(
    r"^([A-Za-zА-Яа-яёЁ]+)\.?\s*[-/.]?\s*((?:19|20)\d{2}|\d{2})([EFAPеЕфФпП])?$",
    re.IGNORECASE,
)
_QUARTER_KEY = re.compile(r"^((?:19|20)\d{2})Q([1-4])$")
_MONTH_KEY = re.compile(r"^((?:19|20)\d{2})-(\d{2})$")
_YEAR_KEY = re.compile(r"^((?:19|20)\d{2})$")
_DATE_KEY = re.compile(r"^((?:19|20)\d{2})-(\d{2})-(\d{2})$")
_QUARTER_LABEL = re.compile(r"квартал|quarter", re.IGNORECASE)
_MONTH_LABEL = re.compile(r"месяц|month", re.IGNORECASE)
_START_PERIOD = re.compile(
    r"начал[оа]\s+период|start\s+of\s+period|period\s+start",
    re.IGNORECASE,
)
_END_PERIOD = re.compile(
    r"конец\s+период|end\s+of\s+period|period\s+end",
    re.IGNORECASE,
)
_MONTH_NUM = {
    "янв": 1,
    "январ": 1,
    "фев": 2,
    "феврал": 2,
    "мар": 3,
    "март": 3,
    "апр": 4,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июня": 6,
    "июл": 7,
    "июля": 7,
    "авг": 8,
    "август": 8,
    "сен": 9,
    "сентябр": 9,
    "окт": 10,
    "октябр": 10,
    "ноя": 11,
    "ноябр": 11,
    "дек": 12,
    "декабр": 12,
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

LAYER_DATE = "date"
LAYER_YEAR = "year"
LAYER_QUARTER = "quarter"
LAYER_MONTH = "month"
LAYER_ROLE = "role"
LAYER_BOUNDS = "bounds"


@dataclass(frozen=True)
class HeaderAtom:
    kind: str
    year: str | None = None
    quarter: int | None = None
    month: int | None = None
    day: int | None = None
    role: ColumnRole | None = None
    period_key: str | None = None
    explicit_role: bool = False


def normalize_header(text: str | None) -> str | None:
    if text is None:
        return None
    raw = re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()
    if not raw:
        return None
    return re.sub(r"(?<=\d)\s+(?=\d)", "", raw)


def classify_header(text: str | None) -> PeriodHit | None:
    raw = normalize_header(text)
    if not raw:
        return None
    if _TOTAL.fullmatch(raw):
        return PeriodHit(role="total", period_key="total", explicit_role=True)
    if _STUB.fullmatch(raw):
        return PeriodHit(role="stub", period_key="stub", explicit_role=True)
    if _SCENARIO.fullmatch(raw):
        return PeriodHit(role="scenario", period_key=raw.lower(), explicit_role=True)
    if _FACT.fullmatch(raw):
        return PeriodHit(role="historical", period_key="actual", explicit_role=True)
    if _PLAN.fullmatch(raw):
        return PeriodHit(role="forecast", period_key="plan", explicit_role=True)
    tagged = _tagged_hit(raw)
    if tagged is not None:
        return tagged
    iso_d = _ISO_YMD.fullmatch(raw)
    if iso_d:
        hit = _ymd_hit(
            int(iso_d.group(1)),
            int(iso_d.group(2)),
            int(iso_d.group(3)),
            iso_d.group(4),
        )
        if hit is not None:
            return hit
    iso = _ISO_YM.fullmatch(raw)
    if iso:
        month = int(iso.group(2))
        if 1 <= month <= 12:
            return PeriodHit(
                role=_suffix_role(iso.group(3)),
                period_key=f"{iso.group(1)}-{month:02d}",
                explicit_role=bool(iso.group(3)),
            )
    dmy = _DATE_DMY.fullmatch(raw)
    if dmy:
        year = _full_year(dmy.group(3))
        hit = _ymd_hit(
            int(year),
            int(dmy.group(2)),
            int(dmy.group(1)),
            dmy.group(4),
        )
        if hit is not None:
            return hit
    named = _month_hit(raw)
    if named is not None:
        return named
    quarter = _QUARTER_RU.fullmatch(raw) or _QUARTER_EN.fullmatch(raw)
    if quarter:
        q, year, sfx = quarter.group(1), quarter.group(2), quarter.group(3)
        return PeriodHit(
            role=_suffix_role(sfx),
            period_key=f"{year}Q{q}",
            explicit_role=bool(sfx),
        )
    year = _YEAR.fullmatch(raw)
    if year:
        return PeriodHit(
            role=_suffix_role(year.group(2)),
            period_key=year.group(1),
            explicit_role=bool(year.group(2)),
        )
    as_year = _YEAR_FLOAT.fullmatch(raw)
    if as_year:
        return PeriodHit(role="historical", period_key=as_year.group(1))
    return None


def classify_atom(
    text: str | None,
    *,
    allow_quarter_num: bool = False,
    allow_month_num: bool = False,
) -> HeaderAtom | None:
    raw = normalize_header(text)
    if not raw:
        return None
    if _is_time_factor(raw):
        return HeaderAtom(kind="noise")
    hit = classify_header(raw)
    if hit is not None:
        if hit.period_key in {"actual", "plan"}:
            return HeaderAtom(kind="role_marker", role=hit.role, explicit_role=True)
        parsed = parse_period_key(hit.period_key)
        return HeaderAtom(
            kind="full",
            year=parsed[0] if parsed else None,
            quarter=parsed[1] if parsed else None,
            month=parsed[2] if parsed else None,
            day=parsed[3] if parsed else None,
            role=hit.role,
            period_key=hit.period_key,
            explicit_role=hit.explicit_role,
        )
    qtok = _QUARTER_TOKEN.fullmatch(raw)
    if qtok:
        return HeaderAtom(kind="quarter_token", quarter=int(qtok.group(1) or qtok.group(2)))
    if allow_quarter_num and _is_int_token(raw):
        num = int(raw)
        if 1 <= num <= 4:
            return HeaderAtom(kind="quarter_token", quarter=num)
    if allow_month_num and _is_int_token(raw):
        num = int(raw)
        if 1 <= num <= 12:
            return HeaderAtom(kind="month_num", month=num)
    return None


def compose_period(
    atoms: list[HeaderAtom],
    *,
    year_hint: str | None = None,
) -> PeriodHit | None:
    calendar = [atom for atom in atoms if atom.kind != "noise"]
    if not calendar:
        return None
    years = {atom.year for atom in calendar if atom.year}
    if len(years) > 1:
        return None
    structural = next(
        (
            atom
            for atom in calendar
            if atom.period_key in {"total", "stub"} or (atom.role == "scenario" and atom.period_key)
        ),
        None,
    )
    if structural is not None and structural.period_key:
        return PeriodHit(role=structural.role or "total", period_key=structural.period_key)
    temporal = [
        atom
        for atom in calendar
        if atom.kind != "role_marker"
        and (
            atom.kind in {"full", "quarter_token", "month_num"}
            or atom.year
            or atom.quarter
            or atom.month
        )
    ]
    if not temporal:
        return None
    role = _compose_role(calendar)
    date_atom = next(
        (atom for atom in calendar if atom.day is not None and atom.month and atom.year),
        None,
    )
    if date_atom is not None and date_atom.year and date_atom.month and date_atom.day:
        return PeriodHit(
            role=role,
            period_key=f"{date_atom.year}-{date_atom.month:02d}-{date_atom.day:02d}",
        )
    year = next(iter(years), None) or year_hint
    quarter = next((atom.quarter for atom in calendar if atom.quarter), None)
    month = next((atom.month for atom in calendar if atom.month and atom.day is None), None)
    if year and quarter:
        return PeriodHit(role=role, period_key=f"{year}Q{quarter}")
    if year and month:
        return PeriodHit(role=role, period_key=f"{year}-{month:02d}")
    full = next((atom for atom in calendar if atom.kind == "full" and atom.period_key), None)
    if full is not None and full.period_key and is_calendar_key(full.period_key):
        return PeriodHit(role=role, period_key=full.period_key)
    if year:
        return PeriodHit(role=role, period_key=year)
    return None


def is_calendar_key(key: str | None) -> bool:
    if not key:
        return False
    return bool(
        _YEAR_KEY.fullmatch(key)
        or _MONTH_KEY.fullmatch(key)
        or _DATE_KEY.fullmatch(key)
        or _QUARTER_KEY.fullmatch(key)
    )


def is_calendar_hit(hit: PeriodHit | None) -> bool:
    return hit is not None and is_calendar_key(hit.period_key)


def parse_period_key(key: str) -> tuple[str | None, int | None, int | None, int | None]:
    ymd = _DATE_KEY.fullmatch(key)
    if ymd:
        return ymd.group(1), None, int(ymd.group(2)), int(ymd.group(3))
    ym = _MONTH_KEY.fullmatch(key)
    if ym:
        return ym.group(1), None, int(ym.group(2)), None
    qk = _QUARTER_KEY.fullmatch(key)
    if qk:
        return qk.group(1), int(qk.group(2)), None, None
    yk = _YEAR_KEY.fullmatch(key)
    if yk:
        return yk.group(1), None, None, None
    return None, None, None, None


def infer_grain(keys: list[str]) -> str | None:
    calendar = [key for key in keys if is_calendar_key(key)]
    if len(calendar) < 2:
        return None
    dates = [_key_as_date(key) for key in calendar]
    if all(item is not None for item in dates):
        ordered = sorted(zip(dates, calendar, strict=True), key=lambda item: item[0] or date.min)
        steps: list[int] = []
        for (prev, _), (cur, _) in zip(ordered, ordered[1:], strict=False):
            if prev is None or cur is None:
                continue
            delta = (cur.year - prev.year) * 12 + (cur.month - prev.month)
            if delta > 0:
                steps.append(delta)
        if steps:
            unique = set(steps)
            if len(unique) == 1 or (max(steps) - min(steps) <= 1):
                med = sorted(steps)[len(steps) // 2]
                if med <= 1:
                    return "month"
                if 2 <= med <= 4:
                    return "quarter"
                if 11 <= med <= 13:
                    return "year"
            return None
    if all(_QUARTER_KEY.fullmatch(key) for key in calendar):
        return "quarter"
    if all(_MONTH_KEY.fullmatch(key) for key in calendar):
        return "month"
    if all(_YEAR_KEY.fullmatch(key) for key in calendar):
        return "year"
    return None


def apply_grain(key: str, grain: str | None) -> str:
    if not grain or not is_calendar_key(key):
        if _DATE_KEY.fullmatch(key):
            return key[:7]
        return key
    year, quarter, month, _day = parse_period_key(key)
    if year is None:
        return key
    if grain == "year":
        return year
    if grain == "quarter":
        if quarter:
            return f"{year}Q{quarter}"
        if month:
            return f"{year}Q{(month - 1) // 3 + 1}"
        return key
    if grain == "month":
        if _DATE_KEY.fullmatch(key) or _MONTH_KEY.fullmatch(key):
            return f"{year}-{month:02d}" if month else key
        return key
    return key


def display_cell_text(
    value: str | None,
    number_format: str | None,
    *,
    date1904: bool = False,
) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    if is_date_format(number_format):
        parsed = serial_to_date(text, date1904=date1904)
        if parsed is not None:
            return parsed.strftime("%d.%m.%Y")
    return text


def is_quarter_label(text: str | None) -> bool:
    raw = normalize_header(text)
    return bool(raw and _QUARTER_LABEL.search(raw))


def is_month_label(text: str | None) -> bool:
    raw = normalize_header(text)
    return bool(raw and _MONTH_LABEL.search(raw))


def is_start_period_label(text: str | None) -> bool:
    raw = normalize_header(text)
    return bool(raw and _START_PERIOD.search(raw))


def is_end_period_label(text: str | None) -> bool:
    raw = normalize_header(text)
    return bool(raw and _END_PERIOD.search(raw))


def is_role_marker_text(text: str | None) -> bool:
    atom = classify_atom(text)
    return atom is not None and atom.kind == "role_marker"


def _tagged_hit(raw: str) -> PeriodHit | None:
    match = _TAG.fullmatch(raw)
    if match is None:
        return None
    base = classify_header(match.group(1).strip())
    if base is None or base.role not in {"historical", "forecast", "stub"}:
        return None
    tag = match.group(2)
    role: ColumnRole = "historical" if _FACT.fullmatch(tag) else "forecast"
    return PeriodHit(role=role, period_key=base.period_key, explicit_role=True)


def _month_hit(raw: str) -> PeriodHit | None:
    match = _NAMED_MONTH.fullmatch(raw)
    if match is None:
        return None
    month = _month_number(match.group(1))
    if month is None:
        return None
    year = _full_year(match.group(2))
    return PeriodHit(
        role=_suffix_role(match.group(3)),
        period_key=f"{year}-{month:02d}",
        explicit_role=bool(match.group(3)),
    )


def _ymd_hit(year: int, month: int, day: int, suffix: str | None) -> PeriodHit | None:
    try:
        date(year, month, day)
    except ValueError:
        return None
    return PeriodHit(
        role=_suffix_role(suffix),
        period_key=f"{year:04d}-{month:02d}-{day:02d}",
        explicit_role=bool(suffix),
    )


def _month_number(token: str) -> int | None:
    stem = token.lower().replace("ё", "е").rstrip(".")
    found: int | None = None
    best = 0
    for name, num in _MONTH_NUM.items():
        if stem == name or stem.startswith(name):
            if len(name) > best:
                found = num
                best = len(name)
    return found


def _full_year(raw: str) -> str:
    if len(raw) == 2:
        return f"20{raw}"
    return raw


def _suffix_role(suffix: str | None) -> ColumnRole:
    if not suffix:
        return "historical"
    if suffix.upper() in _FORECAST_SFX:
        return "forecast"
    return "historical"


def _compose_role(atoms: list[HeaderAtom]) -> ColumnRole:
    for atom in atoms:
        if atom.explicit_role and atom.role in {"historical", "forecast", "stub"}:
            return atom.role
    return "historical"


def _key_as_date(key: str) -> date | None:
    year, quarter, month, day = parse_period_key(key)
    if year is None:
        return None
    y = int(year)
    if day and month:
        try:
            return date(y, month, day)
        except ValueError:
            return None
    if month:
        try:
            return date(y, month, 1)
        except ValueError:
            return None
    if quarter:
        return date(y, (quarter - 1) * 3 + 1, 1)
    return date(y, 1, 1)


def _is_time_factor(raw: str) -> bool:
    try:
        value = float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return 0 < value < 1


def _is_int_token(raw: str) -> bool:
    try:
        value = float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return value == int(value)
