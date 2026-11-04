"""Bare-year header detection stays put while the cheap path is in use.

``text200k`` values are ``n, n+1, n+2``. Inside 1900–2099 that run is a header.
Outside it, and on a broken step, it is a data row. An ASCII integer that is
not a year is not a header, including the row numbers that used to fill the
short-header cache.
"""

from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.layout.periods import classify_header


def _cell(addr: str, value: str) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": "Model",
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


def _row(excel_row: int, label: str, values: tuple[int, ...]) -> list[dict]:
    letters = "CDEFGH"
    cells = [
        _cell(f"A{excel_row}", label),
        _cell(f"B{excel_row}", "comment"),
    ]
    for offset, value in enumerate(values):
        cells.append(_cell(f"{letters[offset]}{excel_row}", str(value)))
    return cells


def test_ascii_integers_are_headers_only_inside_1900_2099() -> None:
    for number in [*range(0, 2200), 9999, 10000, 20000, 2099, 2100, 50000]:
        hit = classify_header(str(number))
        if 1900 <= number <= 2099:
            assert hit is not None
            assert hit.period_key == str(number)
            assert hit.explicit_role is False
        else:
            assert hit is None
    assert classify_header("2024.0") is not None
    assert classify_header("2024.0").period_key == "2024"
    assert classify_header(" 2024") is not None
    assert classify_header("2 024").period_key == "2024"
    assert classify_header("２０２４").period_key == "2024"
    assert classify_header("²024").period_key == "2024"
    assert classify_header("2024E").period_key == "2024"
    assert classify_header("2024E").explicit_role is True
    assert classify_header("1899") is None
    assert classify_header("2100") is None


def test_year_runs_keep_the_same_header_rows() -> None:
    """The text-book pattern: one real ruler, then ``n, n+1, n+2``."""
    cells = [
        *_row(1, "Статья", (2022, 2023, 2024)),
        *_row(2, "Outside", (2, 3, 4)),
        *_row(400, "Down", (2074, 2035, 1988)),
        *_row(1900, "Run a", (1900, 1901, 1902)),
        *_row(1901, "Run b", (1901, 1902, 1903)),
        *_row(2097, "Run c", (2097, 2098, 2099)),
        *_row(2098, "Edge", (2098, 2099, 2100)),
        *_row(5000, "Big", (5000, 5001, 5002)),
    ]
    sheet = detect_layout(cells).sheets[0]
    found = [
        (axis.header_row, [period.period_key for period in axis.periods])
        for axis in sheet.axes
    ]
    assert found == [
        (1, ["2022", "2023", "2024"]),
        (1900, ["1900", "1901", "1902"]),
        (1901, ["1901", "1902", "1903"]),
        (2097, ["2097", "2098", "2099"]),
    ]
    labels = [row.label for block in sheet.blocks for row in block.rows]
    assert "Outside" in labels
    assert "Down" in labels
    assert "Edge" in labels
    assert "Big" in labels
    assert "Run a" not in labels
    assert "Run c" not in labels
