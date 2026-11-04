from tests.helpers.published import published_context as build_context

from finance_context.context.series import normalize_value, value_status
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
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
                                    col=2, text="2023", kind="period", period_key="2023"
                                ),
                                AxisHeader(
                                    col=3, text="2024E", kind="period", period_key="2024E"
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
    )
    row = doc.blocks[0].rows[0]
    assert row.hints.scale == "k"
    assert row.hints.currency == "GBP"
    assert row.scale_factor == 1000
    assert row.values == ["2.5", None]
    assert row.normalized_values == ["2500", None]
    assert row.value_statuses == ["cached", "empty"]
    rendered = render_markdown(doc)
    assert "k£ ×1000" in rendered
    assert "2.5 (2500)" in rendered
    assert "empty" in rendered


def test_float_residue_next_to_the_series_scale_is_zero() -> None:
    from finance_context.context.series import normalize_series

    assert normalize_series(["-60000", "1.9099388737231493E-11", "5"], 1) == ["-60000", "0", "5"]
    assert normalize_series(["0.000001", "0.000002"], 1) == ["0.000001", "0.000002"]


def test_grouped_decimal_comma_matches_layout() -> None:
    assert normalize_value("1 230,5", 1) == "1230.5"
    assert normalize_value("1\xa0230,5", 1) == "1230.5"
    assert normalize_value("1\u202f230,5", 1) == "1230.5"
    assert normalize_value("12%", 1) == "12"


def test_scale_past_the_float_range_is_missing() -> None:
    from finance_context.context.series import normalize_series

    assert normalize_series(["1e308", "10"], 1000) == [None, "10000"]
    assert normalize_series(["1e308"], 1000) == [None]


def test_cached_number_is_canonical_without_a_scale_factor() -> None:
    from finance_context.context.series import normalize_series

    assert normalize_series(["7.927055656909944E-2"], None) == ["0.0792705566"]
    assert normalize_series(["1.0612079999999999"], None) == ["1.061208"]
    assert normalize_series(["1000"], None) == ["1000"]
    assert normalize_series(["2.5"], 1000) == ["2500"]
