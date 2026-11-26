from __future__ import annotations

import pytest

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


def _c(
    sheet: str,
    addr: str,
    value: str | None,
    *,
    hidden: bool = False,
    formula: str | None = None,
    formula_template: str | None = None,
    number_format: str | None = None,
) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": hidden,
        "formula_raw": formula,
        "formula_template": formula_template,
        "unparsed": False,
        "number_format": number_format,
        "comment": None,
    }


def test_check_row_is_marker_not_finding() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2023"),
        _c("P&L", "C1", "2024E"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "110"),
        _c("P&L", "A3", "Проверка баланса"),
        _c("P&L", "B3", "0"),
        _c("P&L", "C3", "0"),
    ]
    layout = detect_layout(cells)
    rows = layout.sheets[0].blocks[0].rows
    checks = [r for r in rows if r.check_row]
    assert len(checks) == 1
    assert checks[0].label == "Проверка баланса"
    dumped = layout.model_dump()
    assert "findings" not in dumped
    assert "candidates" not in dumped


def test_check_stem_inside_word_is_not_check_row() -> None:
    cells = [
        _c("BS", "A1", "Item"),
        _c("BS", "B1", "2023"),
        _c("BS", "C1", "2024"),
        _c("BS", "A2", "Задолженность поставщикам, в т.ч."),
        _c("BS", "B2", "50"),
        _c("BS", "C2", "60"),
        _c("BS", "A3", "  - перед подрядчиками"),
        _c("BS", "B3", "20"),
        _c("BS", "C3", "25"),
        _c("BS", "A4", "Всего задолженность"),
        _c("BS", "B4", "50", formula="=B2"),
        _c("BS", "C4", "60", formula="=C2"),
        _c("BS", "A5", "Итог должен быть равен нулю"),
        _c("BS", "B5", "0", formula="=B4-B2"),
        _c("BS", "C5", "0", formula="=C4-C2"),
        _c("BS", "A6", "Сходимость остатков"),
        _c("BS", "B6", "0"),
        _c("BS", "C6", "0"),
    ]
    layout = detect_layout(cells)
    rows = {r.label.strip(): r for r in layout.sheets[0].blocks[0].rows}
    real = ("Задолженность поставщикам, в т.ч.", "- перед подрядчиками", "Всего задолженность")
    for label in real:
        row = rows[label]
        assert row.check_row is False, label
        assert row.kind == "fact", label
    assert rows["Итог должен быть равен нулю"].check_row is True
    assert rows["Итог должен быть равен нулю"].kind == "helper"
    assert rows["Сходимость остатков"].check_row is True
    assert rows["Сходимость остатков"].kind == "helper"


def test_check_prefixes_and_balance_words() -> None:
    """Prefixed check stems stay checks; lookalikes and должность stay facts."""
    checks = [
        "Несходимость",
        "Расходимость",
        "Перепроверка",
        "Сходимость",
        "Сходятся",
        "Сходящиеся",
        "Сумма должна сойтись",
        "Итог должно быть нулём",
        "Строки должны совпасть",
    ]
    facts = [
        "Исходящее сальдо",
        "Исходящий остаток",
        "Нисходящий поток",
        "Восходящий тренд",
        "Происходящие операции",
        "Должность",
        "Подконтрольные общества",
        "Бесконтрольно",
    ]
    cells = [
        _c("BS", "A1", "Item"),
        _c("BS", "B1", "2023"),
        _c("BS", "C1", "2024"),
    ]
    row = 2
    for label in checks:
        cells += [
            _c("BS", f"A{row}", label),
            _c("BS", f"B{row}", "0"),
            _c("BS", f"C{row}", "0"),
        ]
        row += 1
    for label in facts:
        cells += [
            _c("BS", f"A{row}", label),
            _c("BS", f"B{row}", "10"),
            _c("BS", f"C{row}", "25"),
        ]
        row += 1
    cells += [
        _c("BS", f"A{row}", "Задолженность"),
        _c("BS", f"A{row + 1}", "  перед поставщиками"),
        _c("BS", f"B{row + 1}", "15"),
        _c("BS", f"C{row + 1}", "16"),
    ]
    layout = detect_layout(cells)
    rows = {item.label: item for item in layout.sheets[0].blocks[0].rows}
    for label in checks:
        assert rows[label].check_row is True, label
        assert rows[label].kind == "helper", label
    for label in facts:
        assert rows[label].check_row is False, label
        assert rows[label].kind == "fact", label
    assert rows["Задолженность"].kind == "abstract"
    assert rows["Задолженность"].check_row is False
    assert rows["перед поставщиками"].kind == "fact"
    assert rows["перед поставщиками"].check_row is False
    assert rows["перед поставщиками"].section_path == ["Задолженность"]


def test_section_header_is_abstract_and_week_index_is_index() -> None:
    cells = [
        _c("Dash", "A1", "Item"),
        _c("Dash", "B1", "11.01.2026"),
        _c("Dash", "C1", "18.01.2026"),
        _c("Dash", "D1", "25.01.2026"),
        _c("Dash", "A2", "Week #"),
        _c("Dash", "B2", "1"),
        _c("Dash", "C2", "2"),
        _c("Dash", "D2", "3"),
        _c("Dash", "A3", "CASH INFLOWS"),
        _c("Dash", "A4", "Receipts"),
        _c("Dash", "B4", "10"),
        _c("Dash", "C4", "20"),
        _c("Dash", "D4", "30"),
        _c("Dash", "A5", "Проверка баланса"),
        _c("Dash", "B5", "0"),
        _c("Dash", "C5", "0"),
        _c("Dash", "D5", "0"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Week #"].kind == "index"
    assert rows["CASH INFLOWS"].kind == "abstract"
    assert rows["Receipts"].kind == "fact"
    assert rows["Receipts"].section_path == ["CASH INFLOWS"]
    assert rows["Receipts"].parent_row == rows["CASH INFLOWS"].row
    assert rows["Проверка баланса"].kind == "helper"


def test_total_label_and_check_left_of_axis_stay_rows() -> None:
    cells = [
        _c("BS", "B3", "Year"),
        _c("BS", "F3", "1"),
        _c("BS", "G3", "2"),
        _c("BS", "H3", "3"),
        _c("BS", "B8", "Asset"),
        _c("BS", "C8", "k£"),
        _c("BS", "F8", "10"),
        _c("BS", "G8", "11"),
        _c("BS", "H8", "12"),
        _c("BS", "B9", "Cash in hand"),
        _c("BS", "C9", "k£"),
        _c("BS", "F9", "1"),
        _c("BS", "G9", "2"),
        _c("BS", "H9", "3"),
        _c("BS", "B10", "Total"),
        _c("BS", "C10", "k£"),
        _c("BS", "F10", "11", formula="=SUM(F8:F9)"),
        _c("BS", "G10", "13", formula="=SUM(G8:G9)"),
        _c("BS", "H10", "15", formula="=SUM(H8:H9)"),
        _c("BS", "E19", "CHECK"),
        _c("BS", "F19", "1", formula="=F10-F16<0.01"),
        _c("BS", "G19", "1", formula="=G10-G16<0.01"),
        _c("BS", "H19", "1", formula="=H10-H16<0.01"),
    ]
    layout = detect_layout(cells)
    rows = {row.row: row for row in layout.sheets[0].blocks[0].rows}
    assert rows[10].label == "Total"
    assert rows[10].kind == "fact"
    assert rows[19].label == "CHECK"
    assert rows[19].kind == "helper"
    assert rows[19].check_row is True


def test_nested_label_columns_use_span_indent() -> None:
    cells = [
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "N1", "2026"),
        _c("PF", "B2", "Cashflow Statement"),
        _c("PF", "C3", "Uses of funds"),
        _c("PF", "D4", "EPC"),
        _c("PF", "L4", "10"),
        _c("PF", "M4", "20"),
        _c("PF", "N4", "30"),
        _c("PF", "D5", "Development"),
        _c("PF", "L5", "4"),
        _c("PF", "M5", "5"),
        _c("PF", "N5", "6"),
        _c("PF", "D6", "Share premium"),
        _c("PF", "L6", "1"),
        _c("PF", "M6", "1"),
        _c("PF", "N6", "1"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.label_col == 4
    rows = {r.label: r for r in block.rows}
    assert rows["Cashflow Statement"].kind == "abstract"
    assert rows["Uses of funds"].kind == "abstract"
    assert rows["EPC"].kind == "fact"
    assert rows["EPC"].section_path == ["Cashflow Statement", "Uses of funds"]
    assert rows["EPC"].label_col == 4
    assert rows["Cashflow Statement"].label_col == 2


def test_small_integers_with_formulas_are_fact() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2023"),
        _c("P&L", "C1", "2024E"),
        _c("P&L", "D1", "2025E"),
        _c("P&L", "A2", "Units"),
        _c("P&L", "B2", "1", formula="=Assumptions!B2", formula_template="=Assumptions!R[0]C[0]"),
        _c("P&L", "C2", "2", formula="=Assumptions!C2", formula_template="=Assumptions!R[0]C[0]"),
        _c("P&L", "D2", "3", formula="=Assumptions!D2", formula_template="=Assumptions!R[0]C[0]"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Units"].kind == "fact"


def test_flag_values_are_not_index() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "A2", "Construction flag"),
        _c("PF", "B2", "1"),
        _c("PF", "C2", "1"),
        _c("PF", "D2", "0"),
        _c("PF", "A3", "Revenue"),
        _c("PF", "B3", "10"),
        _c("PF", "C3", "20"),
        _c("PF", "D3", "30"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Construction flag"].kind == "flag"
    assert rows["Revenue"].kind == "fact"


def test_placeholder_and_binary_timing_are_not_facts() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "A2", "Spare"),
        _c("PF", "B2", "0"),
        _c("PF", "C2", "0"),
        _c("PF", "D2", "0"),
        _c("PF", "A3", "Construction"),
        _c("PF", "B3", "1"),
        _c("PF", "C3", "1"),
        _c("PF", "D3", "0"),
        _c("PF", "A4", "Merchant price choice"),
        _c("PF", "B4", "Mid"),
        _c("PF", "C4", "Low"),
        _c("PF", "D4", "Mid"),
        _c("PF", "A5", "Revenue"),
        _c("PF", "B5", "10"),
        _c("PF", "C5", "20"),
        _c("PF", "D5", "30"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Spare"].kind == "helper"
    assert rows["Construction"].kind == "flag"
    assert rows["Merchant price choice"].kind == "flag"
    assert rows["Revenue"].kind == "fact"


def test_scenario_and_covenant_breach_are_flags() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "A2", "Mid case"),
        _c("PF", "B2", "1"),
        _c("PF", "C2", "0"),
        _c("PF", "D2", "0"),
        _c("PF", "A3", "Low case"),
        _c("PF", "B3", "0"),
        _c("PF", "C3", "1"),
        _c("PF", "D3", "0"),
        _c("PF", "A4", "Applied (real terms)"),
        _c("PF", "B4", "1"),
        _c("PF", "C4", "1"),
        _c("PF", "D4", "1"),
        _c("PF", "A5", "Covenant breach"),
        _c("PF", "B5", "0"),
        _c("PF", "C5", "0"),
        _c("PF", "D5", "1"),
        _c("PF", "A6", "Revenue"),
        _c("PF", "B6", "10"),
        _c("PF", "C6", "20"),
        _c("PF", "D6", "30"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Mid case"].kind == "flag"
    assert rows["Low case"].kind == "flag"
    assert rows["Applied (real terms)"].kind == "flag"
    assert rows["Covenant breach"].kind == "flag"
    assert rows["Revenue"].kind == "fact"


def test_index_names_a_value_column_and_stays_a_unit_cell() -> None:
    from finance_context.layout.params import column_header_text, is_unit_text

    assert is_unit_text("Index")
    assert not is_unit_text("Base Index")
    titled = {
        5: [{"col": 7, "cached_value": "Index"}],
        6: [{"col": 7, "cached_value": "1.02"}],
    }
    assert column_header_text(titled, 7, 6, 5, False) == "Index"
    currency = {
        5: [{"col": 5, "cached_value": "EUR'000"}],
        6: [{"col": 5, "cached_value": "10"}],
    }
    assert column_header_text(currency, 5, 6, 5, False) is None


def test_prose_and_shortcut_sheets_are_rejected() -> None:
    cells = [
        _c("Cover", "A1", "Strictly confidential educational material for modelling."),
        _c(
            "Cover",
            "A2",
            "No representation or warranty of any kind is made in relation to accuracy.",
        ),
        _c(
            "Cover",
            "A3",
            "This spreadsheet is for educational purposes only and must not be relied on.",
        ),
        _c("Shortcuts", "A1", "CTRL"),
        _c("Shortcuts", "B1", "+"),
        _c("Shortcuts", "C1", "Arrow Keys"),
        _c("Shortcuts", "D1", "Move to the edge of the current data region in a worksheet."),
        _c("Shortcuts", "A2", "CTRL"),
        _c("Shortcuts", "B2", "+"),
        _c("Shortcuts", "C2", "Home"),
        _c("Shortcuts", "D2", "Move to the beginning of the worksheet."),
        _c("Shortcuts", "A3", "TAB"),
        _c("Shortcuts", "D3", "Move to the next cell within a menu window."),
    ]
    layout = detect_layout(cells)
    assert all(not sheet.blocks for sheet in layout.sheets)


def test_numbered_rows_under_a_text_header_stay_a_table() -> None:
    cells = [
        _c("FLM", "F1", "Показатели Forward-Looking Monitoring"),
        _c("FLM", "B5", "№ п/п"),
        _c("FLM", "C5", "Отрасль"),
        _c("FLM", "D5", "Показатель"),
        _c("FLM", "E5", "Источник информации"),
        _c("FLM", "H5", "Максимальное/минимальное значение на 31.12.2016"),
        _c("FLM", "I5", "Максимальное/минимальное значение на 31.12.2017"),
        _c("FLM", "J5", "Максимальное/минимальное значение на 31.12.2018"),
    ]
    for row, (number, name) in enumerate(
        [("1", "Инфляция"), ("2", "Доходы"), ("3", "EBITDA")],
        start=6,
    ):
        cells += [
            _c("FLM", f"B{row}", number),
            _c("FLM", f"C{row}", "Все"),
            _c("FLM", f"D{row}", name),
            _c("FLM", f"E{row}", "СМИ, прогноз ЦМИ"),
            _c("FLM", f"H{row}", "0.05"),
            _c("FLM", f"I{row}", "0.04"),
            _c("FLM", f"J{row}", "0.04"),
        ]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert block.kind == "params"
    assert block.label_col == 4
    assert [row.label for row in block.rows if row.kind == "fact"] == [
        "Инфляция",
        "Доходы",
        "EBITDA",
    ]
    by_text = {header.text: header.role for header in block.axis.headers}
    assert by_text["№ п/п"] == "index"
    assert by_text["Отрасль"] == "unknown"
    assert by_text["Источник информации"] == "note"
    assert by_text["Максимальное/минимальное значение на 31.12.2016"] == "value"
    assert "scenario" not in by_text.values()


def test_sensitivity_grid_keeps_title_and_numeric_rows() -> None:
    cells = [
        _c("Sensitivity", "A1", "GRID 1: MO-12 CLOSING CASH"),
        _c("Sensitivity", "B2", "0.75"),
        _c("Sensitivity", "C2", "1.00"),
        _c("Sensitivity", "D2", "1.25"),
        _c("Sensitivity", "A3", "-0.2"),
        _c("Sensitivity", "B3", "100"),
        _c("Sensitivity", "C3", "200"),
        _c("Sensitivity", "D3", "300"),
        _c("Sensitivity", "A4", "0"),
        _c("Sensitivity", "B4", "110"),
        _c("Sensitivity", "C4", "210"),
        _c("Sensitivity", "D4", "310"),
        _c("Sensitivity", "A5", "0.2"),
        _c("Sensitivity", "B5", "120"),
        _c("Sensitivity", "C5", "220"),
        _c("Sensitivity", "D5", "320"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.kind == "matrix"
    assert [row.label for row in block.rows] == ["-0.2", "0", "0.2"]
    assert all(row.section_path == ["GRID 1: MO-12 CLOSING CASH"] for row in block.rows)
    assert [header.text for header in block.axis.headers] == ["0.75", "1.00", "1.25"]


def test_timeline_stub_cells_get_roles() -> None:
    cells = [
        _c("Construction", "A1", "Item"),
        _c("Construction", "E1", "2023"),
        _c("Construction", "F1", "2024"),
        _c("Construction", "G1", "2025"),
        _c("Construction", "A2", "CAPEX"),
        _c("Construction", "C2", "300", formula="=SUM(E2:G2)"),
        _c("Construction", "E2", "100", formula_template="=RC[1]"),
        _c("Construction", "F2", "100"),
        _c("Construction", "G2", "100"),
        _c("Construction", "A3", "Equity target"),
        _c("Construction", "C3", "115", formula="=$C$2*0.35"),
        _c("Construction", "E3", "50"),
        _c("Construction", "F3", "65"),
        _c("Construction", "G3", "0"),
        _c("Construction", "A4", "Debt"),
        _c("Construction", "C4", "k£"),
        _c("Construction", "E4", "10"),
        _c("Construction", "F4", "20"),
        _c("Construction", "G4", "30"),
    ]
    layout = detect_layout(cells)
    rows = {row.label: row for row in layout.sheets[0].blocks[0].rows}
    capex_roles = {cell.role for cell in rows["CAPEX"].cells}
    assert "total" in capex_roles
    equity_roles = {cell.role for cell in rows["Equity target"].cells}
    assert "value" in equity_roles
    debt_roles = {cell.role for cell in rows["Debt"].cells}
    assert "unit" in debt_roles


def test_check_result_table_is_helper_not_junk() -> None:
    cells = [
        _c("Checks", "A1", "Check"),
        _c("Checks", "B1", "Result"),
        _c("Checks", "C1", "Note"),
        _c("Checks", "A2", "Balance sheet ties"),
        _c("Checks", "B2", "1"),
        _c("Checks", "C2", "assets vs equity plus debt"),
        _c("Checks", "A3", "Cashflow ties"),
        _c("Checks", "B3", "0"),
        _c("Checks", "A4", "Sources equal uses"),
        _c("Checks", "B4", "1"),
        _c("Checks", "A5", "Debt schedule ties"),
        _c("Checks", "B5", "1"),
    ]
    layout = detect_layout(cells)
    rows = layout.sheets[0].blocks[0].rows
    assert len(rows) >= 3
    assert all(row.check_row or row.kind == "helper" for row in rows)


def test_graph_selects_active_value_column() -> None:
    cells = [
        _c("Input Assumptions", "B5", "Parameter"),
        _c("Input Assumptions", "C5", "Base"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "B8", "Gearing"),
        _c("Input Assumptions", "C8", "0.7"),
        _c("Input Assumptions", "D8", "0.65"),
        _c("Input Assumptions", "B9", "Tax Rate"),
        _c("Input Assumptions", "C9", "0.3"),
        _c("Input Assumptions", "D9", "0.3"),
        _c("Input Assumptions", "B10", "Duration"),
        _c("Input Assumptions", "C10", "30"),
        _c("Input Assumptions", "D10", "40"),
        _c("Input Assumptions", "B11", "Toll"),
        _c("Input Assumptions", "C11", "3"),
        _c("Input Assumptions", "D11", "3.56"),
    ]
    edges = [
        {"source": "Construction!E21", "target": "Input Assumptions!D8"},
        {"source": "P&L!C9", "target": "Input Assumptions!D9"},
        {"source": "CFS!C10", "target": "Input Assumptions!D10"},
    ]
    layout = detect_layout(cells, edges=edges)
    block = layout.sheets[0].blocks[0]
    headers = {header.col: (header.role, header.text) for header in block.axis.headers}
    assert headers[3] == ("value", "Base")
    assert headers[4] == ("value", "Values")


def test_graph_selects_active_value_column_from_parquet(tmp_path) -> None:
    """The same column, read from ir/edges.parquet instead of a resident list."""
    from finance_context.formulas.stage import IR_EDGE_COLUMNS
    from finance_context.store.fs import write_parquet

    cells = [
        _c("Input Assumptions", "B5", "Parameter"),
        _c("Input Assumptions", "C5", "Base"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "B8", "Gearing"),
        _c("Input Assumptions", "C8", "0.7"),
        _c("Input Assumptions", "D8", "0.65"),
        _c("Input Assumptions", "B9", "Tax Rate"),
        _c("Input Assumptions", "C9", "0.3"),
        _c("Input Assumptions", "D9", "0.3"),
        _c("Input Assumptions", "B10", "Duration"),
        _c("Input Assumptions", "C10", "30"),
        _c("Input Assumptions", "D10", "40"),
        _c("Input Assumptions", "B11", "Toll"),
        _c("Input Assumptions", "C11", "3"),
        _c("Input Assumptions", "D11", "3.56"),
    ]
    path = tmp_path / "edges.parquet"
    write_parquet(
        path,
        IR_EDGE_COLUMNS,
        [
            ("Construction!E21", "ref", "Input Assumptions!D8", False, False, False),
            ("P&L!C9", "ref", "Input Assumptions!D9", False, False, False),
            ("CFS!C10", "ref", "Input Assumptions!D10", False, False, False),
        ],
    )
    layout = detect_layout(cells, edges=path)
    block = layout.sheets[0].blocks[0]
    headers = {header.col: (header.role, header.text) for header in block.axis.headers}
    assert headers[3] == ("value", "Base")
    assert headers[4] == ("value", "Values")


def test_unit_caption_does_not_replace_the_debt_section() -> None:
    cells = [
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "B2", "Senior Debt"),
        _c("PF", "C3", "Linear repayment"),
        _c("PF", "B4", "% p.a."),
        _c("PF", "D5", "Balance b/f"),
        _c("PF", "L5", "10"),
        _c("PF", "D6", "Principal repayment"),
        _c("PF", "L6", "2"),
        _c("PF", "D7", "Balance c/f"),
        _c("PF", "L7", "8"),
    ]
    layout = detect_layout(cells)
    rows = {row.label: row for row in layout.sheets[0].blocks[0].rows}
    assert "Linear repayment" in rows["Balance b/f"].section_path
    assert "% p.a." not in rows["Principal repayment"].section_path
    assert rows["Principal repayment"].parent_row == rows["Linear repayment"].row
    assert rows["Balance c/f"].parent_row == rows["Linear repayment"].row


def test_totals_take_the_asset_section_as_parent() -> None:
    cells = [
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "B2", "Balance Sheet"),
        _c("PF", "C3", "Non-current assets"),
        _c("PF", "C4", "Total"),
        _c("PF", "L4", "10"),
        _c("PF", "C5", "Current assets"),
        _c("PF", "C6", "Total"),
        _c("PF", "L6", "4"),
    ]
    layout = detect_layout(cells)
    totals = [row for row in layout.sheets[0].blocks[0].rows if row.label == "Total"]
    by_section = {tuple(row.section_path): row for row in totals}
    noncurrent = by_section[("Balance Sheet", "Non-current assets")]
    current = by_section[("Balance Sheet", "Current assets")]
    labels = {row.row: row.label for row in layout.sheets[0].blocks[0].rows}
    assert labels[noncurrent.parent_row] == "Non-current assets"
    assert labels[current.parent_row] == "Current assets"


def test_rub_unit_column_cells_are_units() -> None:
    cells = [
        _c("Модель", "B3", "Показатель"),
        _c("Модель", "C3", "Ед. изм."),
        _c("Модель", "D3", "2025"),
        _c("Модель", "E3", "2026"),
        _c("Модель", "F3", "2027"),
        _c("Модель", "B4", "Выручка"),
        _c("Модель", "C4", "тыс. руб."),
        _c("Модель", "D4", "100"),
        _c("Модель", "E4", "110"),
        _c("Модель", "F4", "121"),
        _c("Модель", "B5", "Капзатраты"),
        _c("Модель", "C5", "млрд руб."),
        _c("Модель", "D5", "5"),
        _c("Модель", "E5", "6"),
        _c("Модель", "F5", "7"),
    ]
    layout = detect_layout(cells)
    rows = {row.label: row for row in layout.sheets[0].blocks[0].rows}
    assert [(cell.col, cell.role) for cell in rows["Выручка"].cells] == [(3, "unit")]
    assert [(cell.col, cell.role) for cell in rows["Капзатраты"].cells] == [(3, "unit")]


@pytest.mark.parametrize(
    "text",
    [
        "тыс. руб.",
        "тыс.р.",
        "млн.р.",
        "млрд.р.",
        "т. руб.",
        "т.р.",
        "тысячи руб.",
        "тысяч рублей",
        "р.",
    ],
)
def test_rub_unit_captions_are_unit_text(text: str) -> None:
    from finance_context.layout.params import is_unit_text

    assert is_unit_text(text)


def test_plan_fact_and_scenario_labels_stay_rows_under_a_year_axis() -> None:
    labels = ["План", "Факт", "Отклонение", "Budget", "Base case"]
    cells = [_c("ПФ", "A2", "Показатель")]
    years = ["2023", "2024", "2025"]
    cells += [_c("ПФ", f"{col}2", y) for col, y in zip("BCD", years, strict=True)]
    for r, label in enumerate(labels, start=3):
        cells.append(_c("ПФ", f"A{r}", label))
        cells += [_c("ПФ", f"{col}{r}", str(r + i)) for i, col in enumerate("BCD")]
    rows = detect_layout(cells).sheets[0].blocks[0].rows
    assert [row.label for row in rows] == labels


def test_a_banner_higher_up_does_not_pull_the_previous_title_down() -> None:
    cells = [
        _c("P&L", "A1", "Upper"),
        _c("P&L", "B2", "2020"),
        _c("P&L", "C2", "2021"),
        _c("P&L", "A3", "Sales"),
        _c("P&L", "B3", "1"),
        _c("P&L", "C3", "2"),
        _c("P&L", "B4", "Remark"),
        _c("P&L", "A5", "Between"),
        _c("P&L", "B7", "Факт"),
        _c("P&L", "C7", "План"),
        _c("P&L", "B8", "2024"),
        _c("P&L", "C8", "2025"),
        _c("P&L", "A9", "Costs"),
        _c("P&L", "B9", "3"),
        _c("P&L", "C9", "4"),
    ]
    blocks = detect_layout(cells).sheets[0].blocks
    labels = [[row.label for row in block.rows] for block in blocks]
    upper = next(rows for rows in labels if "Sales" in rows)
    assert upper[:2] == ["Upper", "Sales"]
    lower = next(rows for rows in labels if "Costs" in rows)
    assert "Upper" not in lower
    assert "Between" in lower


def test_section_title_above_a_fact_banner_stays_on_the_axis() -> None:
    """A fact/plan banner is not a header layer, and titles above it stay put."""
    cells = [
        _c("P&L", "A1", "Operating"),
        _c("P&L", "B2", "Факт"),
        _c("P&L", "C2", "План"),
        _c("P&L", "B3", "2024"),
        _c("P&L", "C3", "2025"),
        _c("P&L", "A4", "Sales"),
        _c("P&L", "B4", "10"),
        _c("P&L", "C4", "12"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [axis.header_row for axis in sheet.axes] == [3]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2024", "2025"]
    labels = [row.label for block in sheet.blocks for row in block.rows]
    assert labels[0] == "Operating"
    assert "Sales" in labels
    assert "Факт" not in labels


def test_word_plus_year_in_the_label_column_stays_a_row_name() -> None:
    cells = [_c("ПФ", "A2", "Показатель")]
    years = ["2023", "2024", "2025"]
    cells += [_c("ПФ", f"{col}2", y) for col, y in zip("BCD", years, strict=True)]
    cells += [_c("ПФ", "A3", "Выручка")]
    cells += [_c("ПФ", f"{col}3", v) for col, v in zip("BCD", ["1", "2", "3"], strict=True)]
    cells += [_c("ПФ", "A4", "Revenue 2024")]
    cells += [_c("ПФ", f"{col}4", v) for col, v in zip("BCD", ["4", "5", "6"], strict=True)]
    sheet = detect_layout(cells).sheets[0]
    assert [axis.header_row for axis in sheet.axes] == [2]
    assert [period.period_key for period in sheet.axes[0].periods] == years
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "Revenue 2024"]


def test_year_in_the_label_column_is_still_not_a_row_name() -> None:
    cells = [_c("ПФ", "A2", "Показатель")]
    years = ["2023", "2024", "2025"]
    cells += [_c("ПФ", f"{col}2", y) for col, y in zip("BCD", years, strict=True)]
    cells += [_c("ПФ", "A3", "Выручка")]
    cells += [_c("ПФ", f"{col}3", v) for col, v in zip("BCD", ["1", "2", "3"], strict=True)]
    cells += [_c("ПФ", "A4", "2024")]
    cells += [_c("ПФ", f"{col}4", v) for col, v in zip("BCD", ["4", "5", "6"], strict=True)]
    rows = detect_layout(cells).sheets[0].blocks[0].rows
    assert "2024" not in [row.label for row in rows]


def _flat_list(second: list[dict]) -> list[dict]:
    cells = [_c("Notes", "A1", "Item")]
    years = ["2023", "2024", "2025"]
    cells += [_c("Notes", f"{col}1", y) for col, y in zip("CDE", years, strict=True)]
    cells += [_c("Notes", "A2", "Widgets owed")] + second
    cells += [_c("Notes", "A3", "Gadgets owed")]
    cells += [_c("Notes", f"{col}3", v) for col, v in zip("CDE", ["5", "6", "7"], strict=True)]
    cells += [_c("Notes", "A4", "Gizmos owed")]
    cells += [_c("Notes", f"{col}4", v) for col, v in zip("CDE", ["8", "9", "10"], strict=True)]
    return cells


def test_dash_placeholders_make_an_empty_line_not_a_section_title() -> None:
    dashes = [_c("Notes", f"{col}2", "-") for col in "CDE"]
    rows = {row.label: row for row in detect_layout(_flat_list(dashes)).sheets[0].blocks[0].rows}
    assert rows["Widgets owed"].kind == "fact"
    assert rows["Gadgets owed"].parent_row is None
    assert rows["Gadgets owed"].section_path == []
    assert rows["Gizmos owed"].parent_row is None


def test_a_formula_beside_the_label_makes_an_empty_line_not_a_section_title() -> None:
    blank = [_c("Notes", "B2", None, formula='IF(ISERROR(Z2),"",Z2)')]
    rows = {row.label: row for row in detect_layout(_flat_list(blank)).sheets[0].blocks[0].rows}
    assert rows["Widgets owed"].kind == "fact"
    assert rows["Gadgets owed"].parent_row is None
    assert rows["Gizmos owed"].section_path == []


def test_a_label_only_row_still_opens_a_section_at_the_same_indent() -> None:
    rows = {row.label: row for row in detect_layout(_flat_list([])).sheets[0].blocks[0].rows}
    title = rows["Widgets owed"]
    assert title.kind == "abstract"
    assert rows["Gadgets owed"].parent_row == title.row
    assert rows["Gizmos owed"].section_path == ["Widgets owed"]


def test_a_formula_that_prints_period_captions_keeps_a_section_title() -> None:
    cells = _flat_list([_c("Notes", "B2", "2 кв. 2024", formula="C1")])
    rows = {row.label: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows["Widgets owed"].kind == "abstract"
    assert rows["Gadgets owed"].parent_row == rows["Widgets owed"].row


def _years(sheet: str, row: int, cols: str) -> list[dict]:
    years = ["2024", "2025"]
    return [
        _c(sheet, f"{col}{row}", year) for col, year in zip(cols, years, strict=True)
    ]


def test_label_joins_a_coefficient_and_the_indicator_name() -> None:
    cells = [_c("ОПиУ", "A1", "Группа"), _c("ОПиУ", "B1", "Показатель")]
    cells += _years("ОПиУ", 1, "DE")
    cells += [
        _c("ОПиУ", "A2", "Коэффициент"),
        _c("ОПиУ", "B2", "Текущая ликвидность"),
        _c("ОПиУ", "D2", "1.2"),
        _c("ОПиУ", "E2", "1.4"),
    ]
    rows = detect_layout(cells).sheets[0].blocks[0].rows
    assert rows[0].label == "Коэффициент Текущая ликвидность"


def test_far_left_caption_does_not_hide_the_name_beside_the_values() -> None:
    """Hugenson «Затраты»: B29 is a banner and F29 is the line name «Приход»."""
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
        _c("Затраты", "B31", banner),
        _c("Затраты", "H31", "14"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[28].label == "Расход"
    assert rows[29].label == "Приход"
    assert rows[31].label == banner


def test_formula_cache_text_is_the_label_and_other_caches_are_not() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A2", None, formula='=B10'),
        _c("ОПиУ", "B2", "Проценты к получению", formula="=B10"),
        _c("ОПиУ", "C2", "5"),
        _c("ОПиУ", "D2", "6"),
        _c("ОПиУ", "B3", None, formula="=B11"),
        _c("ОПиУ", "C3", "1"),
        _c("ОПиУ", "A4", "Статья"),
        _c("ОПиУ", "B4", "#N/A", formula="=B12"),
        _c("ОПиУ", "C4", "2"),
        _c("ОПиУ", "A5", "Сумма"),
        _c("ОПиУ", "B5", "3000", formula="=B13"),
        _c("ОПиУ", "C5", "3"),
        _c("ОПиУ", "A6", "Выручка"),
        _c("ОПиУ", "B6", "Выручка"),
        _c("ОПиУ", "C6", "тыс. руб."),
        _c("ОПиУ", "D6", "4"),
        _c("ОПиУ", "A7", "Выручка"),
        _c("ОПиУ", "B7", "Факт"),
        _c("ОПиУ", "C7", "8"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Проценты к получению"
    assert 3 not in rows
    assert rows[4].label == "Статья"
    assert rows[5].label == "Сумма"
    assert "3000" not in rows[5].label
    assert rows[6].label == "Выручка"
    assert "тыс. руб." not in rows[6].label
    assert rows[7].label == "Выручка"


def test_a_note_column_stays_out_of_the_name_and_the_row_stays_fact() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "Notes"),
        _c("P&L", "C1", "2024"),
        _c("P&L", "D1", "2025"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "Check"),
        _c("P&L", "C2", "10"),
        _c("P&L", "D2", "11"),
    ]
    row = detect_layout(cells).sheets[0].blocks[0].rows[0]
    assert row.label == "Revenue"
    assert row.kind == "fact"


def test_a_named_second_column_survives_extra_rows() -> None:
    cells = [_c("ОПиУ", "A1", "Группа"), _c("ОПиУ", "B1", "Показатель")]
    cells += _years("ОПиУ", 1, "DE")
    cells += [
        _c("ОПиУ", "A2", "Коэффициент"),
        _c("ОПиУ", "B2", "Текущая ликвидность"),
        _c("ОПиУ", "D2", "1.2"),
        _c("ОПиУ", "E2", "1.4"),
        _c("ОПиУ", "A3", "Выручка"),
        _c("ОПиУ", "D3", "10"),
        _c("ОПиУ", "E3", "11"),
        _c("ОПиУ", "A4", "Себестоимость"),
        _c("ОПиУ", "D4", "4"),
        _c("ОПиУ", "E4", "5"),
        _c("ОПиУ", "A5", "Прибыль"),
        _c("ОПиУ", "D5", "6"),
        _c("ОПиУ", "E5", "7"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Коэффициент Текущая ликвидность"


def test_a_formula_cache_beside_the_title_is_not_a_second_name() -> None:
    """A text cache in the next column is a note, not part of the section name."""
    cells = [_c("CF", "C1", "2024"), _c("CF", "D1", "2025")]
    cells += [
        _c("CF", "A2", "Section"),
        _c("CF", "B2", "Operating Activities", formula="=A10"),
        _c("CF", "A3", "Revenue"),
        _c("CF", "C3", "10"),
        _c("CF", "D3", "11"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Section"
    assert rows[2].kind == "abstract"
    assert rows[3].section_path == ["Section"]


def test_modern_formula_errors_stay_out_of_the_label() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A2", "Revenue"),
        _c("ОПиУ", "B2", "#SPILL!", formula="=B10"),
        _c("ОПиУ", "C2", "5"),
        _c("ОПиУ", "A3", "Costs"),
        _c("ОПиУ", "B3", "#CALC!", formula="=B11"),
        _c("ОПиУ", "C3", "1"),
        _c("ОПиУ", "A4", "Tax"),
        _c("ОПиУ", "B4", "#N/A", formula="=B12"),
        _c("ОПиУ", "C4", "2"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Revenue"
    assert rows[3].label == "Costs"
    assert rows[4].label == "Tax"
    from finance_context.layout.rows import _name_columns

    row = [
        _c("Sheet", "A2", "Выручка"),
        _c("Sheet", "C2", "1"),
        _c("Sheet", "F2", "Себестоимость"),
        _c("Sheet", "H2", "3"),
    ]
    assert _name_columns([1], set(), row) == [1]
    assert _name_columns([6], set(), row) == [6]


def test_one_note_in_a_short_table_stays_out_of_the_name() -> None:
    cells = [
        _c("ОПиУ", "A1", "Показатель"),
        _c("ОПиУ", "C1", "2024"),
        _c("ОПиУ", "D1", "2025"),
        _c("ОПиУ", "A2", "Revenue"),
        _c("ОПиУ", "B2", "See agreement"),
        _c("ОПиУ", "C2", "10"),
        _c("ОПиУ", "D2", "11"),
        _c("ОПиУ", "A3", "Costs"),
        _c("ОПиУ", "C3", "4"),
        _c("ОПиУ", "D3", "5"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Revenue"
    assert rows[3].label == "Costs"


def test_one_formula_note_in_a_short_table_stays_out_of_the_name() -> None:
    cells = [
        _c("ОПиУ", "A1", "Показатель"),
        _c("ОПиУ", "C1", "2024"),
        _c("ОПиУ", "D1", "2025"),
        _c("ОПиУ", "A2", "Revenue"),
        _c("ОПиУ", "B2", "See agreement", formula="=Z2"),
        _c("ОПиУ", "C2", "10"),
        _c("ОПиУ", "D2", "11"),
        _c("ОПиУ", "A3", "Costs"),
        _c("ОПиУ", "C3", "4"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Revenue"
    assert rows[2].kind == "fact"


def test_distinct_notes_without_a_header_stay_out_of_the_name() -> None:
    cells = [
        _c("ОПиУ", "A1", "Показатель"),
        _c("ОПиУ", "C1", "2024"),
        _c("ОПиУ", "D1", "2025"),
        _c("ОПиУ", "A2", "Выручка"),
        _c("ОПиУ", "B2", "см. договор"),
        _c("ОПиУ", "C2", "10"),
        _c("ОПиУ", "D2", "11"),
        _c("ОПиУ", "A3", "Себестоимость"),
        _c("ОПиУ", "B3", "без НДС"),
        _c("ОПиУ", "C3", "4"),
        _c("ОПиУ", "D3", "5"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Выручка"
    assert rows[3].label == "Себестоимость"
    assert rows[2].kind == "fact"
    assert rows[3].kind == "fact"


def test_an_aggregate_formula_parents_only_the_rows_it_sums() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A2", "Инвестиции"),
        _c("ОПиУ", "A3", "CAPEX"),
        _c("ОПиУ", "C3", "30", formula="=C4+C5"),
        _c("ОПиУ", "D3", "40", formula="=D4+D5"),
        _c("ОПиУ", "A4", "  IaaS CAPEX"),
        _c("ОПиУ", "C4", "18"),
        _c("ОПиУ", "D4", "20"),
        _c("ОПиУ", "A5", "  DC CAPEX"),
        _c("ОПиУ", "C5", "12"),
        _c("ОПиУ", "D5", "20"),
        _c("ОПиУ", "A6", "  Прочее"),
        _c("ОПиУ", "C6", "1"),
        _c("ОПиУ", "D6", "1"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[3].kind == "fact"
    assert rows[4].parent_row == 3
    assert rows[5].parent_row == 3
    assert rows[4].section_path == ["Инвестиции"]
    assert rows[5].section_path == ["Инвестиции"]
    assert rows[6].parent_row == 2
    assert rows[6].section_path == ["Инвестиции"]


def test_a_repayment_subtotal_without_an_outline_parents_its_lines() -> None:
    """Auchan «Денежные потоки»!54 sums !55+!56 in the same period column, with no group."""
    cells = [_c("Денежные потоки", "C1", "2024"), _c("Денежные потоки", "D1", "2025")]
    cells += [
        _c("Денежные потоки", "A2", "Финансовый денежный поток"),
        _c("Денежные потоки", "A3", "Погашение обязательств, за кв."),
        _c("Денежные потоки", "C3", "0", formula="=C4+C5"),
        _c("Денежные потоки", "D3", "0", formula="=D4+D5"),
        _c("Денежные потоки", "A4", "  Текущих обязательств в Сбербанке"),
        _c("Денежные потоки", "C4", "1"),
        _c("Денежные потоки", "D4", "2"),
        _c("Денежные потоки", "A5", "  Текущих обязательств в других банках"),
        _c("Денежные потоки", "C5", "3"),
        _c("Денежные потоки", "D5", "4"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[3].kind == "fact"
    assert rows[4].parent_row == 3
    assert rows[5].parent_row == 3
    assert rows[4].section_path == ["Финансовый денежный поток"]
    assert rows[5].section_path == ["Финансовый денежный поток"]


def test_a_balance_subtotal_without_an_outline_parents_its_lines() -> None:
    """VEB BS_PL_DCF!4 is SUM of !5:!6. Outline level is 0; the subtotal still parents."""
    cells = [_c("BS_PL_DCF", "C1", "2018"), _c("BS_PL_DCF", "D1", "2019")]
    cells += [
        _c("BS_PL_DCF", "A2", "Бухгалтерский баланс"),
        _c("BS_PL_DCF", "A3", "Необоротные активы"),
        _c("BS_PL_DCF", "C3", "260000", formula="=SUM(C4:C5)"),
        _c("BS_PL_DCF", "D3", "252866", formula="=SUM(D4:D5)"),
        _c("BS_PL_DCF", "A4", "  Основные средства"),
        _c("BS_PL_DCF", "C4", "250000"),
        _c("BS_PL_DCF", "D4", "242866"),
        _c("BS_PL_DCF", "A5", "  Прочее"),
        _c("BS_PL_DCF", "C5", "10000"),
        _c("BS_PL_DCF", "D5", "10000"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[3].kind == "fact"
    assert rows[4].parent_row == 3
    assert rows[5].parent_row == 3
    assert rows[4].section_path == ["Бухгалтерский баланс"]
    assert rows[5].section_path == ["Бухгалтерский баланс"]


def test_a_sum_down_a_parallel_column_does_not_parent_local_rows() -> None:
    """Same physical rows, another column: the sum is not this table's parent."""
    cells = [_c("BS", "C1", "2018"), _c("BS", "D1", "2019")]
    cells += [
        _c("BS", "A2", "Бухгалтерский баланс"),
        _c("BS", "A3", "Необоротные активы"),
        _c("BS", "C3", "1", formula="=SUM(H4:H5)"),
        _c("BS", "D3", "2", formula="=SUM(I4:I5)"),
        _c("BS", "A4", "  Основные средства"),
        _c("BS", "C4", "10"),
        _c("BS", "D4", "11"),
        _c("BS", "A5", "  Прочее"),
        _c("BS", "C5", "3"),
        _c("BS", "D5", "4"),
        _c("BS", "H4", "250000"),
        _c("BS", "H5", "10000"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[4].parent_row == 2
    assert rows[5].parent_row == 2
    assert rows[4].section_path == ["Бухгалтерский баланс"]


def test_a_sum_on_another_sheet_does_not_parent_local_rows() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A2", "Investments"),
        _c("ОПиУ", "A3", "CAPEX"),
        _c("ОПиУ", "C3", "30", formula="=SUM(Inputs!C4:C5)"),
        _c("ОПиУ", "D3", "40", formula="=SUM(Inputs!D4:D5)"),
        _c("ОПиУ", "A4", "  IaaS CAPEX"),
        _c("ОПиУ", "C4", "18"),
        _c("ОПиУ", "D4", "20"),
        _c("ОПиУ", "A5", "  DC CAPEX"),
        _c("ОПиУ", "C5", "12"),
        _c("ОПиУ", "D5", "14"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[3].kind == "fact"
    assert rows[4].parent_row == 2
    assert rows[5].parent_row == 2
    assert rows[4].section_path == ["Investments"]
    assert rows[5].section_path == ["Investments"]


def test_a_local_sum_range_parents_only_its_rows() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A2", "Investments"),
        _c("ОПиУ", "A3", "CAPEX"),
        _c("ОПиУ", "C3", "30", formula="=SUM(C4:C5)"),
        _c("ОПиУ", "D3", "40", formula="=SUM(ОПиУ!D4:D5)"),
        _c("ОПиУ", "A4", "  IaaS CAPEX"),
        _c("ОПиУ", "C4", "18"),
        _c("ОПиУ", "D4", "20"),
        _c("ОПиУ", "A5", "  DC CAPEX"),
        _c("ОПиУ", "C5", "12"),
        _c("ОПиУ", "D5", "14"),
        _c("ОПиУ", "A6", "  Прочее"),
        _c("ОПиУ", "C6", "1"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[4].parent_row == 3
    assert rows[5].parent_row == 3
    assert rows[6].parent_row == 2


def test_a_full_column_sum_keeps_its_bounds() -> None:
    from finance_context.layout.rows import _vertical_aggregate_rows

    spans = _vertical_aggregate_rows("=SUM(C1:C1048576)", 6, "ОПиУ", 3).spans
    assert spans == ((1, 1048576),)
    assert _vertical_aggregate_rows("=SUM(Inputs!C4:C5)", 3, "ОПиУ", 3).spans == ()
    assert _vertical_aggregate_rows("=SUM('ОПиУ'!C4:C5)", 3, "ОПиУ", 3).spans == ((4, 5),)
    assert (
        _vertical_aggregate_rows('=COUNTIF(Inputs!C4:C5,"SUM(C4:C5)")', 3, "ОПиУ", 3).spans == ()
    )
    assert _vertical_aggregate_rows("=SUM(C4:C5)+Inputs!C8", 3, "ОПиУ", 3).spans == ((4, 5),)
    assert _vertical_aggregate_rows("=IF(SUM(C4:C5)>0,C10,C11)", 3, "ОПиУ", 3).spans == ()
    assert _vertical_aggregate_rows("=SUM(C4,C5)", 3, "ОПиУ", 3).spans == ((4, 4), (5, 5))
    assert _vertical_aggregate_rows("=SUM(Inputs!C8,C4:C5)", 3, "ОПиУ", 3).spans == ((4, 5),)
    assert _vertical_aggregate_rows("=SUM(H4:H5)", 3, "ОПиУ", 3).spans == ()


def test_a_unit_formula_keeps_a_section_and_parents_the_next_line() -> None:
    cells = [
        _c("ОПиУ", "A1", "Показатель"),
        _c("ОПиУ", "B1", "Ед. изм."),
        _c("ОПиУ", "C1", "2024"),
        _c("ОПиУ", "D1", "2025"),
        _c("ОПиУ", "A2", "Раздел"),
        _c("ОПиУ", "B2", "тыс. руб.", formula='="тыс. руб."'),
        _c("ОПиУ", "A3", "Выручка"),
        _c("ОПиУ", "C3", "10"),
        _c("ОПиУ", "D3", "12"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].label == "Раздел"
    assert rows[2].kind == "abstract"
    assert rows[3].parent_row == 2
    assert rows[3].section_path == ["Раздел"]
    assert rows[3].kind == "fact"


def test_a_unit_formula_without_a_header_stays_a_section_via_the_stub() -> None:
    cells = [
        _c("ОПиУ", "A1", "Показатель"),
        _c("ОПиУ", "C1", "2024"),
        _c("ОПиУ", "D1", "2025"),
        _c("ОПиУ", "A2", "Раздел"),
        _c("ОПиУ", "B2", "тыс. руб.", formula='="тыс. руб."'),
        _c("ОПиУ", "A3", "Выручка"),
        _c("ОПиУ", "C3", "10"),
        _c("ОПиУ", "D3", "12"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[2].kind == "abstract"
    assert any(cell.role == "unit" for cell in rows[2].cells)
    assert rows[3].parent_row == 2
    assert rows[3].section_path == ["Раздел"]


def test_an_empty_period_formula_still_marks_a_data_row() -> None:
    cells = [
        _c("ОПиУ", "A1", "Показатель"),
        _c("ОПиУ", "B1", "Ед. изм."),
        _c("ОПиУ", "C1", "2024"),
        _c("ОПиУ", "D1", "2025"),
        _c("ОПиУ", "A2", "Выручка"),
        _c("ОПиУ", "B2", "тыс. руб.", formula='="тыс. руб."'),
        _c("ОПиУ", "C2", None, formula="=C9"),
        _c("ОПиУ", "D2", None, formula="=D9"),
    ]
    row = detect_layout(cells).sheets[0].blocks[0].rows[0]
    assert row.label == "Выручка"
    assert row.kind == "fact"


def test_a_valued_total_is_not_the_parent_of_the_next_lines() -> None:
    cells = [_c("ОПиУ", "C1", "2024"), _c("ОПиУ", "D1", "2025")]
    cells += [
        _c("ОПиУ", "A12", "Валовая прибыль"),
        _c("ОПиУ", "C12", "100"),
        _c("ОПиУ", "D12", "110"),
        _c("ОПиУ", "A13", "  Коммерческие расходы"),
        _c("ОПиУ", "C13", "10"),
        _c("ОПиУ", "A14", "  Управленческие расходы"),
        _c("ОПиУ", "C14", "20"),
        _c("ОПиУ", "A15", "Прибыль (убыток) от реализации"),
        _c("ОПиУ", "C15", "70"),
        _c("ОПиУ", "D15", "80"),
        _c("ОПиУ", "A16", "  Проценты к получению", formula=None),
        _c("ОПиУ", "C16", "5"),
        _c("ОПиУ", "A20", "Прибыль до налогообложения"),
        _c("ОПиУ", "C20", None, formula="=C15+C16"),
        _c("ОПиУ", "A21", "  Налог"),
        _c("ОПиУ", "C21", "2"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[15].kind == "fact"
    assert rows[16].section_path == []
    assert rows[16].parent_row is None
    assert rows[13].parent_row is None
    assert rows[20].kind == "fact"
    assert rows[21].parent_row is None
    assert rows[21].section_path == []


def test_blank_prochie_is_not_a_section_and_a_real_one_is() -> None:
    cells = [_c("BS", "C1", "2024"), _c("BS", "D1", "2025")]
    cells += [
        _c("BS", "A2", "Прочие"),
        _c("BS", "A3", "Капитал"),
        _c("BS", "C3", "10"),
        _c("BS", "A4", "Займы"),
        _c("BS", "C4", "20"),
        _c("BS", "A6", "Прочие"),
        _c("BS", "A7", "  Прочие доходы"),
        _c("BS", "C7", "3"),
    ]
    rows = {row.row: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    assert rows[3].section_path == []
    assert rows[3].parent_row is None
    assert rows[4].section_path == []
    assert rows[7].section_path == ["Прочие"]
    assert rows[7].parent_row == 6
