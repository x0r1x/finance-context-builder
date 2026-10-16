from __future__ import annotations

import pytest

from finance_context.excel.a1 import shift_formula


@pytest.mark.parametrize(
    ("formula", "shifted"),
    [
        ("LOG10(A1)", "LOG10(B2)"),
        ("_xlfn.LOG10(A1)", "_xlfn.LOG10(B2)"),
        ("ATAN2(A1,B1)", "ATAN2(B2,C2)"),
        ("DEC2HEX(A1)", "DEC2HEX(B2)"),
        ("Table1[Col]+A1", "Table1[Col]+B2"),
        ("Rate1*A1", "Rate1*B2"),
        ("Rate_A1+A1", "Rate_A1+B2"),
        ("1E5*A1", "1E5*B2"),
        ("XFE1+A1", "XFE1+B2"),
    ],
)
def test_shift_moves_cells_and_keeps_names_that_look_like_cells(
    formula: str, shifted: str
) -> None:
    assert shift_formula(formula, 2, 2, 3, 3) == shifted


@pytest.mark.parametrize(
    ("formula", "shifted"),
    [
        ("$A$1+A1", "$A$1+B2"),
        ("A$1+$A1", "B$1+$A2"),
        ("SUM(A1:B2)", "SUM(B2:C3)"),
        ("Лист1!A1+'My Sheet'!B2", "Лист1!B2+'My Sheet'!C3"),
        ("Q1_план!A1", "Q1_план!B2"),
        ("[1]Sheet!A1", "[1]Sheet!B2"),
        ('IF(A1>0,"A1",B1)', 'IF(B2>0,"A1",C2)'),
    ],
)
def test_shift_keeps_moving_real_references(formula: str, shifted: str) -> None:
    assert shift_formula(formula, 2, 2, 3, 3) == shifted
