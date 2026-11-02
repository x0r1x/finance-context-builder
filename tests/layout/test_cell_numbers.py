from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.cells import _is_int_text, _is_number, parse_int, parse_number
from finance_context.layout.detect import detect_layout


def test_parse_number_accepts_grouped_and_decimal_text() -> None:
    assert parse_number("1 230") == 1230
    assert parse_number("1 230,5") == 1230.5
    assert parse_number("1\xa0230,5") == 1230.5
    assert parse_number("1\u202f230") == 1230
    assert parse_number("-12,5") == -12.5
    assert parse_int("2024") == 2024
    assert parse_int("1 230") == 1230


def test_parse_number_rejects_non_finite_and_underscores() -> None:
    for text in ("inf", "INF", "-inf", "nan", "NaN", "1e400", "1_000", ""):
        assert parse_number(text) is None
        assert parse_int(text) is None
        assert not _is_number(text)
        assert not _is_int_text(text)


def _c(sheet: str, addr: str, value: str | None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def test_layout_keeps_going_when_a_header_cell_is_not_a_finite_number() -> None:
    cells = [
        _c("CF", "A1", "Показатель"),
        _c("CF", "B1", "2023"),
        _c("CF", "C1", "2024"),
        _c("CF", "D1", "inf"),
        _c("CF", "A2", "Выручка"),
        _c("CF", "B2", "100"),
        _c("CF", "C2", "110"),
        _c("CF", "D2", "1 230"),
        _c("CF", "A3", "Год"),
        _c("CF", "B3", "1"),
        _c("CF", "C3", "2"),
        _c("CF", "D3", "nan"),
        _c("CF", "E3", "1_000"),
        _c("CF", "F3", "1\xa0230,5"),
        _c("CF", "A6", "Параметр"),
        _c("CF", "B6", "Значение"),
        _c("CF", "A7", "Ставка"),
        _c("CF", "B7", "1 230,5"),
        _c("CF", "A8", "Мусор"),
        _c("CF", "B8", "inf"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert sheet.blocks
    periods = [period.text for axis in sheet.axes for period in axis.periods]
    assert "2023" in periods
    assert "2024" in periods
    assert "inf" not in periods
    assert "nan" not in periods
