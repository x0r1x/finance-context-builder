from tests.helpers.published import published_context
from tests.layout.test_detect_rows import _c

from finance_context.layout.detect import detect_layout


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
