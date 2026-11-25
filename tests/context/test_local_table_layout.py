from tests.helpers.published import published_context
from tests.layout.test_detect_rows import _c

from finance_context.graph.stage import _local_value_cells, _period_maps
from finance_context.layout.detect import detect_layout


def _layout_and_cells(entries):
    cells = [_c("Model", addr, value) for addr, value in entries]
    return detect_layout(cells), cells


def _publish(entries):
    layout, cells = _layout_and_cells(entries)
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
