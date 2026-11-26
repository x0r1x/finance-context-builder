import pytest
from tests.helpers.published import published_context
from tests.layout.test_detect_rows import _c

from finance_context.graph.stage import _local_value_cells, _period_maps, _side_cells
from finance_context.layout.detect import detect_layout


def _layout_and_cells(entries, *, hidden_cells=(), hidden_rows=None):
    cells = [
        _c(
            "Model", addr, value, formula=formula[0] if formula else None,
            hidden=addr in hidden_cells,
        )
        for addr, value, *formula in entries
    ]
    return detect_layout(cells, hidden_rows=hidden_rows), cells


def _publish(entries, **visibility):
    layout, cells = _layout_and_cells(entries, **visibility)
    return published_context(
        job_id="local-tables", workbook_meta={"sheets": [{"name": "Model"}]},
        cells=cells, layout=layout,
    )


def _rows(doc, label):
    return [(block, row) for block in doc.blocks for row in block.rows if row.label == label]


def test_two_local_lists_on_the_same_rows_keep_separate_values():
    doc = _publish([
        ("B1", "Valuation"), ("E1", "Loan"),
        ("B2", "NOI"), ("C2", "328"), ("E2", "Requested"), ("F2", "360"),
        ("B3", "Value"), ("C3", "570"), ("E3", "LTV"), ("F3", "0.65"),
    ])
    assert len(doc.blocks) == 2
    for label, col, value in [("NOI", 3, "328"), ("Requested", 6, "360")]:
        [(block, row)] = _rows(doc, label)
        assert [p["col"] for p in block.periods] == [col]
        assert row.values == [value]


def test_tables_with_their_own_headers_do_not_borrow_other_header_periods():
    doc = _publish([
        ("B1", "Inputs"), ("C1", "Input"), ("B2", "Minimum cash"), ("C2", "20"),
        ("B3", "Tax rate"), ("C3", "0.25"), ("B4", "Commitment"), ("C4", "75"),
        ("B6", "Debt tranches"), ("C6", "x EBITDA"), ("D6", "Cash rate"), ("E6", "Sweep"),
        ("B7", "Term Loan B"), ("C7", "3.75"), ("D7", "0.085"), ("E7", "1"),
        ("B8", "Senior Notes"), ("C8", "1.25"), ("D8", "0.09"), ("E8", "0"),
        ("B10", "Projections"), ("C10", "Year 1"), ("D10", "Year 2"), ("E10", "Year 3"),
        ("B11", "Revenue growth"), ("C11", "0.06"), ("D11", "0.06"), ("E11", "0.04"),
        ("B12", "Margin"), ("C12", "0.21"), ("D12", "0.22"), ("E12", "0.23"),
    ])
    assert len(doc.blocks) == 3
    [(debt, tranche)] = _rows(doc, "Term Loan B")
    [(projection, growth)] = _rows(doc, "Revenue growth")
    assert [(p["text"], p["period_key"]) for p in debt.periods] == [
        ("x EBITDA", "c3"), ("Cash rate", "c4"), ("Sweep", "c5"),
    ]
    assert [p["text"] for p in projection.periods] == ["Year 1", "Year 2", "Year 3"]
    assert tranche.values == ["3.75", "0.085", "1"]
    assert tranche.label_path == ["Debt tranches"]
    assert growth.label_path == ["Projections"]


def test_input_panels_under_a_calendar_keep_only_their_own_values():
    doc = _publish([
        ("B1", "Model"), ("G1", "2020"), ("H1", "2021"), ("I1", "2022"),
        ("B3", "General Inputs"), ("F3", "Energy"), ("J3", "PPA"),
        ("B4", "CPI Estimate"), ("D4", "0.02"), ("F4", "Energy Probability"), ("H4", "P50"),
        ("J4", "Term"), ("K4", "20"), ("B5", "Project life"), ("D5", "30"),
        ("F5", "Probability"), ("G5", "Production"), ("J5", "Escalation"), ("K5", "0.01"),
        ("B6", "Size"), ("D6", "25"), ("F6", "P50"), ("G6", "500"),
        ("J6", "Price"), ("K6", "60"),
        ("B7", "Start date"), ("D7", "01.01.2020"), ("F7", "P75"), ("G7", "450"),
        ("J7", "Year"), ("K7", "Power price"),
        ("J8", "2020"), ("K8", "62"), ("J9", "2021"), ("K9", "63"), ("J10", "2022"), ("K10", "64"),
        ("B13", "Production forecast"), ("G13", "2020"), ("H13", "2021"), ("I13", "2022"),
        ("B14", "Revenue"), ("G14", "100"), ("H14", "110"), ("I14", "120"),
    ])
    [(panel, cpi)] = _rows(doc, "CPI Estimate")
    [(energy, production)] = _rows(doc, "P50")
    [(ppa, term)] = _rows(doc, "Term")
    assert all(block.kind != "timeline" for block in (panel, energy, ppa))
    assert [cpi.values, production.values, term.values] == [["0.02"], ["500"], ["20"]]
    [(_energy_input, probability)] = _rows(doc, "Energy Probability")
    [(_general, date)] = _rows(doc, "Start date")
    assert probability.values == ["P50"]
    assert date.values == ["01.01.2020"]
    [(schedule, price)] = _rows(doc, "Power price")
    assert schedule.kind == "timeline"
    assert price.value_col == 11
    assert price.values == ["62", "63", "64"]
    [(forecast, revenue)] = _rows(doc, "Revenue")
    assert forecast.kind == "timeline"
    assert revenue.values == ["100", "110", "120"]


def test_panel_cell_can_share_a_physical_row_with_a_calendar_observation():
    entries = [
        ("B1", "Model"), ("G1", "2020"), ("H1", "2021"), ("I1", "2022"),
        ("J1", "2023"), ("K1", "2024"), ("B3", "Inputs"), ("H3", "Capital costs"),
        ("B4", "Tax"), ("D4", "0.2"), ("H4", "Land"), ("K4", "100"),
        ("B5", "Share"), ("D5", "0.8"), ("H5", "Construction"), ("K5", "110"),
        ("B6", "Rate"), ("D6", "0.1"), ("H6", "Installation"), ("K6", "120"),
        ("B7", "Revenue"), ("G7", "50"), ("H7", "Total costs"), ("K7", "330"),
    ]
    doc = _publish(entries)
    [(timeline, revenue)] = _rows(doc, "Revenue")
    [(panel, total)] = _rows(doc, "Total costs")
    assert timeline.kind == "timeline"
    assert panel.kind != "timeline"
    assert revenue.values == ["50", None, None, None, None]
    assert revenue.value_statuses[-1] == "not_applicable"
    assert total.values == ["330"]
    layout, _ = _layout_and_cells(entries)
    local = _local_value_cells(layout)
    periods, _ = _period_maps(layout)
    assert local["Model", 7, 11] == (total.row_key, panel.periods[0]["period_key"])
    assert periods["Model", 11] == "2024"


@pytest.mark.parametrize(("hidden_rows", "expected_hidden"), [
    ({"Model": {10}}, True),
    ({"Model": set()}, False),
    (None, True),
])
def test_headed_cost_tables_and_adjacent_inputs_under_a_calendar(hidden_rows, expected_hidden):
    entries = [
        ("B2", "Year"), ("G2", "2020"), ("H2", "2021"), ("I2", "2022"),
        ("J2", "2023"), ("K2", "2024"),
        ("B4", "Operating costs"),
        ("E6", "From Year"), ("F6", "To Year"), ("G6", "$/MWh"), ("I6", "Financing"),
        ("B7", "Maintenance"), ("D7", "300"), ("E7", "1"), ("F7", "30"), ("G7", "5"),
        ("B8", "Insurance"), ("D8", "100"), ("E8", "1"), ("F8", "30"), ("G8", "2"),
        ("I8", "Amortization"), ("K8", "19"),
        ("B9", "Property tax"), ("D9", "150"), ("E9", "1"), ("F9", "30"), ("G9", "3"),
        ("I9", "Sizing"), ("K9", "0.85"),
        ("G10", "10", "=SUM(G7:G9)"),
        ("I10", "Minimum coverage"), ("K10", "1"),
        ("G11", "20.85", "=SUM(K8:K10)"),
        ("B12", "Replacement"), ("D12", "450"), ("E12", "15"), ("F12", "15"),
        ("I12", "Reserve"), ("K12", "6"),
        ("B14", "Tax"),
        ("D16", "% of Capex"), ("E16", "Depreciation Rate"),
        ("B17", "Class A"), ("D17", "0.2"), ("E17", "0.05"),
        ("G17", "Local tax rate"), ("I17", "0.25"),
        ("B18", "Class B"), ("D18", "0"), ("E18", "0.2"),
        ("B19", "Class C"), ("E19", "0.05"),
        ("B22", "Year"), ("G22", "2020"), ("H22", "2021"), ("I22", "2022"),
        ("J22", "2023"), ("K22", "2024"),
        ("B23", "Revenue"), ("G23", "50"), ("H23", "60"), ("I23", "70"),
    ]
    doc = _publish(entries, hidden_cells={"G10"}, hidden_rows=hidden_rows)
    [(costs, insurance)] = _rows(doc, "Insurance")
    [(costs_tail, replacement)] = _rows(doc, "Replacement")
    [(finance, amortization)] = _rows(doc, "Amortization")
    [(finance_tail, reserve)] = _rows(doc, "Reserve")
    [(totals, total)] = _rows(doc, "Total")
    [(tax, class_a)] = _rows(doc, "Class A")
    [(_, class_b)] = _rows(doc, "Class B")
    [(tax_rate, rate)] = _rows(doc, "Local tax rate")
    [(_, class_c)] = _rows(doc, "Class C")
    [(forecast, revenue)] = _rows(doc, "Revenue")

    assert costs.kind == "matrix"
    assert costs == costs_tail
    assert [p["col"] for p in costs.periods] == [4, 5, 6, 7]
    assert insurance.values == ["100", "1", "30", "2"]
    assert replacement.values == ["450", "15", "15", None]
    assert insurance.label_path == ["Operating costs"]
    assert totals == costs
    assert total.values == [None, None, None, "10"]
    assert total.hidden is expected_hidden
    assert finance == finance_tail
    assert amortization.values == ["19"]
    assert reserve.values == ["6"]
    assert amortization.label_path == ["Financing"]
    assert tax.kind == tax_rate.kind == "key_value"
    assert class_a.values == ["0.2", "0.05"]
    assert class_b.value_statuses == ["zero_explicit", "cached"]
    assert class_c.values == [None, "0.05"]
    assert class_c.value_statuses == ["empty", "cached"]
    assert rate.values == ["0.25"]
    assert forecast.kind == "timeline"
    assert revenue.values[:3] == ["50", "60", "70"]

    layout, _ = _layout_and_cells(entries)
    local = _local_value_cells(layout)
    sides = _side_cells(layout)
    periods, _ = _period_maps(layout)
    assert sides["Model", 8, 4] == insurance.row_key
    assert periods["Model", 4] == "c4"
    assert local["Model", 8, 7] == (insurance.row_key, "c7")
    assert local["Model", 8, 11] == (amortization.row_key, "c11")
    assert local["Model", 17, 9] == (rate.row_key, "c9")


def test_local_captions_without_an_independent_neighbor_do_not_claim_calendar_rows():
    doc = _publish([
        ("B2", "Year"), ("G2", "2020"), ("H2", "2021"), ("I2", "2022"),
        ("B4", "Forecast"),
        ("D6", "Amount"), ("E6", "Rate"),
        ("B7", "Revenue"), ("D7", "100"), ("E7", "0.01"),
        ("G7", "110"), ("H7", "120"), ("I7", "130"),
        ("B8", "Costs"), ("D8", "50"), ("E8", "0.02"),
        ("G8", "51"), ("H8", "52"), ("I8", "53"),
    ])
    [(timeline, revenue)] = _rows(doc, "Revenue")
    assert timeline.kind == "timeline"
    assert revenue.values == ["110", "120", "130"]
