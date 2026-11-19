"""Joined labels and section paths survive into the published context."""

from __future__ import annotations

from tests.helpers.published import published_context
from tests.layout.test_detect_rows import _c

from finance_context.layout.detect import detect_layout


def _publish(sheet: str, cells: list[dict]):
    return published_context(
        job_id="row-labels",
        workbook_meta={"sheets": [{"name": sheet}]},
        cells=cells,
        layout=detect_layout(cells),
    )


def test_prihod_keeps_its_name_and_its_period_values() -> None:
    banner = "Затраты в пересчете на 1 кв. м общей пл.:"
    cells = [_c("Затраты", "H4", "2020-06"), _c("Затраты", "I4", "2020-07")]
    cells += [
        _c("Затраты", "F28", "Расход"),
        _c("Затраты", "H28", "10"),
        _c("Затраты", "I28", "11"),
        _c("Затраты", "B29", banner),
        _c("Затраты", "F29", "Приход"),
        _c("Затраты", "H29", "12"),
        _c("Затраты", "I29", "13"),
    ]
    doc = _publish("Затраты", cells)
    rows = {row.row: row for block in doc.blocks for row in block.rows}
    assert rows[29].label == f"{banner} Приход"
    assert rows[29].label_path == []
    assert rows[29].values == ["12", "13"]
    assert rows[28].label == "Расход"
    assert rows[28].values == ["10", "11"]


def test_a_valued_profit_line_is_absent_from_the_following_label_path() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A15", "Прибыль (убыток) от реализации"),
        _c("ОПиУ", "C15", "70"),
        _c("ОПиУ", "D15", "80"),
        _c("ОПиУ", "A16", "  Проценты к получению"),
        _c("ОПиУ", "C16", "5"),
        _c("ОПиУ", "D16", "6"),
    ]
    doc = _publish("ОПиУ", cells)
    rows = {row.row: row for block in doc.blocks for row in block.rows}
    assert rows[15].label == "Прибыль (убыток) от реализации"
    assert rows[15].values == ["70", "80"]
    assert rows[16].label == "Проценты к получению"
    assert rows[16].label_path == []
    assert rows[16].values == ["5", "6"]
