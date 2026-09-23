from __future__ import annotations

from finance_context.context.build import (
    _escalation_hint,
    _hint_blob_and_tokens,
    _hints_for,
    _segment_hint,
)
from finance_context.layout.models import LayoutRow
from finance_context.mapping.models import MappedRow


def test_cost_inflation_in_parentheses_is_cost_escalation() -> None:
    blob, tokens = _hint_blob_and_tokens(
        "Inflation per year (costs) from beginning of concession"
    )
    assert "costs" in tokens
    assert _escalation_hint(blob, tokens) == "cost"
    row = LayoutRow(row=8, label="Inflation per year (costs) from beginning of concession")
    mapped = MappedRow(
        row_key="IA|8|b",
        sheet="Input Assumptions",
        row=8,
        block_id="b",
        label=row.label,
        concept_id="ops.inflation_cost",
        article_role="assumption",
        source="rule",
    )
    hints = _hints_for(mapped, row, "%", sheet="Input Assumptions", parent="COSTS DURING OPERATION")
    assert hints.escalation == "cost"
    assert hints.time_semantics == "rate"


def test_revenue_inflation_defaults_to_revenue_escalation() -> None:
    blob, tokens = _hint_blob_and_tokens("Inflation per year")
    assert _escalation_hint(blob, tokens) == "revenue"


def test_heavy_maintenance_is_not_hv_segment() -> None:
    blob, tokens = _hint_blob_and_tokens(
        "Maintenance (including heavy maintenance & SPV costs)"
    )
    assert _segment_hint(blob, tokens) is None
    row = LayoutRow(
        row=13,
        label="Maintenance (including heavy maintenance & SPV costs)",
        kind="fact",
    )
    mapped = MappedRow(
        row_key="IA|13|b",
        sheet="Input Assumptions",
        row=13,
        block_id="b",
        label=row.label,
        concept_id="pnl.opex",
        article_role="assumption",
        source="rule",
    )
    hints = _hints_for(mapped, row, sheet="Input Assumptions", parent="COSTS DURING OPERATION")
    assert hints.segment is None


def test_heavy_vehicle_traffic_is_hv_segment() -> None:
    blob, tokens = _hint_blob_and_tokens("TRAFFIC - Heavy Vehicle (HV)")
    assert _segment_hint(blob, tokens) == "hv"


def test_balance_sheet_line_is_stock_not_flow() -> None:
    row = LayoutRow(row=8, label="Cash in hand", kind="fact", section_path=["Balance Sheet"])
    mapped = MappedRow(
        row_key="BS|8|b",
        sheet="Balance Sheet",
        row=8,
        block_id="b",
        label=row.label,
        concept_id="bs.cash",
        article_role="database_like",
        source="rule",
    )
    hints = _hints_for(mapped, row, sheet="Balance Sheet", parent="Balance Sheet")
    assert hints.nature == "balance"
    assert hints.time_semantics == "stock"
    assert hints.statement == "bs"
    assert hints.sign == "stock"


def test_opening_balance_stays_bop() -> None:
    row = LayoutRow(row=2, label="Opening cash", kind="fact")
    mapped = MappedRow(
        row_key="CF|2|b",
        sheet="CF",
        row=2,
        block_id="b",
        label=row.label,
        concept_id="bs.cash",
        article_role="database_like",
        source="rule",
    )
    hints = _hints_for(mapped, row, sheet="CF")
    assert hints.time_semantics == "bop"
    assert hints.sign == "stock"


def test_cpi_index_is_a_level_and_the_rate_stays_a_rate() -> None:
    index = LayoutRow(row=172, label="CPI", kind="fact")
    mapped_index = MappedRow(
        row_key="PF|172|b",
        sheet="PF Model",
        row=172,
        block_id="b",
        label="CPI",
        concept_id="ops.cpi",
        article_role="assumption",
        source="rule",
    )
    index_hints = _hints_for(mapped_index, index, "Index", sheet="PF Model")
    assert index_hints.time_semantics == "stock"
    rate = LayoutRow(row=165, label="CPI", kind="fact")
    mapped_rate = MappedRow(
        row_key="PF|165|b",
        sheet="PF Model",
        row=165,
        block_id="b",
        label="CPI",
        concept_id="ops.inflation",
        article_role="assumption",
        source="rule",
    )
    rate_hints = _hints_for(mapped_rate, rate, "%", sheet="PF Model")
    assert rate_hints.time_semantics == "rate"
    assert rate_hints.unit == "rate"


def test_k_pound_in_label_sets_money_gbp() -> None:
    row = LayoutRow(row=10, label="Revenue k£", kind="fact")
    mapped = MappedRow(
        row_key="P&L|10|b",
        sheet="P&L",
        row=10,
        block_id="b",
        label=row.label,
        concept_id="pnl.revenue",
        article_role="database_like",
        source="rule",
    )
    hints = _hints_for(mapped, row, sheet="P&L")
    assert hints.unit == "money"
    assert hints.currency == "GBP"
    assert hints.scale == "k"
    assert hints.sign == "inflow"
