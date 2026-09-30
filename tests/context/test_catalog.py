from __future__ import annotations

from tests.helpers.slice_book import MYSTERY, sample_context

from finance_context.context.catalog import build_catalog

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
    assert document.axes[0].periods[0].start_date == "2026-01-01"


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
