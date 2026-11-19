"""Joined labels and section paths survive into the published context."""

from __future__ import annotations

import pytest
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


@pytest.mark.parametrize("caption", ["Business Unit", "Unit Name", "Source of Funds"])
def test_a_caption_containing_a_role_word_does_not_erase_the_block(caption: str) -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", caption),
        _c(sheet, "D1", "2024"),
        _c(sheet, "E1", "2025"),
        _c(sheet, "A2", "Cloud"),
        _c(sheet, "D2", "10"),
        _c(sheet, "E2", "12"),
        _c(sheet, "A3", "Colocation"),
        _c(sheet, "D3", "4"),
        _c(sheet, "E3", "5"),
    ]
    doc = _publish(sheet, cells)
    assert [(row.label, row.values) for block in doc.blocks for row in block.rows] == [
        ("Cloud", ["10", "12"]),
        ("Colocation", ["4", "5"]),
    ]


@pytest.mark.parametrize("caption", ["Notes", "Comment", "Комментарий", "Прим."])
def test_notes_above_the_periods_stay_out_of_names_and_sections(caption: str) -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Operations"),
        _c(sheet, "A2", "Item"),
        _c(sheet, "B2", caption),
        _c(sheet, "D3", "2024"),
        _c(sheet, "E3", "2025"),
        _c(sheet, "A4", "Revenue"),
        _c(sheet, "B4", "Check"),
        _c(sheet, "D4", "10"),
        _c(sheet, "E4", "12"),
        _c(sheet, "A5", "Costs"),
        _c(sheet, "B5", "Preliminary"),
        _c(sheet, "D5", "4"),
        _c(sheet, "E5", "5"),
    ]
    doc = _publish(sheet, cells)
    rows = {row.row: row for block in doc.blocks for row in block.rows}
    assert set(rows) == {1, 4, 5}
    assert rows[4].label == "Revenue"
    assert rows[4].kind == "fact"
    assert rows[4].values == ["10", "12"]
    assert rows[4].label_path == ["Operations"]
    assert rows[5].label == "Costs"


@pytest.mark.parametrize(
    ("caption", "cached"), [("Item", "Operating Activities"), ("Notes", "Audited")]
)
def test_non_value_formulas_preserve_the_section(caption: str, cached: str) -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Item"),
        _c(sheet, "B1", caption),
        _c(sheet, "D1", "2024"),
        _c(sheet, "E1", "2025"),
        _c(sheet, "A2", "Operating Activities"),
        _c(sheet, "B2", cached, formula="=A20"),
        _c(sheet, "A3", "  Revenue"),
        _c(sheet, "D3", "10"),
        _c(sheet, "E3", "12"),
        _c(sheet, "A4", "  Uncomputed"),
        _c(sheet, "C4", None, formula="=D3"),
    ]
    doc = _publish(sheet, cells)
    rows = {row.row: row for block in doc.blocks for row in block.rows}
    assert rows[2].label == "Operating Activities"
    assert rows[2].kind == "abstract"
    assert rows[3].label_path == ["Operating Activities"]
    assert rows[4].kind == "fact"
