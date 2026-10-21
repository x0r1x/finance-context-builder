"""Geometry suffixes. Each case names the keys the sheet must produce."""

from __future__ import annotations

from finance_context.excel.a1 import index_to_col
from finance_context.layout.detect import detect_layout
from finance_context.layout.periods import classify_header, many_dates


def _cell(
    addr: str,
    value: str | None,
    *,
    formula: str | None = None,
    merge_end_col: int | None = None,
) -> dict:
    from finance_context.excel.a1 import parse_addr

    col, row = parse_addr(addr)
    cell = {
        "sheet": "P&L",
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": formula,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }
    if merge_end_col is not None:
        cell["merge_end_col"] = merge_end_col
    return cell


def _sheet(
    headers: list[str],
    *,
    banner: list[tuple[int, str, int | None]] | None = None,
    formulas: dict[int, str] | None = None,
    rows: list[list[str]] | None = None,
    flag: list[str] | None = None,
) -> list[dict]:
    """Columns start at B. ``banner`` is (column, text, merge end) on the row above."""
    header_row = 2 if banner or flag else 1
    if banner:
        header_row = 2
    cells = [_cell("A1" if header_row == 1 else f"A{header_row}", "Item")]
    for offset, text in enumerate(headers):
        col = 2 + offset
        cells.append(_cell(f"{index_to_col(col)}{header_row}", text))
    if banner:
        for col, text, end in banner:
            cells.append(_cell(f"{index_to_col(col)}1", text, merge_end_col=end))
    body = rows or [["10", "21", "33", "47", "58", "69"][: len(headers)]]
    row_n = header_row + 1
    if flag is not None:
        cells.append(_cell(f"A{row_n}", "Flag"))
        for offset, bit in enumerate(flag):
            cells.append(_cell(f"{index_to_col(2 + offset)}{row_n}", bit))
        row_n += 1
    for values in body:
        cells.append(_cell(f"A{row_n}", "Revenue"))
        for offset, value in enumerate(values):
            col = 2 + offset
            formula = (formulas or {}).get(col)
            cells.append(
                _cell(
                    f"{index_to_col(col)}{row_n}",
                    value,
                    formula=formula,
                )
            )
        row_n += 1
    return cells


def _keys(cells: list[dict]) -> list[str]:
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes, "expected a time axis"
    return [period.period_key for period in sheet.axes[0].periods]


def _notes(cells: list[dict]) -> list[str]:
    return list(detect_layout(cells).sheets[0].axes[0].notes)


def test_tail_splits_a_repeated_year() -> None:
    cells = _sheet(["2024 факт", "2024 план"])
    assert _keys(cells) == ["2024@факт", "2024@план"]


def test_merged_banner_stops_at_the_merge() -> None:
    cells = _sheet(
        ["2024", "2024", "2024", "2024"],
        banner=[(2, "Факт", 3), (5, "План", 5)],
    )
    assert _keys(cells) == ["2024@факт", "2024@факт", "2024", "2024@план"]


def test_unmerged_banner_fills_until_the_next_text() -> None:
    cells = _sheet(
        ["2024", "2024", "2024", "2024"],
        banner=[(2, "Факт", None), (5, "План", None)],
    )
    assert _keys(cells) == ["2024@факт", "2024@факт", "2024@факт", "2024@план"]


def test_repeating_pattern_inside_one_date() -> None:
    cells = _sheet(["2024 факт", "2024 план", "2024 факт", "2024 план"])
    assert _keys(cells) == ["2024@факт", "2024@план", "2024@факт", "2024@план"]


def test_formula_derived_column_is_not_a_marker() -> None:
    cells = _sheet(
        ["2024 факт", "2024 план", "2024 итог"],
        formulas={4: "=SUM(B2:C2)"},
        rows=[["10", "21", "31"]],
    )
    assert _keys(cells) == ["2024@факт", "2024@план", "2024"]


def test_value_derived_column_is_not_a_marker() -> None:
    cells = _sheet(
        ["2024 факт", "2024 план", "2024 итог"],
        rows=[["10", "20", "30"], ["3", "4", "7"]],
    )
    assert _keys(cells) == ["2024@факт", "2024@план", "2024"]


def test_flag_row_confirms_the_tail_when_the_banner_disagrees() -> None:
    cells = _sheet(
        ["2024 факт", "2024 факт", "2024 план", "2024 план"],
        banner=[(2, "Альфа", 4), (5, "Бета", 5)],
        flag=["0", "0", "1", "1"],
        rows=[["10", "21", "33", "47"]],
    )
    assert _keys(cells) == ["2024@факт", "2024@факт", "2024@план", "2024@план"]
    assert _notes(cells) == []


def test_derived_word_on_a_sum_column_cannot_mark_it() -> None:
    cells = _sheet(
        ["2024", "2024", "2024 итог"],
        banner=[(2, "Факт", 2), (3, "План", 3), (4, "Дельта", 4)],
        formulas={4: "=B3-C3"},
        rows=[["10", "21", "11"]],
    )
    assert _keys(cells) == ["2024@факт", "2024@план", "2024"]


def test_two_dates_in_one_header_are_not_a_period_key() -> None:
    assert classify_header("2024 vs 2025") is None
    assert many_dates("2024 vs 2025") is True
    cells = _sheet(["2023", "2024 vs 2025", "2026"])
    keys = _keys(cells) if detect_layout(cells).sheets[0].axes else []
    assert "2024 vs 2025" not in keys
    assert all("@" not in key for key in keys)
    assert not any(key.startswith("2024") and "2025" in key for key in keys)


def test_year_banner_over_its_periods_is_not_a_marker() -> None:
    cells = _sheet(
        ["1 кв. 2024", "1 кв. 2024"],
        banner=[(2, "2024", 2), (3, "2024", 3)],
    )
    assert _keys(cells) == ["2024Q1", "2024Q1"]


def test_banner_over_the_whole_axis_is_a_title() -> None:
    cells = _sheet(
        ["2024", "2024"],
        banner=[(2, "Прогноз", None)],
    )
    assert _keys(cells) == ["2024", "2024"]


def test_units_row_is_not_a_marker() -> None:
    cells = _sheet(
        ["2024", "2024"],
        banner=[(2, "RUB", 2), (3, "USD", 3)],
    )
    assert _keys(cells) == ["2024", "2024"]


def test_banner_that_does_not_separate_duplicates_is_not_a_marker() -> None:
    cells = _sheet(
        ["2023", "2024"],
        banner=[(2, "Факт", 2), (3, "План", 3)],
    )
    assert _keys(cells) == ["2023", "2024"]


def test_broken_pattern_inside_a_date_is_not_a_marker() -> None:
    cells = _sheet(["2024 факт", "2024 план", "2024 факт", "2024 план", "2024 план"])
    assert _keys(cells) == ["2024", "2024", "2024", "2024", "2024"]


def test_bare_fact_column_stays_unknown() -> None:
    assert classify_header("Факт") is None
    assert classify_header("План") is None
    cells = _sheet(["2024", "2025", "Факт"])
    assert _keys(cells) == ["2024", "2025"]


def test_total_word_with_a_sum_formula_is_a_total_column() -> None:
    cells = [
        _cell("A1", "Item"),
        _cell("B1", "2023"),
        _cell("C1", "2024"),
        _cell("D1", "Итого"),
        _cell("A2", "Revenue"),
        _cell("B2", "10"),
        _cell("C2", "20"),
        _cell("D2", "30", formula="=SUM(B2:C2)"),
    ]
    assert _keys(cells) == ["2023", "2024", "total"]


def test_total_word_alone_is_not_a_period() -> None:
    cells = [
        _cell("A1", "Item"),
        _cell("B1", "2023"),
        _cell("C1", "2024"),
        _cell("D1", "Итого"),
        _cell("A2", "Revenue"),
        _cell("B2", "10"),
        _cell("C2", "20"),
        _cell("D2", "99"),
        _cell("A3", "Cost"),
        _cell("B3", "1"),
        _cell("C3", "2"),
        _cell("D3", "5"),
    ]
    assert _keys(cells) == ["2023", "2024"]


def test_sum_of_the_periods_is_a_total_without_the_word() -> None:
    cells = [
        _cell("A1", "Item"),
        _cell("B1", "2023"),
        _cell("C1", "2024"),
        _cell("D1", "Check"),
        _cell("A2", "Revenue"),
        _cell("B2", "10"),
        _cell("C2", "20"),
        _cell("D2", "30", formula="=SUM(B2:C2)"),
        _cell("A3", "Cost"),
        _cell("B3", "1"),
        _cell("C3", "2"),
        _cell("D3", "3", formula="=SUM(B3:C3)"),
    ]
    assert _keys(cells) == ["2023", "2024", "total"]


def test_hinted_total_accepts_one_matching_row() -> None:
    cells = [
        _cell("A1", "Item"),
        _cell("B1", "2023"),
        _cell("C1", "2024"),
        _cell("D1", "Total"),
        _cell("A2", "Revenue"),
        _cell("B2", "10"),
        _cell("C2", "20"),
        _cell("D2", "30"),
    ]
    assert _keys(cells) == ["2023", "2024", "total"]


def test_unresolved_tail_and_banner_leave_plain_keys_and_warn() -> None:
    cells = _sheet(
        ["2024 факт", "2024 план"],
        banner=[(2, "План", 2), (3, "Факт", 3)],
        rows=[["10", "21"]],
    )
    assert _keys(cells) == ["2024", "2024"]
    notes = _notes(cells)
    assert len(notes) == 1
    assert notes[0].startswith("Ambiguous period marker on P&L!r2:")
    assert "2024" in notes[0]
