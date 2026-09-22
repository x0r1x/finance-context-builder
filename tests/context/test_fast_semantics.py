from __future__ import annotations

from tests.helpers.fast_sheet import SHEET, fast_cells

from finance_context.context.build import build_context
from finance_context.layout.detect import detect_layout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.models import RowRelation
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.render.markdown import render_markdown


def _context(*, relations: list[RowRelation] | None = None):
    cells = fast_cells()
    layout = detect_layout(cells)
    mapping = map_layout(
        layout, taxonomy=load_taxonomy(), glossary={}, cells=cells, embed=None, chat=None
    )
    if relations:
        mapping = mapping.model_copy(update={"relations": [*mapping.relations, *relations]})
    return build_context(
        job_id="fast",
        workbook_meta={"sheets": [{"name": SHEET}]},
        cells=cells,
        layout=layout,
        mapping=mapping,
    )


def _rows(doc) -> dict[int, object]:
    return {row.row: row for block in doc.blocks for row in block.rows}


def test_phases_and_period_dates_come_from_the_ruler_and_its_flags() -> None:
    doc = _context()
    assert len(doc.axes) == 1
    by_key = {period.period_key: period for period in doc.axes[0].periods}
    assert (by_key["2024"].phase, by_key["2024"].phase_year) == ("construction", 1)
    assert (by_key["2025"].phase, by_key["2025"].phase_year) == ("construction", 2)
    assert (by_key["2026"].phase, by_key["2026"].phase_year) == ("operation", 1)
    assert (by_key["2031"].phase, by_key["2031"].phase_year) == ("operation", 6)
    assert by_key["2026"].start_date == "2026-01-01"
    assert by_key["2026"].end_date == "2026-12-31"
    payload = doc.model_dump(mode="json")
    first = payload["axes"][0]["periods"][0]
    assert first["start_date"] == "2024-01-01"
    flags = {name for period in doc.axes[0].periods for name in period.flags}
    assert flags == {"construction", "operations"}


def test_scenario_rows_are_point_values_with_parsed_units() -> None:
    rows = _rows(_context())
    npv = rows[50]
    assert npv.hints.unit == "money"
    assert npv.hints.currency == "EUR"
    assert npv.hints.scale == "k"
    assert npv.scale_factor == 1000
    assert (npv.period_position, npv.aggregation) == ("instant", "none")
    assert npv.values == ["100", "100", "90", "80"]
    duration = rows[64]
    headers = {cell.addr: cell.header for cell in duration.cells}
    assert headers["G64"] == "Start"
    assert headers["H64"] == "End"
    assert headers["L64"] == "Live Case"


def test_price_unit_keeps_the_denominator() -> None:
    price = _rows(_context())[186]
    assert price.hints.unit == "price"
    assert price.hints.unit_per == "MWh"
    assert price.hints.currency == "EUR"


def test_sources_and_uses_follow_the_section() -> None:
    rows = _rows(_context())
    assert rows[201].concept_id == "cf.drawdown"
    assert rows[201].hints.time_semantics == "flow"
    assert rows[196].concept_id == "cf.uses"
    assert rows[294].concept_id == "bs.debt"
    assert rows[186].concept_id == "pnl.price"


def test_generic_total_takes_the_section_concept() -> None:
    rows = _rows(_context())
    assert rows[285].concept_id == "bs.assets_noncurrent"


def test_scalar_kpi_left_of_the_ruler_is_mapped_as_an_instant() -> None:
    irr = _rows(_context())[560]
    assert irr.kind == "fact"
    assert irr.concept_id == "val.irr"
    assert irr.period_position == "instant"


def test_roll_forward_lines_move_between_opening_and_closing() -> None:
    block = f"{SHEET}!r185"
    relation = RowRelation(
        kind="roll_forward",
        source_row_key=f"{SHEET}|253|{block}",
        target_row_key=f"{SHEET}|255|{block}",
    )
    rows = _rows(_context(relations=[relation]))
    assert rows[253].hints.time_semantics == "bop"
    assert rows[254].hints.time_semantics == "flow"
    assert rows[254].hints.nature == "flow"
    assert rows[255].hints.time_semantics == "eop"


def test_float_residue_normalizes_to_zero_but_keeps_the_cache() -> None:
    debt = _rows(_context())[294]
    assert debt.values[3] == "1.9099388737231493E-11"
    assert debt.normalized_values[3] == "0"
    assert debt.normalized_values[1] == "-60000000"


def test_markdown_prints_dates_scenarios_paths_and_cells() -> None:
    rendered = render_markdown(_context())
    assert f"| {SHEET}!r7 | 2024 | 2025 |" in rendered
    assert "\n| Start | 2024-01-01 | 2025-01-01 |" in rendered
    assert "\n| Phase | construction | construction | operation |" in rendered
    assert "Live Case (L) | Case 1 (N) | Case 2 (O) | Case 3 (P) |" in rendered
    assert "| Path |" in rendered and "| Cells |" in rendered
    assert "G Start: 01.01.2024; H End: 31.12.2025" in rendered
    assert "L total: -100" in rendered
    assert "Cashflow Statement / Sources of funds" in rendered
    assert "| EUR/MWh |" in rendered
