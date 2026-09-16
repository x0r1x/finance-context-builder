from __future__ import annotations

from finance_context.mapping.models import RowContext
from finance_context.mapping.rowfacets import infer_row_facets


def test_revenue_earned_is_accrual_not_cash() -> None:
    ctx = RowContext(
        row_key="x",
        sheet="P&L",
        row=2,
        block_id="b",
        label="Other Income",
        parent_label="REVENUE EARNED",
        section_path=["REVENUE EARNED"],
        period_headers=["W1"],
    )
    facets = infer_row_facets(ctx)
    assert facets.basis.value == "accrual"
    assert facets.basis.confident


def test_cash_inflows_are_cash_basis() -> None:
    ctx = RowContext(
        row_key="x",
        sheet="CF",
        row=2,
        block_id="b",
        label="Other Income",
        parent_label="CASH INFLOWS",
        section_path=["CASH INFLOWS"],
        period_headers=["W1"],
    )
    facets = infer_row_facets(ctx)
    assert facets.basis.value == "cash"
    assert facets.direction.value == "inflow"


def test_cfads_under_dscr_heading_is_not_a_covenant_row() -> None:
    ctx = RowContext(
        row_key="x",
        sheet="PF Model",
        row=2,
        block_id="b",
        label="CFADS",
        parent_label="Debt Service Coverage Ratio (DSCR)",
        section_path=["Debt Service Coverage Ratio (DSCR)"],
        period_headers=["2024"],
    )
    facets = infer_row_facets(ctx)
    assert facets.statement.value is None


def test_dscr_label_is_covenant() -> None:
    ctx = RowContext(
        row_key="x",
        sheet="PF Model",
        row=3,
        block_id="b",
        label="Average Debt Service Coverage Ratio (DSCR)",
        parent_label="Debt Service Coverage Ratio (DSCR)",
        section_path=["Debt Service Coverage Ratio (DSCR)"],
        period_headers=["2024"],
    )
    facets = infer_row_facets(ctx)
    assert facets.statement.value == "cov"


def test_dividends_earned_is_not_accrual_basis() -> None:
    ctx = RowContext(
        row_key="x",
        sheet="Ratios",
        row=2,
        block_id="b",
        label="Dividends earned",
        period_headers=["2024"],
    )
    facets = infer_row_facets(ctx)
    assert facets.basis.value != "accrual"
