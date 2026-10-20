from __future__ import annotations

from finance_context.layout.formulas_shape import _range_rows
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.layout.relations import formula_relations


def _layout() -> Layout:
    return Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(
                            id="CF!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=4,
                                    text="2024",
                                    kind="period",
                                    period_key="2024",
                                )
                            ],
                        ),
                        rows=[
                            LayoutRow(row=2, label="Revenue"),
                            LayoutRow(row=3, label="Costs"),
                            LayoutRow(row=4, label="Other"),
                            LayoutRow(row=10, label="Total"),
                        ],
                    )
                ],
            )
        ]
    )


def _cell(formula: str) -> dict:
    return {
        "sheet": "CF",
        "row": 10,
        "col": 4,
        "addr": "D10",
        "formula_raw": formula,
    }


def test_whole_column_sum_adds_no_aggregate_relation() -> None:
    relations = formula_relations(_layout(), [_cell("=SUM(D:D)")])
    assert relations == []


def test_full_sheet_column_span_adds_no_aggregate_relation() -> None:
    relations = formula_relations(_layout(), [_cell("=SUM($D$1:$D$1048576)")])
    assert relations == []


def test_near_full_column_range_stays_a_span() -> None:
    span = _range_rows(
        {
            "op": "range",
            "start": {"col": 4, "row": 2},
            "end": {"col": 4, "row": 1_048_576},
        },
        4,
    )
    assert span == (2, 1_048_576)


def test_near_full_column_sum_uses_layout_rows_only() -> None:
    relations = formula_relations(_layout(), [_cell("=SUM(D2:D1048576)")])
    assert len(relations) == 1
    assert relations[0].kind == "aggregate"
    assert relations[0].member_row_keys == [
        "CF|2|CF!r1",
        "CF|3|CF!r1",
        "CF|4|CF!r1",
        "CF|10|CF!r1",
    ]


def test_bounded_column_sum_keeps_member_rows() -> None:
    relations = formula_relations(_layout(), [_cell("=SUM(D2:D4)")])
    assert len(relations) == 1
    assert relations[0].kind == "aggregate"
    assert relations[0].member_row_keys == [
        "CF|2|CF!r1",
        "CF|3|CF!r1",
        "CF|4|CF!r1",
    ]


def _two_sheet_layout() -> Layout:
    def block(sheet: str, rows: list[LayoutRow]) -> Block:
        return Block(
            block_id=f"{sheet}!r1",
            label_col=1,
            axis=Axis(
                id=f"{sheet}!r1",
                row=1,
                headers=[
                    AxisHeader(col=4, text="2024", kind="period", period_key="2024"),
                    AxisHeader(col=5, text="2025", kind="period", period_key="2025"),
                ],
            ),
            rows=rows,
        )

    return Layout(
        sheets=[
            SheetLayout(
                name="Ops",
                blocks=[block("Ops", [LayoutRow(row=14, label="Total Revenue")])],
            ),
            SheetLayout(
                name="CF",
                blocks=[
                    block(
                        "CF",
                        [
                            LayoutRow(row=8, label="Gross Revenues"),
                            LayoutRow(row=14, label="CFADS"),
                        ],
                    )
                ],
            ),
        ]
    )


def _cf_cell(row: int, col: int, formula: str) -> dict:
    letter = {4: "D", 5: "E"}[col]
    return {
        "sheet": "CF",
        "row": row,
        "col": col,
        "addr": f"{letter}{row}",
        "formula_raw": formula,
    }


def test_previous_column_on_another_sheet_is_not_a_roll_forward() -> None:
    cells = [_cf_cell(8, 4, "=Ops!C14"), _cf_cell(8, 5, "=Ops!D14")]
    relations = formula_relations(_two_sheet_layout(), cells)
    assert [rel.kind for rel in relations] == ["alias"]
    assert relations[0].source_row_key == "CF|8|CF!r1"
    assert relations[0].target_row_key == "Ops|14|Ops!r1"


def test_previous_column_on_the_same_sheet_stays_a_roll_forward() -> None:
    cells = [_cf_cell(8, 4, "=C14"), _cf_cell(8, 5, "=CF!D14")]
    relations = formula_relations(_two_sheet_layout(), cells)
    assert [rel.kind for rel in relations] == ["roll_forward"]
    assert relations[0].source_row_key == "CF|8|CF!r1"
    assert relations[0].target_row_key == "CF|14|CF!r1"
