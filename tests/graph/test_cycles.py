from __future__ import annotations

from finance_context.graph.cycles import classify_cycles
from finance_context.graph.stage import attach_cycle_breakers, circularity_hints
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.models import MappedRow, MappingDocument


def test_unexpected_two_cell_cycle() -> None:
    edges = [
        {
            "source": "Sheet!A1",
            "target": "Sheet!B1",
            "kind": "ref",
            "col_offset": 1,
            "period_lag": "same",
        },
        {
            "source": "Sheet!B1",
            "target": "Sheet!A1",
            "kind": "ref",
            "col_offset": -1,
            "period_lag": "same",
        },
    ]
    cycles = classify_cycles(edges)
    assert len(cycles) == 1
    assert cycles[0].class_ == "unexpected"
    assert set(cycles[0].members) == {"Sheet!A1", "Sheet!B1"}


def test_period_roll_forward_is_iterative() -> None:
    edges = [
        {
            "source": "CF!C2",
            "target": "CF!B2",
            "kind": "ref",
            "col_offset": -1,
            "period_lag": "-1",
        },
        {
            "source": "CF!B2",
            "target": "CF!C2",
            "kind": "ref",
            "col_offset": 1,
            "period_lag": "1",
        },
    ]
    cycles = classify_cycles(edges)
    assert len(cycles) == 1
    assert cycles[0].class_ == "iterative_ok"


def test_technical_bridge_cells_are_cycle_breakers() -> None:
    edges = [
        {
            "source": "CF!B5",
            "target": "CF!B6",
            "kind": "ref",
            "col_offset": 0,
            "period_lag": "same",
        },
        {
            "source": "CF!B6",
            "target": "CF!B5",
            "kind": "ref",
            "col_offset": 0,
            "period_lag": "same",
        },
    ]
    mapping = MappingDocument(
        rows=[
            MappedRow(
                row_key="CF|6|CF!r1",
                sheet="CF",
                row=6,
                block_id="CF!r1",
                label="Uses of funds for circularity breakdown",
                article_role="calculation",
                source="rule",
                disposition="excluded",
                exclusion_reason="technical_bridge",
            )
        ]
    )
    cycles = attach_cycle_breakers(classify_cycles(edges), mapping)
    assert len(cycles) == 1
    assert cycles[0].class_ == "unexpected"
    assert cycles[0].breakers == ["CF!B6"]


def test_circular_label_hint_when_no_scc() -> None:
    mapping = MappingDocument(
        rows=[
            MappedRow(
                row_key="CF|12|CF!r1",
                sheet="CF",
                row=12,
                block_id="CF!r1",
                label="Uses of funds for circularity breakdown",
                article_role="calculation",
                source="rule",
                disposition="excluded",
                exclusion_reason="technical_bridge",
            )
        ]
    )
    layout = Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(
                            id="a",
                            row=1,
                            headers=[AxisHeader(col=2, text="2023", role="forecast", period_key="2023")],
                        ),
                        rows=[
                            LayoutRow(row=12, label="Uses of funds for circularity breakdown"),
                        ],
                    )
                ],
            )
        ]
    )
    index_rows = [("CF!B12", "CF", "B12", "CF|12|CF!r1", None, "2023", "calculated")]
    hints = circularity_hints(mapping, layout, index_rows, cycles=[])
    assert len(hints) == 1
    assert hints[0].sheet == "CF"
    assert hints[0].row == 12
    assert "circular" in hints[0].label.casefold()
    assert hints[0].cell_ids == ["CF!B12"]
    cycle_edges = [
        {
            "source": "CF!B5",
            "target": "CF!B6",
            "kind": "ref",
            "period_lag": "same",
        },
        {
            "source": "CF!B6",
            "target": "CF!B5",
            "kind": "ref",
            "period_lag": "same",
        },
    ]
    assert circularity_hints(mapping, layout, index_rows, classify_cycles(cycle_edges)) == []
