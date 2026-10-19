"""Period position and aggregation of a row: what an agent may add across periods."""

from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    RowCell,
    SheetLayout,
)
from finance_context.models.context import BlockRow

_UNIT_COL = 2


def _row(
    label: str,
    *,
    unit: str | None = None,
    section: list[str] | None = None,
    sheet: str = "CF",
) -> BlockRow:
    cells = [
        {"sheet": sheet, "row": 2, "col": 3, "addr": "C2", "cached_value": "10"},
        {"sheet": sheet, "row": 2, "col": 4, "addr": "D2", "cached_value": "12"},
    ]
    role_cells: list[RowCell] = []
    if unit is not None:
        cells.append(
            {"sheet": sheet, "row": 2, "col": _UNIT_COL, "addr": "B2", "cached_value": unit}
        )
        role_cells.append(RowCell(col=_UNIT_COL, role="unit"))
    layout = Layout(
        sheets=[
            SheetLayout(
                name=sheet,
                blocks=[
                    Block(
                        block_id=f"{sheet}!r1",
                        label_col=1,
                        axis=Axis(
                            id=f"{sheet}!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=3, text="2024", role="historical", period_key="2024"
                                ),
                                AxisHeader(
                                    col=4, text="2025", role="forecast", period_key="2025"
                                ),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=2,
                                label=label,
                                kind="fact",
                                section_path=list(section or []),
                                cells=role_cells,
                            )
                        ],
                    )
                ],
            )
        ]
    )
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": sheet}]},
        cells=cells,
        layout=layout,
    )
    return doc.blocks[0].rows[0]


def _time(row: BlockRow) -> tuple[str | None, str | None, str | None]:
    return row.hints.time_semantics, row.period_position, row.aggregation


def test_price_per_unit_is_not_summed() -> None:
    row = _row("Merchant price (real)", unit="EUR/MWh")
    assert row.unit == "price"
    assert _time(row) == ("rate", "during_period", "none")


def test_coverage_ratio_in_x_is_not_summed_or_averaged() -> None:
    row = _row("Debt Service Coverage Ratio (last 12 months)", unit="x")
    assert row.unit == "ratio"
    assert _time(row) == ("rate", "during_period", "none")


def test_dscr_label_without_a_unit_is_not_summed() -> None:
    row = _row("DSCR (Op CF / Debt Service)")
    assert _time(row) == ("rate", "during_period", "none")


def test_percent_rate_keeps_the_average() -> None:
    row = _row("Interest rate", unit="%")
    assert _time(row) == ("rate", "during_period", "average")


def test_percent_formatted_coverage_ratio_is_not_averaged() -> None:
    row = _row("Debt Service Coverage Ratio", unit="%")
    assert row.unit == "rate"
    assert _time(row) == ("rate", "during_period", "none")


def test_money_flow_keeps_the_sum() -> None:
    row = _row("Revenue", unit="EUR'000")
    assert _time(row) == ("flow", "during_period", "sum")


def test_russian_balance_at_period_start_is_bop() -> None:
    row = _row("Остаток на начало периода")
    assert _time(row) == ("bop", "beginning", "first")
    assert row.hints.sign == "stock"


def test_russian_balance_at_period_end_is_eop() -> None:
    row = _row("Остаток денежных средств на конец периода")
    assert _time(row) == ("eop", "end", "last")


def test_russian_opening_and_closing_balance_words() -> None:
    assert _time(_row("Входящее сальдо")) == ("bop", "beginning", "first")
    assert _time(_row("Исходящий остаток")) == ("eop", "end", "last")


def test_russian_balance_word_alone_is_a_stock() -> None:
    assert _time(_row("Остаток денежных средств")) == ("stock", "end", "last")


def test_russian_input_vat_is_still_a_flow() -> None:
    assert _time(_row("НДС входящий")) == ("flow", "during_period", "sum")


def test_index_at_beginning_of_period_stays_bop() -> None:
    row = _row("Index at beginning of period", unit="Index")
    assert _time(row) == ("bop", "beginning", "first")


def test_revenue_from_beginning_of_year_is_a_flow() -> None:
    assert _time(_row("Revenue from beginning of year")) == ("flow", "during_period", "sum")


def test_english_beginning_and_end_of_period() -> None:
    assert _time(_row("Undrawn funds beginning of period")) == ("bop", "beginning", "first")
    assert _time(_row("Cash at end of period")) == ("eop", "end", "last")
    assert _time(_row("Debt Outstanding BoP (k£)")) == ("bop", "beginning", "first")
    assert _time(_row("Debt Outstanding EoP (k£)")) == ("eop", "end", "last")


def test_brought_and_carried_forward_without_a_roll_forward() -> None:
    assert _time(_row("Cash account b/f")) == ("bop", "beginning", "first")
    assert _time(_row("Reserve c/f")) == ("eop", "end", "last")
    assert _time(_row("Cash b/fwd")) == ("bop", "beginning", "first")
    assert _time(_row("Reserve c/fwd")) == ("eop", "end", "last")


def test_russian_phase_words_are_not_period_boundaries() -> None:
    assert _time(_row("Затраты на конец строительства")) == ("flow", "during_period", "sum")
    assert _time(_row("Затраты на начало строительства")) == ("flow", "during_period", "sum")
    assert _time(_row("на конец дня")) == ("flow", "during_period", "sum")
    assert _time(_row("Начальная остаточная стоимость")) == ("flow", "during_period", "sum")
    assert _time(_row("Остаточная стоимость")) == ("flow", "during_period", "sum")


def test_end_of_construction_is_not_a_period_end() -> None:
    assert _time(_row("Cost to end of construction")) == ("flow", "during_period", "sum")


def test_balance_sheet_section_row_is_a_stock() -> None:
    row = _row("Cash in hand", section=["Balance Sheet"])
    assert _time(row) == ("stock", "end", "last")
    assert row.hints.nature == "balance"


def test_russian_balance_section_row_is_a_stock() -> None:
    row = _row("Основные средства", section=["Баланс", "Активы"])
    assert _time(row) == ("stock", "end", "last")


def test_balance_sheet_tab_row_is_a_stock() -> None:
    assert _time(_row("Retained Earnings", sheet="Balance Sheet")) == ("stock", "end", "last")
    assert _time(_row("Дебиторская задолженность", sheet="Баланс")) == (
        "stock",
        "end",
        "last",
    )


def test_balance_sheet_title_allows_a_year_a_scale_and_consolidated() -> None:
    assert _time(_row("Retained Earnings", sheet="Balance Sheet 2024")) == (
        "stock",
        "end",
        "last",
    )
    assert _time(_row("Основные средства", sheet="Баланс, тыс. руб.")) == (
        "stock",
        "end",
        "last",
    )
    assert _time(_row("Cash", sheet="Consolidated Balance Sheet")) == ("stock", "end", "last")
    assert _time(_row("Revenue", sheet="Balance Sheet 20241")) == ("flow", "during_period", "sum")
    assert _time(_row("Revenue", sheet="Balance check")) == ("flow", "during_period", "sum")
    assert _time(_row("Revenue", sheet="Balances and checks")) == ("flow", "during_period", "sum")


def test_balance_sheet_ratio_price_and_percent_are_not_last() -> None:
    dscr = _row("DSCR", sheet="Balance Sheet")
    multiple = _row("Debt Service Coverage Ratio", unit="x", sheet="Balance Sheet")
    price = _row("Merchant price (real)", unit="EUR/MWh", sheet="Баланс")
    percent = _row("Margin", unit="%", sheet="Balance Sheet")
    llcr = _row("LLCR", sheet="Balance Sheet")
    coverage = _row("Коэффициент покрытия", sheet="Баланс")
    for row in (dscr, multiple, price, percent, llcr, coverage):
        assert row.aggregation != "last", row.label
    assert _time(dscr) == ("rate", "during_period", "none")
    assert _time(multiple) == ("rate", "during_period", "none")
    assert _time(price) == ("rate", "during_period", "none")
    assert percent.hints.time_semantics == "rate"
    assert _time(llcr)[2] == "none"
    assert _time(coverage)[2] == "none"


def test_balance_sheet_section_keeps_opening_and_closing() -> None:
    row = _row("Opening balance", section=["Balance Sheet"])
    assert _time(row) == ("bop", "beginning", "first")


def test_cash_balance_section_is_not_the_balance_sheet() -> None:
    row = _row("Receipts", section=["Cash balance"])
    assert _time(row) == ("flow", "during_period", "sum")
