from finance_context.context.build import build_context
from finance_context.context.series import normalize_value, phase_gate, value_status
from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.models import MappingDocument
from finance_context.render.markdown import render_markdown


def test_status_keeps_explicit_zero_apart_from_empty() -> None:
    assert value_status("0", applicable=True) == "zero_explicit"
    assert value_status("0.0", applicable=True) == "zero_explicit"
    assert value_status(None, applicable=True) == "empty"
    assert value_status("", applicable=True) == "empty"
    assert value_status("12.5", applicable=True) == "cached"
    assert value_status(None, applicable=False) == "not_applicable"
    assert normalize_value("12.5", 1000) == "12500"
    assert normalize_value("31.12.2023", 1) is None
    assert phase_gate("PC traffic", None) == "operation"
    assert phase_gate("Capex", "cf.capex") == "construction"
    assert phase_gate("Opening cash", "bs.cash") is None


def test_build_marks_scale_and_blank_cells() -> None:
    layout = Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(
                            id="CF!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2, text="2023", role="historical", period_key="2023"
                                ),
                                AxisHeader(
                                    col=3, text="2024E", role="forecast", period_key="2024E"
                                ),
                            ],
                        ),
                        rows=[LayoutRow(row=2, label="Maintenance k£", kind="fact")],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "CF",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "cached_value": "2.5",
            "formula_raw": None,
            "number_format": "#,##0.0",
        }
    ]
    doc = build_context(
        job_id="scale",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=layout,
        mapping=MappingDocument(),
    )
    row = doc.blocks[0].rows[0]
    assert row.hints.scale == "k"
    assert row.hints.currency == "GBP"
    assert row.scale_factor == 1000
    assert row.values == ["2.5", None]
    assert row.normalized_values == ["2500", None]
    assert row.value_statuses == ["cached", "empty"]
    assert row.period_position == "during_period"
    assert row.aggregation == "sum"
    rendered = render_markdown(doc)
    assert "k£ ×1000" in rendered
    assert "2.5 (2500)" in rendered
    assert "empty" in rendered
    assert "flow/during_period/sum" in rendered


def _c(sheet: str, addr: str, value: str | None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def test_empty_operation_line_outside_phase_is_not_applicable() -> None:
    cells = [
        _c("TBA", "A1", "Year"),
        _c("TBA", "B1", "1"),
        _c("TBA", "C1", "2"),
        _c("TBA", "D1", "3"),
        _c("TBA", "A2", "Construction"),
        _c("TBA", "B2", "1"),
        _c("TBA", "C2", "1"),
        _c("TBA", "D2", "0"),
        _c("TBA", "A3", "Operation"),
        _c("TBA", "B3", "0"),
        _c("TBA", "C3", "0"),
        _c("TBA", "D3", "1"),
        _c("TBA", "A4", "PC traffic"),
        _c("TBA", "D4", "5"),
        _c("TBA", "A5", "Toll revenue"),
        _c("TBA", "B5", "0"),
        _c("TBA", "C5", "0"),
        _c("TBA", "D5", "8"),
    ]
    layout = detect_layout(cells)
    doc = build_context(
        job_id="phase",
        workbook_meta={"sheets": [{"name": "TBA"}]},
        cells=cells,
        layout=layout,
        mapping=MappingDocument(),
    )
    rows = {row.label: row for block in doc.blocks for row in block.rows}
    traffic = rows["PC traffic"]
    assert traffic.value_statuses == ["not_applicable", "not_applicable", "cached"]
    assert traffic.values[2] == "5"
    revenue = rows["Toll revenue"]
    assert revenue.value_statuses == ["zero_explicit", "zero_explicit", "cached"]
    rendered = render_markdown(doc)
    assert "n/a" in rendered
    assert "| 0 |" in rendered or "0<br>" in rendered or rendered.count(" 0 ") >= 1


def test_float_residue_next_to_the_series_scale_is_zero() -> None:
    from finance_context.context.series import normalize_series, temporal_profile

    assert normalize_series(["-60000", "1.9099388737231493E-11", "5"], 1) == ["-60000", "0", "5"]
    assert normalize_series(["0.000001", "0.000002"], 1) == ["0.000001", "0.000002"]
    assert temporal_profile("instant") == ("instant", "none")


def test_debt_service_cover_is_gated_to_operations() -> None:
    assert phase_gate("Debt Service Coverage Ratio", "cov.dscr") == "operation"
