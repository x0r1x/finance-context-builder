"""Cached values from a real `.xls` replace whatever LibreOffice wrote."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import xlrd
import xlwt
from tests.helpers.converter import FakeConverter
from tests.helpers.ole2 import XLS_STREAMS, write_ole2
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel.legacy import ensure_xlsx
from finance_context.excel.ooxml import parse_ooxml
from finance_context.excel.xls_cache import overlay_xls_cache
from finance_context.excel.zip_guard import open_xlsx_zip

_PRECISE = 2035.0000000000002


def _cells(path: Path) -> dict[tuple[str, str], str | None]:
    with open_xlsx_zip(path) as zf:
        cells, _meta, _presence = parse_ooxml(zf)
    return {(cell.sheet, cell.addr): cell.cached_value for cell in cells}


def _formulas(path: Path) -> dict[tuple[str, str], str | None]:
    with open_xlsx_zip(path) as zf:
        cells, _meta, _presence = parse_ooxml(zf)
    return {(cell.sheet, cell.addr): cell.formula_raw for cell in cells}


def test_overlay_restores_numbers_text_bool_date_and_formula_cache(tmp_path: Path) -> None:
    xls_path = tmp_path / "book.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Inputs")
    sheet.write(0, 0, "Объем закупок полувагонов")
    sheet.write(0, 1, _PRECISE)
    sheet.write(0, 2, True)
    style = xlwt.XFStyle()
    style.num_format_str = "DD.MM.YYYY"
    sheet.write(0, 3, datetime.date(2020, 5, 17), style)
    sheet.write(0, 4, xlwt.Formula("1+1"))
    book.save(str(xls_path))
    serial = _date_serial(xls_path)

    xlsx_path = build_xlsx(
        tmp_path / "book.xlsx",
        sheets=[
            SheetSpec(
                name="Inputs",
                cells=[
                    CellSpec(addr="A1", value="rounded label"),
                    CellSpec(addr="B1", value="2035"),
                    CellSpec(addr="C1", value="0", type="b"),
                    CellSpec(addr="D1", value="1"),
                    CellSpec(addr="E1", value="999", formula="1+1"),
                ],
            )
        ],
        shared_strings=["rounded label"],
    )
    overlay_xls_cache(xls_path, xlsx_path)
    cached = _cells(xlsx_path)
    assert cached[("Inputs", "A1")] == "Объем закупок полувагонов"
    assert float(cached[("Inputs", "B1")] or "nan") == _PRECISE
    assert cached[("Inputs", "C1")] == "1"
    assert float(cached[("Inputs", "D1")] or "nan") == serial
    # xlwt stores the formula with an empty cache. The invented xlsx value is removed.
    assert cached[("Inputs", "E1")] is None
    assert _formulas(xlsx_path)[("Inputs", "E1")] == "1+1"


def test_overlay_restores_an_error_and_a_formula_number(tmp_path: Path, monkeypatch) -> None:
    xls_path = tmp_path / "book.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Calc")
    # BIFF code 15 is #VALUE!. xlwt's string '#VALUE!' maps to the wrong code.
    sheet.row(0).set_cell_error(0, 15)
    book.save(str(xls_path))
    xlsx_path = build_xlsx(
        tmp_path / "errors.xlsx",
        sheets=[SheetSpec(name="Calc", cells=[CellSpec(addr="A1", value="0")])],
    )
    overlay_xls_cache(xls_path, xlsx_path)
    assert _cells(xlsx_path)[("Calc", "A1")] == "#VALUE!"

    class _Cell:
        ctype = xlrd.XL_CELL_NUMBER
        value = _PRECISE

    class _Sheet:
        name = "Calc"
        nrows = 1
        ncols = 1

        def cell(self, row: int, col: int) -> _Cell:
            del row, col
            return _Cell()

    class _Book:
        nsheets = 1
        datemode = 0

        def sheet_by_index(self, index: int) -> _Sheet:
            del index
            return _Sheet()

    monkeypatch.setattr(
        "finance_context.excel.xls_cache.xlrd.open_workbook",
        lambda **_kwargs: _Book(),
    )
    formula_book = tmp_path / "formula.xls"
    formula_book.write_bytes(b"xls")
    formula_xlsx = build_xlsx(
        tmp_path / "formula.xlsx",
        sheets=[
            SheetSpec(
                name="Calc",
                cells=[CellSpec(addr="A1", value="999", formula="1+1")],
            )
        ],
    )
    overlay_xls_cache(formula_book, formula_xlsx)
    assert float(_cells(formula_xlsx)[("Calc", "A1")] or "nan") == _PRECISE
    assert _formulas(formula_xlsx)[("Calc", "A1")] == "1+1"


def test_date_serial_follows_the_xlsx_system(tmp_path: Path) -> None:
    day = datetime.date(2020, 5, 17)
    xls_path = tmp_path / "book.xls"
    book = xlwt.Workbook()
    book.dates_1904 = 1
    sheet = book.add_sheet("Dates")
    style = xlwt.XFStyle()
    style.num_format_str = "DD.MM.YYYY"
    sheet.write(0, 0, day, style)
    book.save(str(xls_path))
    source = xlrd.open_workbook(file_contents=xls_path.read_bytes())
    serial_1904 = float(source.sheet_by_index(0).cell(0, 0).value)
    xlsx_path = build_xlsx(
        tmp_path / "book.xlsx",
        sheets=[SheetSpec(name="Dates", cells=[CellSpec(addr="A1", value="0")])],
    )
    overlay_xls_cache(xls_path, xlsx_path)
    written = float(_cells(xlsx_path)[("Dates", "A1")] or "nan")
    assert written == (day - datetime.date(1899, 12, 30)).days
    assert written != serial_1904


def test_sheets_match_by_name_not_order(tmp_path: Path) -> None:
    xls_path = tmp_path / "book.xls"
    book = xlwt.Workbook()
    first = book.add_sheet("Beta")
    first.write(0, 0, 2)
    second = book.add_sheet("Alpha")
    second.write(0, 0, 1)
    book.save(str(xls_path))
    xlsx_path = build_xlsx(
        tmp_path / "book.xlsx",
        sheets=[
            SheetSpec(name="Alpha", cells=[CellSpec(addr="A1", value="0")]),
            SheetSpec(name="Beta", cells=[CellSpec(addr="A1", value="0")]),
        ],
    )
    overlay_xls_cache(xls_path, xlsx_path)
    cached = _cells(xlsx_path)
    assert cached[("Alpha", "A1")] == "1"
    assert cached[("Beta", "A1")] == "2"


def test_dimension_mismatch_warns_and_overlays_the_overlap(
    tmp_path: Path, caplog
) -> None:
    xls_path = tmp_path / "book.xls"
    book = xlwt.Workbook()
    sheet = book.add_sheet("Data")
    sheet.write(0, 0, 7)
    sheet.write(1, 0, 8)
    book.save(str(xls_path))
    xlsx_path = build_xlsx(
        tmp_path / "book.xlsx",
        sheets=[SheetSpec(name="Data", cells=[CellSpec(addr="A1", value="0")])],
    )
    with caplog.at_level(logging.WARNING):
        overlay_xls_cache(xls_path, xlsx_path)
    assert _cells(xlsx_path)[("Data", "A1")] == "7"
    assert "dimensions" in caplog.text
    assert "missing" in caplog.text


def test_unreadable_xls_is_left_unchanged(tmp_path: Path, caplog) -> None:
    xls_path = write_ole2(tmp_path / "stub.xls", XLS_STREAMS)
    xlsx_path = build_xlsx(
        tmp_path / "book.xlsx",
        sheets=[SheetSpec(name="P&L", cells=[CellSpec(addr="A1", value="1")])],
    )
    before = xlsx_path.read_bytes()
    with caplog.at_level(logging.WARNING):
        overlay_xls_cache(xls_path, xlsx_path)
    assert xlsx_path.read_bytes() == before
    assert "skipped" in caplog.text


def test_ensure_xlsx_overlays_the_xls_cache(tmp_path: Path) -> None:
    book = xlwt.Workbook()
    sheet = book.add_sheet("P&L")
    sheet.write(0, 0, _PRECISE)
    book.save(str(tmp_path / "source.xls"))
    prepared = build_xlsx(
        tmp_path / "prepared.xlsx",
        sheets=[SheetSpec(name="P&L", cells=[CellSpec(addr="A1", value="2035")])],
    )
    target = ensure_xlsx(tmp_path, FakeConverter(prepared))
    assert float(_cells(target)[("P&L", "A1")] or "nan") == _PRECISE
    assert prepared.read_bytes() != target.read_bytes()


def _date_serial(path: Path) -> float:
    book = xlrd.open_workbook(file_contents=path.read_bytes())
    cell = book.sheet_by_index(0).cell(0, 3)
    assert cell.ctype == xlrd.XL_CELL_DATE
    return float(cell.value)
