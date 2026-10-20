"""Duplicate calendar keys stay shared until one axis carries different markers."""

from __future__ import annotations

from tests.layout.test_detect_axes import _c

from finance_context.context.build import build_context
from finance_context.context.observations import build_observations
from finance_context.graph.stage import _period_maps
from finance_context.layout.detect import detect_layout


def _fact_plan_sheet() -> list[dict]:
    return [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2024 факт"),
        _c("P&L", "C1", "2024 план"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "80"),
    ]


def test_duplicate_markers_suffix_the_period_key_with_casefold() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2024 Факт"),
        _c("P&L", "C1", "2024 ПЛАН"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "80"),
    ]
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [header.period_key for header in headers] == ["2024@факт", "2024@план"]
    assert [header.kind for header in headers] == ["period", "period"]


def test_a_marker_without_a_duplicate_keeps_the_calendar_key() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2024 факт"),
        _c("P&L", "C1", "2025 план"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "80"),
    ]
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [header.period_key for header in headers] == ["2024", "2025"]


def test_graph_index_keeps_both_variant_columns() -> None:
    cells = _fact_plan_sheet()
    layout = detect_layout(cells)
    period_by_cell, index = _period_maps(layout)
    assert period_by_cell[("P&L", 2)] == "2024@факт"
    assert period_by_cell[("P&L", 3)] == "2024@план"
    assert index["2024@факт"] != index["2024@план"]


def test_observations_and_block_series_keep_both_variant_columns() -> None:
    cells = _fact_plan_sheet()
    layout = detect_layout(cells)
    doc = build_context(
        job_id="variants",
        workbook_meta={"sheets": [{"name": "P&L"}]},
        cells=cells,
        layout=layout,
    )
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == ["2024@факт", "2024@план"]
    assert "role" not in axis.periods[0].model_dump(mode="json")
    row = next(item for block in doc.blocks for item in block.rows if item.label == "Revenue")
    assert row.series[0].values == ["100", "80"]
    found = build_observations(doc, [], q="Revenue", limit=10)
    assert [(item.period_id, item.value) for item in found.observations] == [
        ("2024@факт", "100"),
        ("2024@план", "80"),
    ]
