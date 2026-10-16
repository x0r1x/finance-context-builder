from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.context.timeline import build_axes
from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.render.markdown import render_markdown


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


def test_flag_rows_set_construction_then_operation_phase() -> None:
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
        _c("TBA", "A4", "Revenue"),
        _c("TBA", "B4", "10"),
        _c("TBA", "C4", "20"),
        _c("TBA", "D4", "30"),
        _c("TBA", "A5", "Beginning of construction"),
        _c("TBA", "B5", "1"),
        _c("TBA", "C5", "0"),
        _c("TBA", "D5", "0"),
        _c("TBA", "A6", "Active"),
        _c("TBA", "B6", "1"),
        _c("TBA", "C6", "1"),
        _c("TBA", "D6", "0"),
    ]
    layout = detect_layout(cells)
    kinds = {row.label: row.kind for row in layout.sheets[0].blocks[0].rows}
    assert kinds["Construction"] == "flag"
    assert kinds["Operation"] == "flag"
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert not warnings
    by_id = {item.period_key: item for item in axes[0].periods}
    assert by_id["Y1"].phase == "construction" and by_id["Y1"].phase_year == 1
    assert by_id["Y2"].phase == "construction" and by_id["Y2"].phase_year == 2
    assert by_id["Y3"].phase == "operation" and by_id["Y3"].phase_year == 1
    assert by_id["Y1"].flags["construction"] is True
    assert by_id["Y3"].flags["operation"] is True
    assert by_id["Y1"].flags["beginning of construction"] is True
    assert by_id["Y1"].calendar_year is None
    doc = build_context(
        job_id="t",
        workbook_meta={"sheets": [{"name": "TBA"}]},
        cells=cells,
        layout=layout,
    )
    revenue_block = next(block for block in doc.blocks if block.sheet == "TBA")
    assert revenue_block.periods == []
    rendered = render_markdown(doc)
    assert "## Axes" in rendered
    assert "construction" in rendered
    flag_rows = [row for block in doc.blocks for row in block.rows if row.kind == "flag"]
    assert flag_rows
    assert all(row.disposition == "excluded" for row in flag_rows)


def test_repeated_flag_labels_keep_both_rows() -> None:
    cells = [
        _c("TBA", "A1", "Year"),
        _c("TBA", "B1", "1"),
        _c("TBA", "C1", "2"),
        _c("TBA", "D1", "3"),
        _c("TBA", "E1", "4"),
        _c("TBA", "A2", "Availability Start Date"),
        _c("TBA", "B2", "1"),
        _c("TBA", "C2", "0"),
        _c("TBA", "D2", "0"),
        _c("TBA", "E2", "0"),
        _c("TBA", "A3", "Active"),
        _c("TBA", "B3", "1"),
        _c("TBA", "C3", "1"),
        _c("TBA", "D3", "0"),
        _c("TBA", "E3", "0"),
        _c("TBA", "A4", "Repayment Start Date"),
        _c("TBA", "B4", "0"),
        _c("TBA", "C4", "0"),
        _c("TBA", "D4", "1"),
        _c("TBA", "E4", "0"),
        _c("TBA", "A5", "Active"),
        _c("TBA", "B5", "0"),
        _c("TBA", "C5", "0"),
        _c("TBA", "D5", "1"),
        _c("TBA", "E5", "1"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert not warnings
    by_id = {item.period_key: item for item in axes[0].periods}
    availability = "active (availability start date)"
    repayment = "active (repayment start date)"
    assert by_id["Y1"].flags[availability] is True
    assert by_id["Y1"].flags[repayment] is False
    assert by_id["Y2"].flags[availability] is True
    assert by_id["Y3"].flags[availability] is False
    assert by_id["Y3"].flags[repayment] is True
    assert by_id["Y4"].flags[repayment] is True
    assert "active" not in by_id["Y1"].flags


def test_two_identical_flags_under_one_anchor_stay_distinct() -> None:
    cells = [
        _c("TBA", "A1", "Year"),
        _c("TBA", "B1", "1"),
        _c("TBA", "C1", "2"),
        _c("TBA", "D1", "3"),
        _c("TBA", "E1", "4"),
        _c("TBA", "A2", "Availability Start Date"),
        _c("TBA", "B2", "1"),
        _c("TBA", "C2", "0"),
        _c("TBA", "D2", "0"),
        _c("TBA", "E2", "0"),
        _c("TBA", "A3", "Active"),
        _c("TBA", "B3", "1"),
        _c("TBA", "C3", "1"),
        _c("TBA", "D3", "0"),
        _c("TBA", "E3", "0"),
        _c("TBA", "A4", "Active"),
        _c("TBA", "B4", "0"),
        _c("TBA", "C4", "0"),
        _c("TBA", "D4", "1"),
        _c("TBA", "E4", "1"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert not warnings
    by_id = {item.period_key: item for item in axes[0].periods}
    first = "active (availability start date)"
    second = "active (availability start date) 4"
    assert by_id["Y1"].flags[first] is True
    assert by_id["Y1"].flags[second] is False
    assert by_id["Y3"].flags[first] is False
    assert by_id["Y3"].flags[second] is True
    assert by_id["Y4"].flags[second] is True
    assert "active" not in by_id["Y1"].flags


def test_flag_row_fallback_does_not_replace_another_label() -> None:
    cells = [
        _c("TBA", "A1", "Year"),
        _c("TBA", "B1", "1"),
        _c("TBA", "C1", "2"),
        _c("TBA", "D1", "3"),
        _c("TBA", "E1", "4"),
        _c("TBA", "A2", "Active 4"),
        _c("TBA", "B2", "1"),
        _c("TBA", "C2", "0"),
        _c("TBA", "D2", "0"),
        _c("TBA", "E2", "0"),
        _c("TBA", "A3", "Active"),
        _c("TBA", "B3", "1"),
        _c("TBA", "C3", "1"),
        _c("TBA", "D3", "0"),
        _c("TBA", "E3", "0"),
        _c("TBA", "A4", "Active"),
        _c("TBA", "B4", "0"),
        _c("TBA", "C4", "0"),
        _c("TBA", "D4", "1"),
        _c("TBA", "E4", "1"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert not warnings
    by_id = {item.period_key: item for item in axes[0].periods}
    qualified = "active (active 4)"
    later = "active (active 4) 4"
    assert by_id["Y1"].flags["active 4"] is True
    assert by_id["Y2"].flags["active 4"] is False
    assert by_id["Y3"].flags["active 4"] is False
    assert by_id["Y1"].flags[qualified] is True
    assert by_id["Y2"].flags[qualified] is True
    assert by_id["Y3"].flags[qualified] is False
    assert by_id["Y1"].flags[later] is False
    assert by_id["Y3"].flags[later] is True
    assert by_id["Y4"].flags[later] is True


def test_calendar_book_timeline_has_no_phase() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2020"),
        _c("P&L", "C1", "2021"),
        _c("P&L", "D1", "2022"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "1"),
        _c("P&L", "C2", "2"),
        _c("P&L", "D2", "3"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert len(axes) == 1
    assert not warnings
    assert axes[0].grain == "year"
    assert all(item.phase is None for item in axes[0].periods)
    assert [item.calendar_year for item in axes[0].periods] == ["2020", "2021", "2022"]


def test_duration_mismatch_warns_without_overriding_flags() -> None:
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
        _c("TBA", "A4", "Revenue"),
        _c("TBA", "B4", "10"),
        _c("TBA", "C4", "20"),
        _c("TBA", "D4", "30"),
        _c("Input Assumptions", "B5", "Parameter"),
        _c("Input Assumptions", "C5", "Units"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "B8", "Construction Duration"),
        _c("Input Assumptions", "C8", "years"),
        _c("Input Assumptions", "D8", "5"),
        _c("Input Assumptions", "B9", "Operations Duration"),
        _c("Input Assumptions", "C9", "years"),
        _c("Input Assumptions", "D9", "1"),
        _c("Input Assumptions", "B10", "Concession Duration"),
        _c("Input Assumptions", "C10", "years"),
        _c("Input Assumptions", "D10", "10"),
        _c("Input Assumptions", "B11", "Tax Rate"),
        _c("Input Assumptions", "C11", "%"),
        _c("Input Assumptions", "D11", "0.3"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert [item.phase for item in axes[0].periods] == [
        "construction",
        "construction",
        "operation",
    ]
    assert any("Construction Duration" in item for item in warnings)
    assert any("Concession Duration" in item for item in warnings)
    assert not any("Operations Duration" in item for item in warnings)


def test_year_banner_and_months_publish_axes_in_json_and_markdown() -> None:
    cells = [
        _c("Output", "N1", "2020"),
        _c("Output", "O1", "2020"),
        _c("Output", "P1", "2020"),
        _c("Output", "Q1", "2021"),
        _c("Output", "A3", "Operational Results"),
        _c("Output", "B6", "Item"),
        _c("Output", "C6", "2020"),
        _c("Output", "D6", "2021"),
        _c("Output", "E6", "2022"),
        _c("Output", "N6", "янв.20"),
        _c("Output", "O6", "фев.20"),
        _c("Output", "P6", "мар.20"),
        _c("Output", "Q6", "янв.21"),
        _c("Output", "B7", "DC Capacity"),
        _c("Output", "C7", "10"),
        _c("Output", "D7", "11"),
        _c("Output", "E7", "12"),
        _c("Output", "N7", "1"),
        _c("Output", "O7", "2"),
        _c("Output", "P7", "3"),
        _c("Output", "Q7", "4"),
    ]
    layout = detect_layout(cells)
    doc = build_context(
        job_id="banner",
        workbook_meta={"sheets": [{"name": "Output"}]},
        cells=cells,
        layout=layout,
    )
    payload = doc.model_dump(mode="json")
    assert "timeline" not in payload
    grains = {axis["grain"] for axis in payload["axes"]}
    assert grains == {"year", "month"}
    month = next(axis for axis in payload["axes"] if axis["grain"] == "month")
    assert [period["group_key"] for period in month["periods"]] == [
        "2020",
        "2020",
        "2020",
        "2021",
    ]
    block = next(item for item in payload["blocks"] if item["sheet"] == "Output")
    assert block["axis_ids"] == [axis["id"] for axis in payload["axes"]]
    series = {item["axis_id"]: item["values"] for item in block["rows"][0]["series"]}
    assert sorted(len(values) for values in series.values()) == [3, 4]
    sample = month["periods"][0]
    assert sample["group_key"] == "2020"
    assert "phase" not in sample
    assert "flags" not in sample
    assert "calendar_year" not in sample
    year = next(axis for axis in payload["axes"] if axis["grain"] == "year")
    assert "phase" not in year["periods"][0]
    assert "calendar_year" not in year["periods"][0]
    rendered = render_markdown(doc)
    assert "## Axes" in rendered
    assert "| Period | Group | Phase | Phase year | Calendar | Flags |" not in rendered
    assert "Phase" not in rendered
    assert "Calendar" not in rendered
    month_id = month["id"]
    assert f"| {month_id} | 2020 | 2021 |\n| --- | --- | --- |\n" in rendered
    assert "| Periods | 2020-01 .. 2020-03 | 2021-01 |" in rendered
    assert f"| {year['id']} | 2020 | 2021 | 2022 |" in rendered
    assert "Axes:" in rendered
    assert rendered.count("### `") >= 2


def test_phased_year_axis_is_forecast_unless_the_header_says_actual() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2023A"),
        _c("PF", "C1", "2024"),
        _c("PF", "D1", "2025E"),
        _c("PF", "E1", "2056"),
        _c("PF", "A2", "Construction"),
        _c("PF", "B2", "0"),
        _c("PF", "C2", "1"),
        _c("PF", "D2", "0"),
        _c("PF", "E2", "0"),
        _c("PF", "A3", "Operations"),
        _c("PF", "B3", "0"),
        _c("PF", "C3", "0"),
        _c("PF", "D3", "1"),
        _c("PF", "E3", "0"),
        _c("PF", "A4", "Revenue"),
        _c("PF", "C4", "10"),
        _c("PF", "D4", "11"),
    ]
    layout = detect_layout(cells)
    headers = {header.period_key: header for header in layout.sheets[0].axes[0].periods}
    assert headers["2024"].role == "historical"
    assert headers["2024"].explicit_role is False
    assert headers["2023"].role == "historical" and headers["2023"].explicit_role is True
    assert headers["2025"].role == "forecast" and headers["2025"].explicit_role is True
    axes, _warnings = build_axes(layout, cells)
    roles = {period.period_key: period.role for period in axes[0].periods}
    assert roles["2023"] == "historical"
    assert roles["2024"] == "forecast"
    assert roles["2025"] == "forecast"
    assert roles["2056"] == "forecast"


def _ru_timeline(*rows: tuple[str, list[str]]) -> list[dict]:
    cells = [_c("Модель", "A1", "Показатель")]
    years = ["2025", "2026", "2027", "2028"]
    cells += [_c("Модель", f"{col}1", year) for col, year in zip("BCDE", years, strict=True)]
    cells += [_c("Модель", "A2", "Выручка")]
    revenue = ["0", "0", "10", "20"]
    cells += [_c("Модель", f"{col}2", v) for col, v in zip("BCDE", revenue, strict=True)]
    for r, (label, values) in enumerate(rows, start=3):
        cells.append(_c("Модель", f"A{r}", label))
        cells += [_c("Модель", f"{col}{r}", v) for col, v in zip("BCDE", values, strict=True)]
    return cells


def test_russian_flag_rows_set_construction_then_operation_phase() -> None:
    cells = _ru_timeline(
        ("Флаг строительства", ["1", "1", "0", "0"]),
        ("Флаг эксплуатации", ["0", "0", "1", "1"]),
        ("Флаг ввода в эксплуатацию", ["0", "0", "1", "0"]),
    )
    axes, _warnings = build_axes(detect_layout(cells), cells)
    assert [(p.period_key, p.phase, p.phase_year, p.role) for p in axes[0].periods] == [
        ("2025", "construction", 1, "forecast"),
        ("2026", "construction", 2, "forecast"),
        ("2027", "operation", 1, "forecast"),
        ("2028", "operation", 2, "forecast"),
    ]


def test_russian_investment_phase_is_construction() -> None:
    cells = _ru_timeline(
        ("Инвестиционная фаза", ["1", "1", "1", "0"]),
        ("Операционный период", ["0", "0", "0", "1"]),
    )
    axes, _warnings = build_axes(detect_layout(cells), cells)
    assert [p.phase for p in axes[0].periods] == [
        "construction",
        "construction",
        "construction",
        "operation",
    ]


def test_russian_event_flags_are_not_phases() -> None:
    cells = _ru_timeline(
        ("Дата начала строительства", ["1", "0", "0", "0"]),
        ("Окончание строительства", ["0", "1", "0", "0"]),
        ("Ввод в эксплуатацию", ["0", "0", "1", "0"]),
    )
    axes, _warnings = build_axes(detect_layout(cells), cells)
    assert [p.phase for p in axes[0].periods] == [None, None, None, None]
