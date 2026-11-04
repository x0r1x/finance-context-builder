"""Each row label is parsed once, including when a caption and the label disagree."""

from __future__ import annotations

from tests.helpers.published import attach_streamed_rows

from finance_context.context import assemble
from finance_context.context.build import build_context
from finance_context.context.measure import Measure
from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


def _cell(addr: str, value: str, fmt: str | None = None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": "Debt",
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "unparsed": False,
        "number_format": fmt,
        "comment": None,
    }


def test_each_row_label_is_parsed_once(monkeypatch) -> None:
    seen: list[str] = []
    original = assemble._parse_text

    def spy(text: str) -> Measure:
        seen.append(text)
        return original(text)

    monkeypatch.setattr(assemble, "_parse_text", spy)
    cells = [
        _cell("A1", "Item"),
        _cell("B1", "Unit"),
        _cell("C1", "2020"),
        _cell("D1", "2021"),
        _cell("A2", "EUR'000"),
        _cell("A3", "Inflation per year"),
        _cell("C3", "0.05"),
        _cell("D3", "0.06"),
        _cell("A4", "Revenue"),
        _cell("B4", "k£"),
        _cell("C4", "10"),
        _cell("D4", "11"),
        _cell("A5", "Price"),
        _cell("B5", "EUR/MWh"),
        _cell("C5", "4"),
        _cell("D5", "5"),
        _cell("A6", "Count"),
        _cell("B6", "MWh p.a."),
        _cell("C6", "1"),
        _cell("D6", "2"),
        _cell("A7", "Margin"),
        _cell("C7", "0.02", "0.00%"),
        _cell("D7", "0.03", "0.00%"),
    ]
    layout = detect_layout(cells)
    doc = attach_streamed_rows(
        build_context(
            job_id="once",
            workbook_meta={"sheets": []},
            cells=cells,
            layout=layout,
        )
    )
    rows = [row for block in doc.blocks for row in block.rows]
    assert seen == [row.label for row in rows]
    by_label = {row.label: row for row in rows}
    assert (
        by_label["EUR'000"].hints.unit,
        by_label["EUR'000"].hints.currency,
        by_label["EUR'000"].hints.scale,
        by_label["EUR'000"].scale_factor,
    ) == ("money", "EUR", "k", 1000)
    inflation = by_label["Inflation per year"]
    assert (inflation.hints.unit, inflation.hints.currency, inflation.hints.scale) == (
        "rate",
        "EUR",
        "k",
    )
    assert inflation.scale_factor == 1000
    assert inflation.normalized_values == ["50", "60"]
    revenue = by_label["Revenue"]
    assert (revenue.hints.unit, revenue.hints.currency, revenue.hints.scale) == (
        "money",
        "GBP",
        "k",
    )
    assert revenue.normalized_values == ["10000", "11000"]
    price = by_label["Price"]
    assert (price.hints.unit, price.hints.currency, price.hints.scale, price.hints.unit_per) == (
        "price",
        "EUR",
        "unit",
        "MWh",
    )
    assert price.scale_factor == 1
    assert by_label["Count"].hints.unit == "count"
    margin = by_label["Margin"]
    assert (margin.hints.unit, margin.hints.currency, margin.hints.scale) == (
        "rate",
        None,
        None,
    )
    assert margin.scale_factor is None
    assert margin.normalized_values == ["0.02", "0.03"]
