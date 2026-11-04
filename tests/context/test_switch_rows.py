"""Switch detection still sees every INDEX, CHOOSE, and OFFSET row.

Layout rows alone are not enough: a constant becomes a switch when two other
rows pass it to INDEX, CHOOSE, or OFFSET, and those rows need not be in the
layout. SUM and plain references do not, so they are not kept in the table.
"""

from __future__ import annotations

from pathlib import Path

from finance_context.context.build import _switch_rows
from finance_context.layout.params import selector_rows
from finance_context.store.fs import write_parquet

_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("row", "INTEGER"),
    ("col", "INTEGER"),
    ("addr", "VARCHAR"),
    ("formula_raw", "VARCHAR"),
)


def _cell(row: int, col: int, addr: str, formula: str | None) -> tuple:
    return ("PF", row, col, addr, formula)


def _book(path: Path) -> None:
    rows = [
        _cell(10, 2, "B10", "=INDEX(F10:G10,$D$3)"),
        _cell(11, 2, "B11", "=INDEX(F11:G11,$D$3)"),
        _cell(12, 2, "B12", "=SUM(A1:A11)"),
        _cell(13, 2, "B13", "=CHOOSE($D$4,1,2)"),
        _cell(14, 2, "B14", "=CHOOSE($D$4,3,4)"),
        _cell(15, 2, "B15", "=OFFSET($A$1,$D$5,0)"),
        _cell(16, 2, "B16", "=OFFSET($A$1,$D$5,1)"),
        _cell(20, 2, "B20", "=INDEX(F20:G20,Pick)"),
        _cell(21, 2, "B21", "=INDEX(F21:G21,Pick)"),
        _cell(40, 3, "C40", "=SUM(A40)"),
        _cell(41, 2, "B41", "=INDEX(F41:G41,$C$40)"),
        _cell(42, 2, "B42", "=INDEX(F42:G42,$C$40)"),
        _cell(3, 4, "D3", None),
        _cell(4, 4, "D4", None),
        _cell(5, 4, "D5", None),
        _cell(6, 4, "D6", None),
        _cell(50, 1, "A50", "=B49+B48-B47"),
    ]
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)


def _every_formula(path_rows: list[tuple]) -> dict[int, list[dict]]:
    by_row: dict[int, list[dict]] = {}
    for sheet, row, col, addr, formula in path_rows:
        by_row.setdefault(row, []).append(
            {
                "sheet": sheet,
                "row": row,
                "col": col,
                "addr": addr,
                "formula_raw": formula,
            }
        )
    return by_row


def test_switch_rows_match_a_full_formula_scan(tmp_path: Path) -> None:
    path = tmp_path / "cells.parquet"
    _book(path)
    names = [{"name": "Pick", "formula": "$D$6", "sheet": "PF"}]
    loaded = _switch_rows(path, names)
    found = selector_rows(loaded["PF"], False, defined_names=names, sheet="PF")
    every = _every_formula(
        [
            ("PF", row, col, addr, formula)
            for row, col, addr, formula in (
                (10, 2, "B10", "=INDEX(F10:G10,$D$3)"),
                (11, 2, "B11", "=INDEX(F11:G11,$D$3)"),
                (12, 2, "B12", "=SUM(A1:A11)"),
                (13, 2, "B13", "=CHOOSE($D$4,1,2)"),
                (14, 2, "B14", "=CHOOSE($D$4,3,4)"),
                (15, 2, "B15", "=OFFSET($A$1,$D$5,0)"),
                (16, 2, "B16", "=OFFSET($A$1,$D$5,1)"),
                (20, 2, "B20", "=INDEX(F20:G20,Pick)"),
                (21, 2, "B21", "=INDEX(F21:G21,Pick)"),
                (40, 3, "C40", "=SUM(A40)"),
                (41, 2, "B41", "=INDEX(F41:G41,$C$40)"),
                (42, 2, "B42", "=INDEX(F42:G42,$C$40)"),
                (3, 4, "D3", None),
                (4, 4, "D4", None),
                (5, 4, "D5", None),
                (6, 4, "D6", None),
                (50, 1, "A50", "=B49+B48-B47"),
            )
        ]
    )
    assert found == selector_rows(every, False, defined_names=names, sheet="PF")
    assert found == {3, 4, 5, 6}
    stored = {
        row
        for row, cells in loaded["PF"].items()
        if any(cell.get("formula_raw") for cell in cells)
    }
    assert 12 not in stored
    assert 50 not in stored
    assert 40 in stored
    # The constant's own row has no INDEX formula. Ignoring the other rows
    # drops the switch.
    only_constant = {3: loaded["PF"].get(3, [])}
    assert selector_rows(only_constant, False, defined_names=names, sheet="PF") == set()
