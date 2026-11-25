from collections import defaultdict

from tests.helpers.published import published_context
from tests.layout.test_detect_rows import _c

from finance_context.layout.detect import detect_layout
from finance_context.layout.sensitivity import sensitivity_blocks


def test_numeric_sensitivity_grids_publish_local_axes_and_values():
    cells = []
    for title_row, title in ((1, "IRR by entry and exit multiple"), (7, "IRR by leverage")):
        cells.append(_c("Cases", f"B{title_row}", title))
        for col, label in zip("CDE", ["9.0x", "10.0x", "11.0x"], strict=True):
            cells.append(_c("Cases", f"{col}{title_row + 1}", label))
        for offset, label in enumerate(["9", "10", "11"], start=2):
            row = title_row + offset
            cells.append(_c("Cases", f"B{row}", label, number_format='0.0"x"'))
            for col, value in zip("CDE", ["0.181", "0.212", "0.239"], strict=True):
                cells.append(_c("Cases", f"{col}{row}", value, number_format="0.0%"))

    layout = detect_layout(cells)
    doc = published_context(
        job_id="grids", workbook_meta={"sheets": [{"name": "Cases"}]}, cells=cells, layout=layout,
    )
    assert layout.sheets[0].axes == []
    assert [block.kind for block in doc.blocks] == ["matrix", "matrix"]
    titles = ["IRR by entry and exit multiple", "IRR by leverage"]
    for block, title in zip(doc.blocks, titles, strict=True):
        assert [(p["col"], p["text"], p["period_key"]) for p in block.periods] == [
            (3, "9.0x", "c3"), (4, "10.0x", "c4"), (5, "11.0x", "c5"),
        ]
        assert [row.label for row in block.rows] == ["9.0x", "10.0x", "11.0x"]
        assert all(row.label_path == [title] for row in block.rows)
        assert block.rows[1].values == ["0.181", "0.212", "0.239"]


def test_calendar_headers_keep_their_timeline():
    cells = [_c("Model", "A1", "Scenario")]
    for col, label in zip("BCD", ["2024", "2025", "2026"], strict=True):
        cells.append(_c("Model", f"{col}1", label))
    for row in range(2, 5):
        cells.append(_c("Model", f"A{row}", str(row)))
        for col in "BCD":
            cells.append(_c("Model", f"{col}{row}", str(row * 2)))
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    assert all(block.kind != "matrix" for block in sheet.blocks)


def test_two_calendar_rows_do_not_turn_preceding_amounts_into_grid_headers():
    cells = [
        _c("Model", "A1", "Results"),
        _c("Model", "A2", "Growth"),
        _c("Model", "A3", "Costs"),
        _c("Model", "A4", "Profit"),
        _c("Model", "B3", "-60000"),
        _c("Model", "B4", "28129"),
    ]
    years = ["2024", "2025", "2026"]
    for col, year, growth in zip("CDE", years, ["0.05", "0.04", "0.03"], strict=True):
        cells.extend([
            _c("Model", f"{col}1", year),
            _c("Model", f"{col}2", growth, number_format="0.0%"),
            _c("Model", f"{col}3", "-60000"),
            _c("Model", f"{col}4", "33000"),
        ])
    by_row = defaultdict(list)
    for cell in cells:
        by_row[cell["row"]].append(cell)
    assert sensitivity_blocks("Model", by_row, False) == ([], set())


def test_grid_with_a_base_case_and_second_column_heading_uses_only_result_rows():
    pct = "#,##0%_);(#,##0%);@"
    cells = [_c("NPV", "C1", "NPV by investment and interest rate")]
    cells.append(_c("NPV", "C3", "7586"))
    rates = ["0.12", "0.11", "0.10"]
    for col, rate, offset in zip("DEF", rates, ["0.02", "0.01", "0"], strict=True):
        cells.extend([
            _c("NPV", f"{col}2", rate, number_format=pct),
            _c("NPV", f"{col}3", offset, number_format=pct),
        ])
    for n, label in enumerate(["0.2", "0.1", "0"], start=4):
        cells.append(_c("NPV", f"C{n}", label, number_format=pct))
        cells.extend(_c("NPV", f"{col}{n}", "1230") for col in "DEF")
    [block] = detect_layout(cells).sheets[0].blocks
    assert [header.text for header in block.axis.headers] == ["12%", "11%", "10%"]
    assert [row.label for row in block.rows] == ["20%", "10%", "0%"]
