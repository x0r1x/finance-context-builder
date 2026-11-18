"""A row in two horizontal blocks is keyed by the column that owns the cell."""

from __future__ import annotations

from finance_context.graph.stage import _at_cell, _row_meta, _side_cells
from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    RowCell,
    SheetLayout,
)


def _shared_layout() -> Layout:
    timeline = Block(
        block_id="CF!r1",
        label_col=1,
        kind="timeline",
        axis=Axis(
            id="CF!r1",
            row=1,
            headers=[AxisHeader(col=4, text="2024", kind="period", period_key="2024")],
        ),
        rows=[
            LayoutRow(
                row=2,
                label="Revenue",
                cells=[RowCell(col=3, role="total")],
            )
        ],
    )
    side = Block(
        block_id="CF!c2l6",
        label_col=6,
        kind="key_value",
        axis=Axis(
            id="CF!c2l6",
            row=1,
            headers=[AxisHeader(col=7, text="Amount", role="value", period_key="c7")],
        ),
        rows=[LayoutRow(row=2, label="Fee")],
    )
    return Layout(sheets=[SheetLayout(name="CF", blocks=[timeline, side])])


def _same_columns() -> Layout:
    def block(block_id: str, label: str) -> Block:
        return Block(
            block_id=block_id,
            label_col=1,
            kind="timeline",
            axis=Axis(
                id=block_id,
                row=1,
                headers=[AxisHeader(col=4, text="2024", kind="period", period_key="2024")],
            ),
            rows=[LayoutRow(row=2, label=label)],
        )

    return Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[block("CF!r1", "Left"), block("CF!r9", "Right")],
            )
        ]
    )


def test_stub_column_on_a_shared_row_keeps_the_timeline_key() -> None:
    layout = _shared_layout()
    side = _side_cells(layout)
    meta = _row_meta(layout, side)
    assert ("CF", 2) not in meta
    assert _at_cell({}, meta, {}, "CF", 2, 3, side)[0] == "CF|2|c1|CF!r1"
    assert _at_cell({}, meta, {}, "CF", 2, 4, side)[0] == "CF|2|c1|CF!r1"
    assert _at_cell({}, meta, {}, "CF", 2, 7, side)[0] == "CF|2|c6|CF!c2l6"


def test_a_fully_conflicting_shared_row_is_not_given_to_the_last_block() -> None:
    layout = _same_columns()
    side = _side_cells(layout)
    meta = _row_meta(layout, side)
    assert ("CF", 2) not in meta
    assert _at_cell({}, meta, {}, "CF", 2, 4, side)[0] is None
    assert _at_cell({}, meta, {}, "CF", 2, 1, side)[0] is None
