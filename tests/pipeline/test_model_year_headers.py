"""Year 1 … Year N is a model-year axis when the body copies formulas.

The first year may use a different formula. A header that only looks similar
stays a local block, with every value and a warning that names its address.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import xlsxwriter
from tests.helpers.published import attach_streamed_rows

from finance_context.app.pipeline import Pipeline
from finance_context.context.observations import build_observations
from finance_context.graph.models import FormulaLink
from finance_context.layout.periods import classify_header
from finance_context.models.context import ContextDocument
from finance_context.settings import Settings

_SHEET = "Operating Forecast"
_RENT = "Base rent ($/sf)"
_YEARS = ("Year 1", "Year 2", "Year 3", "Year 4", "Year 5")
_RENT_CACHE = (24, 24.72, 25.4616, 26.225448, 27.01221144)


def _formula(sheet, row: int, col: int, formula: str, cache) -> None:
    sheet.write_formula(row, col, formula, None, cache)


def _debtlens_geometry(path: Path) -> Path:
    """Title, one parameter, then Year 1…Year 5 with a copied growth formula."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = xlsxwriter.Workbook(str(path))
    assumptions = book.add_worksheet("Assumptions")
    assumptions.write_string(3, 3, "Base")
    assumptions.write_number(10, 3, 24)
    assumptions.write_number(14, 3, 0.03)

    forecast = book.add_worksheet(_SHEET)
    forecast.write_string(1, 2, "Five-year property cash flow")
    forecast.write_string(3, 2, "Case Selected:")
    _formula(forecast, 3, 3, "=Assumptions!D4", "Base")
    forecast.write_string(5, 2, "Metric")
    for offset, label in enumerate(_YEARS):
        forecast.write_string(5, 3 + offset, label)
    forecast.write_string(6, 2, _RENT)
    _formula(forecast, 6, 3, "=Assumptions!D11", _RENT_CACHE[0])
    for offset, cache in enumerate(_RENT_CACHE[1:], start=1):
        previous = xlsxwriter.utility.xl_col_to_name(3 + offset - 1)
        _formula(
            forecast,
            6,
            3 + offset,
            f"={previous}7*(1+Assumptions!$D$15)",
            cache,
        )
    assumptions.write_number(11, 3, 1.2)
    assumptions.write_number(15, 3, 0.02)
    assumptions.write_number(16, 3, 0.025)
    forecast.write_string(7, 2, "Other income ($/sf)")
    _formula(forecast, 7, 3, "=Assumptions!D12", 1.2)
    income = (1.2, 1.224, 1.24848, 1.2734496, 1.298918592)
    for offset, cache in enumerate(income[1:], start=1):
        previous = xlsxwriter.utility.xl_col_to_name(3 + offset - 1)
        _formula(
            forecast,
            7,
            3 + offset,
            f"={previous}8*(1+Assumptions!$D$16)",
            cache,
        )
    for excel_row, label in (
        (9, "Vacancy"),
        (10, "Gross potential income"),
        (11, "Vacancy loss"),
        (12, "Effective gross income"),
        (13, "Operating expense ($/sf)"),
    ):
        forecast.write_string(excel_row - 1, 2, label)
        _formula(forecast, excel_row - 1, 3, "=Assumptions!D11", 10 + excel_row)
        for offset in range(1, 5):
            previous = xlsxwriter.utility.xl_col_to_name(3 + offset - 1)
            _formula(
                forecast,
                excel_row - 1,
                3 + offset,
                f"={previous}{excel_row}*(1+Assumptions!$D$15)",
                10 + excel_row + offset,
            )
    book.close()
    return path


def _run_book(tmp_path: Path, build) -> tuple[ContextDocument, list[FormulaLink]]:
    dest = tmp_path / "job"
    dest.mkdir()
    source = dest / "source" / "source.xlsx"
    build(source)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    doc = attach_streamed_rows(
        pipeline.run(dest, job_id="job", source_filename="book.xlsx")
    )
    return doc, _links(dest)


def _links(dest: Path) -> list[FormulaLink]:
    payload = json.loads((dest / "graph" / "graph.json").read_text(encoding="utf-8"))
    return [
        FormulaLink.model_validate(item)
        for item in payload.get("links") or []
        if isinstance(item, dict)
    ]


def _block(doc: ContextDocument, label: str):
    found = [
        (block, row)
        for block in doc.blocks
        for row in block.rows
        if row.label == label and row.sheet == _SHEET
    ]
    assert len(found) == 1, [row.label for block in doc.blocks for row in block.rows]
    return found[0]


def test_classify_header_still_rejects_a_model_year_caption() -> None:
    assert classify_header("Year 1") is None
    assert classify_header("2025") is not None


def test_copied_year_headers_publish_model_year_keys(tmp_path: Path) -> None:
    doc, links = _run_book(tmp_path, _debtlens_geometry)
    block, rent = _block(doc, _RENT)
    assert block.kind == "timeline"
    axis = next(axis for axis in doc.axes if axis.sheet == _SHEET)
    assert axis.grain == "model_year"
    assert [period.period_key for period in axis.periods] == ["Y1", "Y2", "Y3", "Y4", "Y5"]
    assert [period.col for period in axis.periods] == [4, 5, 6, 7, 8]
    assert [period.text for period in axis.periods] == list(_YEARS)
    assert all(period.calendar_year is None for period in axis.periods)
    assert all(period.start_date is None and period.end_date is None for period in axis.periods)
    assert rent.label == _RENT
    assert [float(value) for value in rent.values] == pytest.approx(list(_RENT_CACHE))
    case_block, case = _block(doc, "Case Selected:")
    title_block, title = _block(doc, "Five-year property cash flow")
    assert title_block.block_id == case_block.block_id
    assert case_block.kind == "params"
    assert (title.row, title.kind) == (2, "abstract")
    assert (case.row, case.kind) == (4, "fact")
    assert case.values == ["Base"]
    assert case.label_path == []
    assert all(row.label_path == [] for row in block.rows)
    assert "Case Selected:" not in {row.label for row in block.rows}
    other_block, other = _block(doc, "Other income ($/sf)")
    assert other_block.block_id == block.block_id
    assert [float(value) for value in other.values] == pytest.approx(
        [1.2, 1.224, 1.24848, 1.2734496, 1.298918592]
    )
    for label in (
        "Vacancy",
        "Gross potential income",
        "Vacancy loss",
        "Effective gross income",
        "Operating expense ($/sf)",
    ):
        body, row = _block(doc, label)
        assert body.block_id == block.block_id
        assert len(row.values) == 5
        assert all(value is not None for value in row.values)
    rent_links = {
        link.cell: link
        for link in links
        if link.cell.startswith(f"{_SHEET}!")
        and link.cell[-2:] in {"D7", "E7", "F7", "G7", "H7"}
    }
    assert rent_links[f"{_SHEET}!E7"].refs == [f"{_SHEET}!D7", "Assumptions!D15"]
    assert rent_links[f"{_SHEET}!E7"].period_id == "Y2"
    assert rent_links[f"{_SHEET}!F7"].period_id == "Y3"
    observations = build_observations(
        doc, links, q=_RENT, period_ids=["Y3"], limit=8
    )
    assert len(observations.observations) == 1
    found = observations.observations[0]
    assert found.source.sheet == _SHEET
    assert found.source.cell == "F7"
    assert float(found.value) == pytest.approx(25.4616)
    assert not any("No time axis" in item and _SHEET in item for item in doc.warnings)
