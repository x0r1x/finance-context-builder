"""Numeric params columns stay in the context as values. The header text is kept.

Each book has one sheet: a label column, a header row, and four rows of numbers.
A period-like header that does not form an axis stays ``unknown`` and warns.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import xlsxwriter

from finance_context.app.pipeline import Pipeline
from finance_context.models.context import ContextDocument
from finance_context.settings import Settings

_LABELS = ["Выручка", "Операционные расходы", "Капитальные затраты", "Налог на прибыль"]


def _book(path: Path, header: list[str], *, unit_col: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet("Ввод")
    for col, text in enumerate(header):
        sheet.write_string(0, col, text)
    for index, label in enumerate(_LABELS, start=1):
        sheet.write_string(index, 0, label)
        for col in range(1, len(header)):
            if unit_col and col == len(header) - 1:
                sheet.write_string(index, col, "млн руб.")
            else:
                sheet.write_number(index, col, 100 * index + col)
    book.close()
    return path


def _run(tmp_path: Path, header: list[str], *, unit_col: bool = False) -> ContextDocument:
    dest = tmp_path / "job"
    dest.mkdir()
    _book(dest / "source" / "source.xlsx", header, unit_col=unit_col)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    return pipeline.run(dest, job_id="job", source_filename="book.xlsx")


def _unknown_columns(doc: ContextDocument) -> dict[str, list[str | None]]:
    """Header text -> values of that column down the block, for ``unknown`` columns."""
    assert doc.blocks, "the sheet vanished: no block"
    block = doc.blocks[0]
    texts = [str(period["text"]) for period in block.periods]
    roles = [period["role"] for period in block.periods]
    found: dict[str, list[str | None]] = {}
    for position, (text, role) in enumerate(zip(texts, roles, strict=True)):
        if role != "unknown":
            continue
        found[text] = [row.series[0].values[position] for row in block.rows]
    return found


@pytest.mark.parametrize(
    "header",
    [
        ["Параметр", "Low", "Mid", "High"],
        ["Показатель", "Вариант 1", "Вариант 2"],
        ["Параметр", "Мин", "Макс"],
    ],
)
def test_side_by_side_numeric_columns_are_values(tmp_path: Path, header: list[str]) -> None:
    doc = _run(tmp_path, header)
    block = doc.blocks[0]
    assert [period["text"] for period in block.periods] == header[1:]
    assert [period["role"] for period in block.periods] == ["value"] * (len(header) - 1)
    for col in range(1, len(header)):
        assert [row.series[0].values[col - 1] for row in block.rows] == [
            str(100 * row_n + col) for row_n in range(1, 5)
        ]
    assert not any("Unrecognized column headers" in item for item in doc.warnings)
    assert not any("scenario" in item for item in doc.warnings)


def test_single_numeric_column_is_a_value_and_the_unit_column_stays(tmp_path: Path) -> None:
    doc = _run(tmp_path, ["Item", "Amount", "Unit"], unit_col=True)
    periods = {period["text"]: period["role"] for period in doc.blocks[0].periods}
    assert periods["Amount"] == "value"
    assert [row.series[0].values[0] for row in doc.blocks[0].rows] == ["101", "201", "301", "401"]
    assert not any("scenario" in item for item in doc.warnings)
    units = {
        cell.cached_value for row in doc.blocks[0].rows for cell in row.cells if cell.role == "unit"
    }
    assert units == {"млн руб."}


@pytest.mark.parametrize(
    "header",
    [
        ["Показатель", "9М 2024", "9М 2025", "9М 2026"],
        ["Показатель", "FY2024/25", "FY2025/26", "FY2026/27"],
        ["Показатель", "2025/26", "2026/27", "2027/28"],
        ["Показатель", "Год 1", "Год 2", "Год 3"],
    ],
)
def test_period_like_headers_without_an_axis_warn_with_their_address(
    tmp_path: Path, header: list[str]
) -> None:
    doc = _run(tmp_path, header)
    columns = _unknown_columns(doc)
    assert list(columns) == header[1:]
    assert columns[header[1]] == ["101", "201", "301", "401"]
    warning = next(item for item in doc.warnings if "No time axis" in item)
    assert "Ввод!B1:D1" in warning
    assert all(f"«{text}»" in warning for text in header[1:])
    assert not any("scenario" in item for item in doc.warnings)


@pytest.mark.parametrize(
    ("header", "keys"),
    [
        (
            ["Показатель", "Ожидаемое 2025", "Ожидаемое 2026", "Ожидаемое 2027"],
            ["2025", "2026", "2027"],
        ),
        (
            ["Item", "Estimate 2025", "Estimate 2026", "Estimate 2027"],
            ["2025", "2026", "2027"],
        ),
        (
            ["Показатель", "Факт 2024", "Ожидаемое 2025", "План 2026"],
            ["2024", "2025", "2026"],
        ),
    ],
)
def test_one_date_plus_residual_is_a_plain_year_axis(
    tmp_path: Path, header: list[str], keys: list[str]
) -> None:
    doc = _run(tmp_path, header)
    assert doc.blocks[0].kind == "timeline"
    assert [period.period_key for period in doc.axes[0].periods] == keys
    assert not any("No time axis" in item for item in doc.warnings)
