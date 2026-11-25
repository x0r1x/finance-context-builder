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
    section = "Планируемые затраты по проекту"
    cells = [_c("Затраты", "H4", "2020-06"), _c("Затраты", "I4", "2020-07")]
    cells += [
        _c("Затраты", "A6", section),
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
    assert rows[29].label == "Приход"
    assert rows[29].label_path == [section]
    assert rows[29].parent_label == section
    assert rows[29].values == ["12", "13"]
    assert rows[28].label == "Расход"
    assert rows[28].values == ["10", "11"]


@pytest.mark.parametrize(
    "caption", ["ОКОФ", "Код ОКОФ", "ОКОФ (код)", "Основное средство.Код по ОКОФ"]
)
def test_okof_is_excluded_by_column_role_and_preserved_in_real_names(caption: str) -> None:
    sheet = "ОС_БГИ"
    cells = [
        _c(sheet, "A6", "Подразделение"),
        _c(sheet, "C6", "Наименование"),
        _c(sheet, "D6", caption),
        _c(sheet, "F7", "2024"),
        _c(sheet, "G7", "2025"),
        _c(sheet, "A8", "Основное средство"),
        _c(sheet, "C10", "Генератор"),
        _c(sheet, "D10", "330.28.29"),
        _c(sheet, "F10", "4"),
        _c(sheet, "G10", "5"),
        _c(sheet, "C11", "Насос ОКОФ 330.28.13"),
        _c(sheet, "D11", "0330.28.13.11"),
        _c(sheet, "F11", "10"),
        _c(sheet, "G11", "12"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[10].label == "Генератор"
    assert rows[10].values == ["4", "5"]
    assert [(c.addr, c.role, c.cached_value) for c in rows[10].cells] == [
        ("D10", "note", "330.28.29")
    ]
    assert rows[11].label == "Насос ОКОФ 330.28.13"
    assert rows[11].label_path == ["Основное средство"]
    assert rows[11].values == ["10", "12"]
    assert [(c.addr, c.role, c.cached_value) for c in rows[11].cells] == [
        ("D11", "note", "0330.28.13.11")
    ]


def test_a_shallower_fact_closes_the_section_for_later_deeper_rows() -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Статья"),
        _c(sheet, "B1", "2024"),
        _c(sheet, "C1", "2025"),
        _c(sheet, "A2", "  Капитал"),
        _c(sheet, "A3", "Итого"),
        _c(sheet, "B3", "70"),
        _c(sheet, "C3", "80"),
        _c(sheet, "A4", "   Деталь"),
        _c(sheet, "B4", "9"),
        _c(sheet, "C4", "14"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[2].kind == "abstract"
    assert rows[4].kind == "fact"
    assert rows[4].parent_label is None
    assert rows[4].label_path == []


def test_a_blank_formula_in_a_label_column_preserves_the_section() -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Статья"),
        _c(sheet, "B1", "Показатель"),
        _c(sheet, "C1", "2024"),
        _c(sheet, "D1", "2025"),
        _c(sheet, "A2", "Операционные"),
        _c(sheet, "B2", None, formula="=A20"),
        _c(sheet, "A3", "Выручка"),
        _c(sheet, "C3", "10"),
        _c(sheet, "D3", "12"),
        _c(sheet, "A4", "Будущая выручка"),
        _c(sheet, "C4", None, formula="=C3"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[2].kind == "abstract"
    assert not rows[2].cells
    assert rows[3].label_path == ["Операционные"]
    assert rows[4].kind == "fact"
    assert rows[4].value_statuses == ["empty", "empty"]


def test_an_aggregate_fact_enters_the_child_label_path_and_keeps_its_kind() -> None:
    sheet = "ОПиУ"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", "Инвестиции"),
        _c(sheet, "A3", "CAPEX"),
        _c(sheet, "C3", "30", formula="=C4+C5"),
        _c(sheet, "D3", "40", formula="=D4+D5"),
        _c(sheet, "A4", "  IaaS CAPEX"),
        _c(sheet, "C4", "18"),
        _c(sheet, "D4", "20"),
        _c(sheet, "A5", "  DC CAPEX"),
        _c(sheet, "C5", "12"),
        _c(sheet, "D5", "20"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == "fact"
    assert rows[3].values == ["30", "40"]
    assert rows[4].parent_label == "CAPEX"
    assert rows[4].label_path == ["Инвестиции", "CAPEX"]
    assert rows[4].values == ["18", "20"]
    assert rows[5].label_path == ["Инвестиции", "CAPEX"]


def test_a_sum_on_another_sheet_leaves_the_section_label_path() -> None:
    sheet = "ОПиУ"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", "Investments"),
        _c(sheet, "A3", "CAPEX"),
        _c(sheet, "C3", "30", formula="=SUM(Inputs!C4:C5)"),
        _c(sheet, "D3", "40", formula="=SUM(Inputs!D4:D5)"),
        _c(sheet, "A4", "  IaaS CAPEX"),
        _c(sheet, "C4", "18"),
        _c(sheet, "D4", "20"),
        _c(sheet, "A5", "  DC CAPEX"),
        _c(sheet, "C5", "12"),
        _c(sheet, "D5", "14"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == "fact"
    assert rows[3].values == ["30", "40"]
    assert rows[4].parent_label == "Investments"
    assert rows[4].label_path == ["Investments"]
    assert rows[4].values == ["18", "20"]
    assert rows[5].label_path == ["Investments"]


def test_a_sum_quoted_as_a_criterion_does_not_enter_the_label_path() -> None:
    sheet = "ОПиУ"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", "Investments"),
        _c(sheet, "A3", "CAPEX"),
        _c(sheet, "C3", "30", formula='=COUNTIF(Inputs!C4:C5,"SUM(C4:C5)")'),
        _c(sheet, "D3", "40", formula='=COUNTIF(Inputs!D4:D5,"SUM(D4:D5)")'),
        _c(sheet, "A4", "  IaaS CAPEX"),
        _c(sheet, "C4", "18"),
        _c(sheet, "D4", "20"),
        _c(sheet, "A5", "  DC CAPEX"),
        _c(sheet, "C5", "12"),
        _c(sheet, "D5", "14"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == "fact"
    assert rows[4].parent_label == "Investments"
    assert rows[4].label_path == ["Investments"]
    assert rows[4].values == ["18", "20"]
    assert rows[5].label_path == ["Investments"]


def test_a_local_sum_keeps_its_children_beside_another_sheet() -> None:
    sheet = "ОПиУ"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", "Investments"),
        _c(sheet, "A3", "CAPEX"),
        _c(sheet, "C3", "30", formula="=SUM(C4:C5)+Inputs!C8"),
        _c(sheet, "D3", "40", formula="=SUM(D4:D5)+Inputs!D8"),
        _c(sheet, "A4", "  IaaS CAPEX"),
        _c(sheet, "C4", "18"),
        _c(sheet, "D4", "20"),
        _c(sheet, "A5", "  DC CAPEX"),
        _c(sheet, "C5", "12"),
        _c(sheet, "D5", "14"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == "fact"
    assert rows[3].values == ["30", "40"]
    assert rows[4].parent_label == "CAPEX"
    assert rows[4].label_path == ["Investments", "CAPEX"]
    assert rows[4].values == ["18", "20"]
    assert rows[5].label_path == ["Investments", "CAPEX"]


def _capex_children(formula_c: str, formula_d: str):
    sheet = "ОПиУ"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", "Investments"),
        _c(sheet, "A3", "CAPEX"),
        _c(sheet, "C3", "30", formula=formula_c),
        _c(sheet, "D3", "40", formula=formula_d),
        _c(sheet, "A4", "  IaaS CAPEX"),
        _c(sheet, "C4", "18"),
        _c(sheet, "D4", "20"),
        _c(sheet, "A5", "  DC CAPEX"),
        _c(sheet, "C5", "12"),
        _c(sheet, "D5", "14"),
    ]
    return {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}


def test_a_balance_subtotal_enters_the_child_label_path() -> None:
    sheet = "BS_PL_DCF"
    cells = [_c(sheet, "C1", "2018"), _c(sheet, "D1", "2019")]
    cells += [
        _c(sheet, "A2", "Бухгалтерский баланс"),
        _c(sheet, "A3", "Необоротные активы"),
        _c(sheet, "C3", "260000", formula="=SUM(C4:C5)"),
        _c(sheet, "D3", "252866", formula="=SUM(D4:D5)"),
        _c(sheet, "A4", "  Основные средства"),
        _c(sheet, "C4", "250000"),
        _c(sheet, "D4", "242866"),
        _c(sheet, "A5", "  Прочее"),
        _c(sheet, "C5", "10000"),
        _c(sheet, "D5", "10000"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == "fact"
    assert rows[3].values == ["260000", "252866"]
    assert rows[4].parent_label == "Необоротные активы"
    assert rows[4].label_path == ["Бухгалтерский баланс", "Необоротные активы"]
    assert rows[4].values == ["250000", "242866"]
    assert rows[5].label_path == ["Бухгалтерский баланс", "Необоротные активы"]


def test_a_parallel_column_sum_leaves_the_section_label_path() -> None:
    sheet = "BS"
    cells = [_c(sheet, "C1", "2018"), _c(sheet, "D1", "2019")]
    cells += [
        _c(sheet, "A2", "Бухгалтерский баланс"),
        _c(sheet, "A3", "Необоротные активы"),
        _c(sheet, "C3", "1", formula="=SUM(H4:H5)"),
        _c(sheet, "D3", "2", formula="=SUM(I4:I5)"),
        _c(sheet, "A4", "  Основные средства"),
        _c(sheet, "C4", "10"),
        _c(sheet, "D4", "11"),
        _c(sheet, "A5", "  Прочее"),
        _c(sheet, "C5", "3"),
        _c(sheet, "D5", "4"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[4].parent_label == "Бухгалтерский баланс"
    assert rows[4].label_path == ["Бухгалтерский баланс"]
    assert rows[5].label_path == ["Бухгалтерский баланс"]
    assert rows[4].values == ["10", "11"]


def test_a_sum_inside_an_if_test_does_not_enter_the_label_path() -> None:
    rows = _capex_children("=IF(SUM(C4:C5)>0,C10,C11)", "=IF(SUM(D4:D5)>0,D10,D11)")
    assert rows[3].kind == "fact"
    assert rows[3].values == ["30", "40"]
    assert rows[4].parent_label == "Investments"
    assert rows[4].label_path == ["Investments"]
    assert rows[4].values == ["18", "20"]
    assert rows[5].label_path == ["Investments"]


def test_a_sum_of_separate_refs_enters_the_label_path() -> None:
    rows = _capex_children("=SUM(C4,C5)", "=SUM(D4,D5)")
    assert rows[3].kind == "fact"
    assert rows[4].parent_label == "CAPEX"
    assert rows[4].label_path == ["Investments", "CAPEX"]
    assert rows[5].label_path == ["Investments", "CAPEX"]
    assert rows[4].values == ["18", "20"]


def test_a_sum_keeps_a_local_range_beside_another_sheet() -> None:
    rows = _capex_children("=SUM(Inputs!C8,C4:C5)", "=SUM(Inputs!D8,D4:D5)")
    assert rows[4].parent_label == "CAPEX"
    assert rows[4].label_path == ["Investments", "CAPEX"]
    assert rows[5].label_path == ["Investments", "CAPEX"]
    assert rows[4].values == ["18", "20"]


@pytest.mark.parametrize(
    ("formula_c", "formula_d"),
    [
        ("=C4+1", "=D4+1"),
        ("=C4+C4", "=D4+D4"),
        ("=IF(C10>0,C4+1,C5+1)", "=IF(D10>0,D4+1,D5+1)"),
    ],
)
def test_only_a_formula_combining_distinct_rows_supplies_a_parent(
    formula_c: str, formula_d: str
) -> None:
    rows = _capex_children(formula_c, formula_d)
    assert rows[4].label_path == ["Investments"]
    assert rows[5].label_path == ["Investments"]


@pytest.mark.parametrize(
    ("rollup", "subtotal", "is_parent"),
    [
        ("=SUM({c}4,{c}6)", "=SUM({c}5:{c}5)", True),
        ("=SUM({c}4,{c}6)", "=SUM({c}5)", False),
        ("=SUM({c}4,{c}6)", "={c}5", False),
        ("=SUM({c}4,{c}6)", "=SUM(H5:H5)", False),
        (None, "=SUM({c}5:{c}5)", False),
    ],
)
def test_single_detail_of_nested_balance_subtotal_keeps_its_section(
    rollup: str | None, subtotal: str, is_parent: bool
) -> None:
    sheet = "BS"
    section = "Бухгалтерский баланс"
    long_term = "Обязательства долгосрочные"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", section),
        _c(sheet, "A3", "Долгосрочные и краткосрочные обязательства"),
        _c(sheet, "A4", f"  {long_term}"),
        _c(sheet, "A5", "    Кредиты и займы"),
        _c(sheet, "A6", "  Обязательства краткосрочные"),
    ]
    for col in "CD":
        cells.extend([
            _c(sheet, f"{col}3", "17", formula=rollup.format(c=col) if rollup else None),
            _c(sheet, f"{col}4", "10", formula=subtotal.format(c=col)),
            _c(sheet, f"{col}5", "10"),
            _c(sheet, f"{col}6", "7"),
        ])
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[4].kind == rows[5].kind == "fact"
    assert rows[5].parent_label == (long_term if is_parent else section)
    assert rows[5].label_path == ([section, long_term] if is_parent else [section])
    assert rows[5].values == ["10", "10"]


@pytest.mark.parametrize("closing_matches", [True, False])
def test_named_closing_total_preserves_a_valued_section(closing_matches: bool) -> None:
    sheet = "Balance"
    section = "Долгосрочные обязательства"
    closing = f"ИТОГО {section.upper()}" if closing_matches else "ИТОГО КАПИТАЛ"
    cells = [_c(sheet, "C1", "2024"), _c(sheet, "D1", "2025")]
    cells += [
        _c(sheet, "A2", "Баланс"),
        _c(sheet, "A3", section),
        _c(sheet, "A4", "  Займы и кредиты"),
        _c(sheet, "A5", "  Прочие обязательства"),
        _c(sheet, "A6", closing),
    ]
    for col in "CD":
        cells.extend([
            _c(sheet, f"{col}3", "17", formula=f"=Inputs!{col}33"),
            _c(sheet, f"{col}4", "10", formula=f"=Inputs!{col}34"),
            _c(sheet, f"{col}5", "7", formula=f"=Inputs!{col}35"),
            _c(sheet, f"{col}6", "17", formula=f"=Inputs!{col}36"),
        ])
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == rows[6].kind == "fact"
    path = ["Баланс", section] if closing_matches else ["Баланс"]
    assert rows[4].label_path == rows[5].label_path == path
    assert rows[6].label_path == ["Баланс"]
    assert rows[4].values == ["10", "10"]


@pytest.mark.parametrize(
    ("unit", "scale", "normalized"),
    [
        ("млн единиц", 1_000_000, ["2000000000", "3000000000"]),
        ("млн долл.", 1_000_000, ["2000000000", "3000000000"]),
        ("руб. / ед. продукции", 1, ["2000", "3000"]),
        ("руб. / куб. м", 1, ["2000", "3000"]),
        ("руб. / кВт*ч", 1, ["2000", "3000"]),
    ],
)
def test_unit_beside_name_keeps_scale_without_joining_the_name(
    unit: str, scale: int, normalized: list[str]
) -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Статья"),
        _c(sheet, "C1", "2024"),
        _c(sheet, "D1", "2025"),
        _c(sheet, "A2", "Продукция"),
        _c(sheet, "B2", unit),
        _c(sheet, "C2", "2000"),
        _c(sheet, "D2", "3000"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[2].label == "Продукция"
    assert [(cell.addr, cell.role) for cell in rows[2].cells] == [("B2", "unit")]
    assert rows[2].scale_factor == scale
    assert rows[2].normalized_values == normalized


def test_terminal_formula_keeps_the_row_a_fact_without_a_dated_value() -> None:
    sheet = "DCF"
    cells = [
        _c(sheet, "A1", "Item"),
        _c(sheet, "C1", "2024"),
        _c(sheet, "D1", "2025"),
        _c(sheet, "E1", "Terminal"),
        _c(sheet, "A2", "DCF"),
        _c(sheet, "A3", "Terminal value"),
        _c(sheet, "B3", "млн руб."),
        _c(sheet, "E3", "120", formula="=E10/(E11-E12)"),
        _c(sheet, "A4", "Period length"),
        _c(sheet, "C4", "1"),
        _c(sheet, "D4", "1"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[3].kind == "fact"
    assert rows[3].value_statuses == ["empty", "empty"]
    assert rows[4].label_path == ["DCF"]


def test_empty_inputs_continue_a_run_of_formula_generated_names() -> None:
    sheet = "Sales"
    cells = [
        _c(sheet, "C1", "2024"),
        _c(sheet, "D1", "2025"),
        _c(sheet, "A2", "Revenue"),
        _c(
            sheet,
            "A3",
            "Sales 1",
            formula='=I3&"Product 1"',
            formula_template='=R[0]C[8]&"Product 1"',
        ),
        _c(sheet, "C3", "10"),
        _c(sheet, "D3", "12"),
        _c(
            sheet,
            "A4",
            "Sales 2",
            formula='=I4&"Product 2"',
            formula_template='=R[0]C[8]&"Product 2"',
        ),
        _c(
            sheet,
            "A5",
            "Sales 3",
            formula='=I5&"Product 3"',
            formula_template='=R[0]C[8]&"Product 3"',
        ),
        _c(sheet, "A6", "Costs"),
        _c(sheet, "A7", "Materials"),
        _c(sheet, "C7", "4"),
        _c(sheet, "D7", "5"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    for n in (4, 5):
        assert rows[n].kind == "fact"
        assert rows[n].label_path == ["Revenue"]
        assert rows[n].value_statuses == ["empty", "empty"]
    assert rows[6].kind == "abstract"
    assert rows[7].label_path == ["Costs"]


def test_a_unit_formula_section_parents_the_next_indicator() -> None:
    sheet = "ОПиУ"
    cells = [
        _c(sheet, "A1", "Показатель"),
        _c(sheet, "B1", "Ед. изм."),
        _c(sheet, "C1", "2024"),
        _c(sheet, "D1", "2025"),
        _c(sheet, "A2", "Раздел"),
        _c(sheet, "B2", "тыс. руб.", formula='="тыс. руб."'),
        _c(sheet, "A3", "Выручка"),
        _c(sheet, "C3", "10"),
        _c(sheet, "D3", "12"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[2].kind == "abstract"
    assert rows[2].label == "Раздел"
    assert rows[3].kind == "fact"
    assert rows[3].label == "Выручка"
    assert rows[3].label_path == ["Раздел"]
    assert rows[3].parent_label == "Раздел"
    assert rows[3].values == ["10", "12"]


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


def test_a_valued_continuation_can_put_its_names_in_the_unit_column() -> None:
    sheet = "Outputs"
    cells = [
        _c(sheet, "A1", "Наименование"),
        _c(sheet, "B1", "Комментарий"),
        _c(sheet, "C1", "Ед. измерения"),
        _c(sheet, "D1", "2024"),
        _c(sheet, "E1", "2025"),
        _c(sheet, "A2", "Выручка"),
        _c(sheet, "C2", "тыс. руб."),
        _c(sheet, "D2", "10"),
        _c(sheet, "E2", "12"),
        _c(sheet, "C4", "ISCR"),
        _c(sheet, "D4", "2", formula="=D2/5"),
        _c(sheet, "E4", "3", formula="=E2/4"),
        _c(sheet, "C5", "%"),
        _c(sheet, "D5", "25"),
        _c(sheet, "B6", "Check"),
        _c(sheet, "D6", "10"),
        _c(sheet, "C7", "Operating Activities"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert set(rows) == {2, 4}
    assert rows[2].label == "Выручка"
    assert rows[4].label == "ISCR"
    assert rows[4].kind == "fact"
    assert rows[4].values == ["2", "3"]


def test_an_annotated_unit_sets_the_scale_without_entering_the_label() -> None:
    sheet = "CF"
    caption = "тыс. руб. (указывается со знаком '+' и сверяется с USD)"
    cells = [
        _c(sheet, "C1", "2024"),
        _c(sheet, "D1", "2025"),
        _c(sheet, "A2", "Выручка"),
        _c(sheet, "B2", caption),
        _c(sheet, "C2", "10"),
        _c(sheet, "D2", "12"),
    ]
    row = _publish(sheet, cells).blocks[0].rows[0]
    assert row.label == "Выручка"
    assert row.hints.currency == "RUB"
    assert row.scale_factor == 1000
    assert row.values == ["10", "12"]
    assert row.normalized_values == ["10000", "12000"]
    assert [(cell.role, cell.cached_value) for cell in row.cells] == [("unit", caption)]


def test_a_blank_input_with_units_and_entry_instructions_stays_a_fact() -> None:
    sheet = "Коэффициенты"
    cells = [
        _c(sheet, "A1", "Тип показателя"),
        _c(sheet, "B1", "Показатель"),
        _c(sheet, "C1", "Метод расчета"),
        _c(sheet, "E1", "2024"),
        _c(sheet, "F1", "2025"),
        _c(sheet, "A2", "Раздел"),
        _c(sheet, "D2", "тыс. руб."),
        _c(sheet, "A3", "  Корректировка для расчета EBITDA"),
        _c(sheet, "B3", "Скопировать фактические данные из заявки", formula="=A20"),
        _c(sheet, "C3", "Укажите формулу корректировки"),
        _c(sheet, "D3", "тыс. руб."),
        _c(sheet, "A4", "  EBITDA"),
        _c(sheet, "E4", "10", formula="=E5+E3"),
        _c(sheet, "F4", "12", formula="=F5+F3"),
        _c(sheet, "A5", "  Введите ставку дисконтирования"),
        _c(sheet, "D5", "%"),
        _c(sheet, "E5", "10"),
        _c(sheet, "F5", "11"),
    ]
    rows = {row.row: row for block in _publish(sheet, cells).blocks for row in block.rows}
    assert rows[2].kind == "abstract"
    assert rows[3].label == "Корректировка для расчета EBITDA"
    assert rows[3].kind == "fact"
    assert rows[3].value_statuses == ["empty", "empty"]
    assert rows[4].label_path == ["Раздел"]
    assert rows[5].label == "Введите ставку дисконтирования"
    assert rows[5].values == ["10", "11"]
