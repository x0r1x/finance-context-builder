from __future__ import annotations

from finance_context.context.assemble import _hints_for
from finance_context.layout.models import LayoutRow


def test_percent_unit_is_a_rate() -> None:
    row = LayoutRow(row=8, label="Inflation per year (costs) from beginning of concession")
    hints = _hints_for(row, "%")
    assert hints.unit == "rate"


def test_index_unit_is_a_ratio_and_percent_stays_a_rate() -> None:
    index = LayoutRow(row=172, label="CPI", kind="fact")
    index_hints = _hints_for(index, "Index")
    assert index_hints.unit == "ratio"
    rate = LayoutRow(row=165, label="CPI", kind="fact")
    rate_hints = _hints_for(rate, "%")
    assert rate_hints.unit == "rate"


def test_percent_format_replaces_only_a_money_caption() -> None:
    generation = LayoutRow(row=351, label="Net generation", kind="fact")
    energy = _hints_for(generation, "MWh p.a.", number_formats=["0.00%"])
    assert energy.unit == "count"
    revenue = LayoutRow(row=10, label="Revenue", kind="fact")
    money = _hints_for(revenue, "EUR'000", number_formats=["0.00%"])
    assert money.unit == "rate"


def test_k_pound_in_label_sets_money_gbp() -> None:
    row = LayoutRow(row=10, label="Revenue k£", kind="fact")
    hints = _hints_for(row)
    assert hints.unit == "money"
    assert hints.currency == "GBP"
    assert hints.scale == "k"
