from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


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


def _typed_table(name_header: str) -> list[dict]:
    """A type column sits left of the row name. Both cells are filled."""
    cells = [
        _c("Out", "A1", "Тип показателя"),
        _c("Out", "B1", name_header),
        _c("Out", "C1", "2016"),
        _c("Out", "D1", "2017"),
    ]
    for row, (kind, name, left, right) in enumerate(
        [
            ("Общий (не УФН)", "CFADS", "10", "11"),
            ("Коэффициент", "DSCR", "1.4", "1.5"),
            ("Расчетный показатель", "FCF", "3", "4"),
            ("Общий (не УФН)", "NCF", "5", "6"),
            ("Общий (не УФН)", "Debt Service", "7", "8"),
        ],
        start=2,
    ):
        cells += [
            _c("Out", f"A{row}", kind),
            _c("Out", f"B{row}", name),
            _c("Out", f"C{row}", left),
            _c("Out", f"D{row}", right),
        ]
    return cells


def test_name_header_is_the_row_label_not_the_type_column() -> None:
    block = detect_layout(_typed_table("Наименование")).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "CFADS",
        "DSCR",
        "FCF",
        "NCF",
        "Debt Service",
    ]
    assert block.label_col == 2
    assert {row.label_col for row in block.rows} == {2}


def test_two_text_columns_without_a_name_header_keep_the_left_label() -> None:
    block = detect_layout(_typed_table("Статья")).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "Общий (не УФН)",
        "Коэффициент",
        "Расчетный показатель",
        "Общий (не УФН)",
        "Общий (не УФН)",
    ]
    assert block.label_col == 1


def test_profile_name_header_selects_that_column() -> None:
    block = detect_layout(
        _typed_table("Статья"),
        name_headers=("статья",),
    ).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "CFADS",
        "DSCR",
        "FCF",
        "NCF",
        "Debt Service",
    ]
    assert block.label_col == 2
