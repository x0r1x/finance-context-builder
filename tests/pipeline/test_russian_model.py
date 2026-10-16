"""A Russian cash-flow model written the way Excel stores it.

Sheet names are Cyrillic and unquoted in formulas, the unit column says
"тыс. руб.", and formulas keep the en-US `,` separator. Cached values only.
"""

from __future__ import annotations

import json
from pathlib import Path

import xlsxwriter
from tests.helpers.parquet import load_parquet

from finance_context.app.pipeline import Pipeline
from finance_context.excel import parse_workbook
from finance_context.settings import Settings

YEARS = list(range(2025, 2037))
VOLUME = [100 + 10 * i for i in range(len(YEARS))]
PRICE = 12
OPEX_SHARE = 0.4


def _russian_model(path: Path) -> Path:
    book = xlsxwriter.Workbook(str(path))
    amount = book.add_format({"num_format": "[$-419]# ##0"})
    share = book.add_format({"num_format": "0%"})

    inputs = book.add_worksheet("Допущения")
    inputs.write("A1", "Допущения")
    inputs.write("A3", "Цена за единицу")
    inputs.write("B3", "тыс. руб.")
    inputs.write_number("C3", PRICE, amount)
    inputs.write("A4", "Доля операционных расходов")
    inputs.write("B4", "%")
    inputs.write_number("C4", OPEX_SHARE, share)

    model = book.add_worksheet("Модель")
    model.write("A1", "Денежный поток")
    model.write("A3", "Показатель")
    model.write("B3", "Ед. изм.")
    model.write("A4", "Объём продаж")
    model.write("B4", "шт.")
    model.write("A5", "Выручка")
    model.write("B5", "тыс. руб.")
    model.write("A6", "Операционные расходы")
    model.write("B6", "тыс. руб.")
    model.write("A7", "EBITDA")
    model.write("B7", "тыс. руб.")
    for i, year in enumerate(YEARS):
        col = 2 + i
        model.write_number(2, col, year)
        model.write_number(3, col, VOLUME[i])
        cell = xlsxwriter.utility.xl_rowcol_to_cell
        revenue = VOLUME[i] * PRICE
        opex = -revenue * OPEX_SHARE
        model.write_formula(
            4, col, f"={cell(3, col)}*Допущения!$C$3", amount, revenue
        )
        model.write_formula(
            5, col, f"=-{cell(4, col)}*Допущения!$C$4", amount, round(opex)
        )
        model.write_formula(
            6, col, f"=SUM({cell(4, col)},{cell(5, col)})", amount, round(revenue + opex)
        )
    book.close()
    return path


def test_russian_model_keeps_cross_sheet_formulas_and_ruble_scale(
    tmp_path: Path, dest: Path
) -> None:
    source = _russian_model(tmp_path / "model.xlsx")
    parse_workbook(source, dest)
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    doc = pipeline.run(dest, job_id="ru-model", source_filename="model.xlsx")

    assert doc.workbook.formula_count == 36
    assert doc.workbook.unparsed_formulas == 0

    edges = load_parquet(dest / "ir" / "edges.parquet")
    cross = {(e["source"], e["target"]) for e in edges if e["kind"] == "cross_sheet"}
    assert ("Модель!C5", "Допущения!C3") in cross
    assert ("Модель!N6", "Допущения!C4") in cross
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    # Revenue and opex read one input each per year.
    assert graph["kinds"]["cross_sheet"] == 2 * len(YEARS)

    rows = {row.label: row for block in doc.blocks for row in block.rows if row.sheet == "Модель"}
    revenue = rows["Выручка"]
    assert revenue.hints.unit == "money"
    assert revenue.hints.currency == "RUB"
    assert revenue.hints.scale == "k"
    assert revenue.scale_factor == 1000
    assert revenue.values == [
        "1200", "1320", "1440", "1560", "1680", "1800",
        "1920", "2040", "2160", "2280", "2400", "2520",
    ]
    assert revenue.normalized_values == [
        "1200000", "1320000", "1440000", "1560000", "1680000", "1800000",
        "1920000", "2040000", "2160000", "2280000", "2400000", "2520000",
    ]
