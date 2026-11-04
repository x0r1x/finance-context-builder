"""Neighbor maps stay on the rows context reads. Range nodes stay on every edge."""

from __future__ import annotations

from tests.helpers.published import published_context as build_context

from finance_context.formulas.csr import sheet_usage
from finance_context.formulas.models import Edge
from finance_context.layout.adjacency import RangeReads, row_adjacency
from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
)


def _usage() -> object:
    return sheet_usage(["CF!A1", "CF!A2", "CF!B2", "CF!B3", "CF!B100", "Data!C1"])


def _edges() -> list[Edge]:
    return [
        Edge(kind="ref", source="CF!B2", target="CF!B3"),
        Edge(kind="range", source="CF!B2", target="CF!B3:B5"),
        Edge(kind="ref", source="CF!B100", target="CF!B2"),
        Edge(kind="range", source="CF!B100", target="CF!A:A"),
        Edge(kind="range", source="CF!B2", target="Data!C:C"),
        Edge(kind="ref", source="CF!B50", target="CF!B51"),
    ]


def test_neighbor_maps_keep_only_consumed_rows() -> None:
    usage = _usage()
    edges = _edges()
    full_reads = RangeReads()
    kept_reads = RangeReads()
    full_pre, full_dep = row_adjacency(edges, limit=0, usage=usage, ranges=full_reads)
    kept_pre, kept_dep = row_adjacency(
        edges,
        limit=0,
        usage=usage,
        ranges=kept_reads,
        rows={("CF", 2)},
    )
    assert kept_pre == {("CF", 2): full_pre[("CF", 2)]}
    assert kept_dep == {("CF", 2): full_dep[("CF", 2)]}
    assert kept_pre[("CF", 2)] == ["CF!3", "CF!4", "CF!5"]
    assert kept_dep[("CF", 2)] == ["CF!100"]
    assert ("CF", 100) in full_pre
    assert ("CF", 51) in full_dep
    assert kept_reads.records(usage) == full_reads.records(usage)
    assert {item["id"]: item["readers"] for item in kept_reads.records(usage)} == {
        "CF!A:A": ["CF!100"],
        "Data!C:C": ["CF!2"],
    }


def test_no_consumed_rows_still_records_every_range() -> None:
    usage = _usage()
    reads = RangeReads()
    precedents, dependents = row_adjacency(
        _edges(), limit=0, usage=usage, ranges=reads, rows=set()
    )
    assert precedents == {}
    assert dependents == {}
    assert [item["id"] for item in reads.records(usage)] == ["CF!A:A", "Data!C:C"]
    assert reads.records(usage)[0]["readers"] == ["CF!100"]


def test_context_publishes_outside_range_readers_and_layout_neighbors() -> None:
    layout = Layout(
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
                                AxisHeader(col=2, text="2023", kind="period", period_key="2023")
                            ],
                        ),
                        rows=[LayoutRow(row=2, label="Opening cash")],
                    )
                ],
            )
        ]
    )
    doc = build_context(
        job_id="adj",
        workbook_meta={"sheets": [{"name": "CF"}, {"name": "Data"}]},
        cells=[
            {
                "sheet": "CF",
                "row": 2,
                "col": 2,
                "addr": "B2",
                "formula_raw": "=B3",
                "cached_value": "1",
            }
        ],
        layout=layout,
        edges=_edges(),
        usage=_usage(),
    )
    assert doc.blocks[0].rows[0].neighbors == ["CF!3", "CF!4", "CF!5", "CF!100"]
    by_id = {item.id: item for item in doc.ranges}
    assert by_id["CF!A:A"].readers == ["CF!100"]
    assert by_id["Data!C:C"].readers == ["CF!2"]
