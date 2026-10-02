from __future__ import annotations

from tests.helpers.slice_book import DISCOUNT, MYSTERY, OTHER, REVENUE, sample_context

from finance_context.context.catalog import build_catalog
from finance_context.models.context import ContextPeriod

_FORBIDDEN = {
    "values",
    "series",
    "normalized_values",
    "normalized_value",
    "formula_class",
    "mapping_quality",
}


def _keys(value: object):
    if isinstance(value, dict):
        yield from value
        for item in value.values():
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_catalog_has_no_value_arrays() -> None:
    document = build_catalog(sample_context())
    payload = document.model_dump(mode="json")
    assert _FORBIDDEN.isdisjoint(_keys(payload))
    assert document.schema_version == "catalog-1"
    assert document.total == 7
    assert document.limit is None
    assert document.mapping_stats.abstained == 1
    assert document.mapping_stats.inventory_rows == 7
    revenue = next(row for row in document.rows if row.label == "Revenue")
    assert revenue.has_formula is True
    assert revenue.concept_id == "pnl.revenue"
    assert revenue.axis_ids == ["Operation!r8"]
    blank = next(row for row in document.rows if row.label == "Blank")
    assert blank.has_formula is False
    assert document.axes[0].periods[0].period_key == "Y5"
    assert document.axes[0].periods[0].phase == "operation"
    assert document.axes[0].periods[0].phase_year == 1
    assert document.axes[0].periods[0].start_date == "2026-01-01"
    year = document.model_dump(mode="json")["axes"][0]["periods"][0]
    assert year["phase_year"] == 1
    assert "flags" not in year
    assert "group_key" not in year


def test_catalog_period_clocks_omit_empty_keys() -> None:
    context = sample_context()
    context.axes[0].periods = [
        ContextPeriod(
            col=3,
            period_key="2020-01",
            phase="operation",
            phase_year=1,
            group_key="2020",
            flags={"repayment": True, "availability": False},
        ),
        ContextPeriod(col=4, period_key="undated", phase_year=9),
        ContextPeriod(col=5, period_key="2020-02", phase="operation", phase_year=1, flags={}),
    ]
    document = build_catalog(context)
    periods = document.model_dump(mode="json")["axes"][0]["periods"]
    assert periods[0] == {
        "period_key": "2020-01",
        "phase": "operation",
        "phase_year": 1,
        "group_key": "2020",
        "flags": {"repayment": True, "availability": False},
    }
    assert periods[1] == {"period_key": "undated"}
    assert "phase" not in periods[1]
    assert "phase_year" not in periods[1]
    assert periods[2]["phase_year"] == 1
    assert "flags" not in periods[2]
    assert "group_key" not in periods[2]
    payload = document.model_dump(mode="json")
    assert _FORBIDDEN.isdisjoint(_keys(payload))


def test_catalog_finds_abstained_by_label_not_by_concept() -> None:
    context = sample_context()
    found = build_catalog(context, q="MYSTERY")
    assert found.total == 1
    assert found.rows[0].row_key == MYSTERY
    assert found.rows[0].concept_id is None
    assert found.axes
    missed = build_catalog(context, concept_ids=["pnl.nope"])
    assert missed.total == 0
    assert missed.rows == []
    assert missed.axes


def _row(context, row_key: str):
    for block in context.blocks:
        for row in block.rows:
            if row.row_key == row_key:
                return row
    raise AssertionError(row_key)


def test_query_reads_the_own_label_and_ignores_case() -> None:
    context = sample_context()
    assert [row.row_key for row in build_catalog(context, q="revenue").rows] == [REVENUE]
    assert [row.row_key for row in build_catalog(context, q="Rev").rows] == [REVENUE]


def test_query_does_not_read_the_path() -> None:
    context = sample_context()
    _row(context, OTHER).label_path = ["Operation", "Revenue", "Other income"]
    found = build_catalog(context, q="Revenue")
    assert [row.row_key for row in found.rows] == [REVENUE]


def test_query_irr_skips_a_path_only_row_and_keeps_an_empty_concept() -> None:
    context = sample_context()
    capex = _row(context, OTHER)
    capex.label = "CAPEX"
    capex.label_path = ["Project IRR"]
    capex.concept_id = "val.irr"
    project = _row(context, REVENUE)
    project.label = "Project IRR"
    project.concept_id = None
    found = build_catalog(context, q="IRR")
    assert [row.row_key for row in found.rows] == [REVENUE]
    assert found.rows[0].concept_id is None


def test_concept_id_narrows_the_label_query() -> None:
    found = build_catalog(sample_context(), q="income", concept_ids=["pnl.other"])
    assert [row.row_key for row in found.rows] == [OTHER]
    missed = build_catalog(sample_context(), q="income", concept_ids=["pnl.revenue"])
    assert missed.rows == []
    assert missed.axes


def test_unknown_query_keeps_the_axes() -> None:
    missed = build_catalog(sample_context(), q="No such line")
    assert missed.total == 0
    assert missed.rows == []
    assert missed.axes


def test_same_label_on_two_sheets_returns_both() -> None:
    context = sample_context()
    _row(context, DISCOUNT).label = "Revenue"
    both = build_catalog(context, q="Revenue")
    assert {row.sheet for row in both.rows} == {"Inputs", "Operation"}
    one = build_catalog(context, q="Revenue", sheet="Operation")
    assert [row.row_key for row in one.rows] == [REVENUE]


def test_blank_query_adds_no_constraint() -> None:
    context = sample_context()
    assert build_catalog(context, q="   ").total == build_catalog(context).total


def test_catalog_pages_after_the_filter() -> None:
    context = sample_context()
    page = build_catalog(context, sheet="Operation", limit=2, offset=1)
    assert page.total == 6
    assert len(page.rows) == 2
    assert page.offset == 1
    assert [row.label for row in page.rows] == ["Other income", "Total"]
    past = build_catalog(context, offset=100)
    assert past.total == 7
    assert past.rows == []
    assert len(past.axes) == len(context.axes)
