from __future__ import annotations

from finance_context.context.assemble import _hint_blob_and_tokens, _hints_for
from finance_context.layout.models import LayoutRow


def test_percent_unit_is_a_rate() -> None:
    blob, tokens = _hint_blob_and_tokens(
        "Inflation per year (costs) from beginning of concession"
    )
    assert "costs" in tokens
    assert "inflation" in blob
    row = LayoutRow(row=8, label="Inflation per year (costs) from beginning of concession")
    hints = _hints_for(row, "%")
    assert hints.escalation is None
    assert hints.time_semantics == "rate"
    assert hints.unit == "rate"


def test_segment_and_escalation_stay_empty() -> None:
    row = LayoutRow(
        row=13,
        label="TRAFFIC - Heavy Vehicle (HV)",
        kind="fact",
    )
    hints = _hints_for(row)
    assert hints.segment is None
    assert hints.escalation is None
    assert hints.statement is None


def test_sheet_name_does_not_make_a_balance() -> None:
    row = LayoutRow(row=8, label="Cash in hand", kind="fact", section_path=["Balance Sheet"])
    hints = _hints_for(row)
    assert hints.nature is None
    assert hints.time_semantics == "flow"
    assert hints.statement is None


def test_opening_balance_stays_bop() -> None:
    row = LayoutRow(row=2, label="Opening cash", kind="fact")
    hints = _hints_for(row)
    assert hints.time_semantics == "bop"
    assert hints.sign == "stock"


def test_index_unit_is_a_level_and_percent_stays_a_rate() -> None:
    index = LayoutRow(row=172, label="CPI", kind="fact")
    index_hints = _hints_for(index, "Index")
    assert index_hints.time_semantics == "stock"
    assert index_hints.statement is None
    rate = LayoutRow(row=165, label="CPI", kind="fact")
    rate_hints = _hints_for(rate, "%")
    assert rate_hints.time_semantics == "rate"
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
    assert hints.sign == "inflow"
