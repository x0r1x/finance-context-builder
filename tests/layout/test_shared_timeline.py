from __future__ import annotations

from tests.layout.test_detect import _c

from finance_context.context.build import build_context
from finance_context.graph.stage import _period_maps
from finance_context.layout.detect import detect_layout
from finance_context.layout.models import (
    AxisPeriod,
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
    TimeAxis,
)
from finance_context.mapping.models import MappingDocument
from finance_context.render.markdown import render_markdown


def _year_sheet(name: str, columns: str, *, flags: bool) -> list[dict]:
    cells = [_c(name, "A2", "Year")]
    for index, column in enumerate(columns, start=1):
        cells.append(_c(name, f"{column}2", str(index)))
    if flags:
        cells.append(_c(name, "A3", "Construction"))
        cells.append(_c(name, "A4", "Operation"))
        for column, value in zip(columns, ("1", "1", "0", "0"), strict=True):
            cells.append(_c(name, f"{column}3", value))
        for column, value in zip(columns, ("0", "0", "1", "1"), strict=True):
            cells.append(_c(name, f"{column}4", value))
    cells.append(_c(name, "A5", "Amount"))
    for index, column in enumerate(columns, start=1):
        cells.append(_c(name, f"{column}5", str(index * 10)))
    return cells


def test_shifted_model_years_share_one_published_timeline() -> None:
    cells = _year_sheet("TBA", "DEFG", flags=True) + _year_sheet("Build", "EFGH", flags=False)
    layout = detect_layout(cells)
    tba = next(sheet for sheet in layout.sheets if sheet.name == "TBA")
    build = next(sheet for sheet in layout.sheets if sheet.name == "Build")
    canonical = tba.axes[0].id
    assert tba.blocks[0].timeline_ids == [canonical]
    assert build.blocks[0].timeline_ids == [canonical]
    assert build.blocks[0].axis_ids != [canonical]
    assert [period.col for period in build.axes[0].periods] == [5, 6, 7, 8]
    assert [period.col for period in tba.axes[0].periods] == [4, 5, 6, 7]

    doc = build_context(
        job_id="shared",
        workbook_meta={"sheets": [{"name": "TBA"}, {"name": "Build"}]},
        cells=cells,
        layout=layout,
        mapping=MappingDocument(),
    )
    assert [axis.id for axis in doc.axes] == [canonical]
    assert [period.period_key for period in doc.axes[0].periods] == ["Y1", "Y2", "Y3", "Y4"]
    assert [period.phase for period in doc.axes[0].periods] == [
        "construction",
        "construction",
        "operation",
        "operation",
    ]
    build_block = next(block for block in doc.blocks if block.sheet == "Build")
    assert build_block.axis_ids == [canonical]
    assert [item["col"] for item in build_block.periods] == [5, 6, 7, 8]
    amount = next(row for row in build_block.rows if row.label == "Amount")
    assert amount.series[0].values[0] == "10"
    assert amount.series[0].axis_id == canonical

    rendered = render_markdown(doc)
    assert rendered.count("### `") == 1
    assert f"### `{canonical}`" in rendered
    assert "Axes: `" + canonical + "`" in rendered
    assert "Y1 (E)" in rendered
    assert "Build!E5" in rendered
    again = render_markdown(doc.model_validate(doc.model_dump(mode="json")))
    assert again == rendered

    period_by_cell, _index = _period_maps(layout)
    assert period_by_cell[("Build", 5)] == "Y1"
    assert period_by_cell[("TBA", 4)] == "Y1"


def test_different_lengths_and_conflicting_flags_stay_apart() -> None:
    short = _year_sheet("Short", "DEF", flags=False)
    # Three years, not four: drop the last column by building the sheet directly below.
    long_flags = _year_sheet("Long", "DEFG", flags=True)
    other_flags = [
        _c("Other", "A2", "Year"),
        *[_c("Other", f"{column}2", str(index)) for index, column in enumerate("DEFG", start=1)],
        _c("Other", "A3", "Construction"),
        _c("Other", "D3", "0"),
        _c("Other", "E3", "0"),
        _c("Other", "F3", "1"),
        _c("Other", "G3", "1"),
        _c("Other", "A4", "Amount"),
        *[_c("Other", f"{column}4", "1") for column in "DEFG"],
    ]
    layout = detect_layout(short + long_flags + other_flags)
    ids = {sheet.name: sheet.blocks[0].timeline_ids[0] for sheet in layout.sheets}
    assert ids["Long"] != ids["Other"]
    assert ids["Short"] != ids["Long"]


def test_dated_calendar_wins_over_an_undated_copy() -> None:
    dated = TimeAxis(
        id="Model!r7",
        grain="year",
        header_row=7,
        periods=[
            AxisPeriod(
                col=13,
                text=str(year),
                role="historical",
                period_key=str(year),
                start_date=f"{year}-01-01",
                end_date=f"{year}-12-31",
            )
            for year in (2024, 2025, 2026)
        ],
    )
    copy = TimeAxis(
        id="Note!r2",
        grain="year",
        header_row=2,
        periods=[
            AxisPeriod(col=index, text=str(year), role="historical", period_key=str(year))
            for index, year in enumerate((2024, 2025, 2026), start=6)
        ],
    )
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Note",
                axes=[copy],
                blocks=[
                    Block(
                        block_id="Note!r2",
                        label_col=1,
                        axis_ids=["Note!r2"],
                        rows=[LayoutRow(row=3, label="Price")],
                    )
                ],
            ),
            SheetLayout(
                name="Model",
                axes=[dated],
                blocks=[
                    Block(
                        block_id="Model!r7",
                        label_col=1,
                        axis_ids=["Model!r7"],
                        rows=[LayoutRow(row=8, label="Construction", kind="flag")],
                    )
                ],
            ),
        ]
    )
    from finance_context.layout.detect import _link_workbook_timelines

    _link_workbook_timelines(layout, [], False)
    assert layout.sheets[0].blocks[0].timeline_ids == ["Model!r7"]
    assert layout.sheets[1].blocks[0].timeline_ids == ["Model!r7"]
    assert [period.col for period in layout.sheets[0].axes[0].periods] == [6, 7, 8]
