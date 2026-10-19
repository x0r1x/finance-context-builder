"""Alignment indent, row outline, and a standalone RURm unit caption."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.parquet import load_cells, load_workbook_json
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.context.build import build_context
from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.layout import layout_workbook
from finance_context.store.fs import read_parquet


def _t(addr: str, value: str, *, style: int | None = None) -> CellSpec:
    return CellSpec(addr=addr, value=value, type="inlineStr", style=style)


def _n(addr: str, value: str, *, formula: str | None = None) -> CellSpec:
    return CellSpec(addr=addr, value=value, formula=formula)


def _years(row: int, cols: str = "CDE") -> list[CellSpec]:
    years = ("2020", "2021", "2022")
    return [_t(f"{col}{row}", year) for col, year in zip(cols, years, strict=True)]


def _book() -> list[SheetSpec]:
    output = SheetSpec(
        name="Output",
        summary_below=False,
        row_outline={6: 1, 7: 2, 8: 2, 9: 1},
        cells=[
            _t("B1", "Cash Flow Summary"),
            _t("B2", "RURm"),
            *_years(2, "DEF"),
            _t("B3", "CF from Operations"),
            _n("D3", "1109"),
            _n("E3", "1200"),
            _n("F3", "1300"),
            _t("B4", "Local cost"),
            _t("C4", "k£"),
            _n("D4", "5"),
            _n("E4", "6"),
            _n("F4", "7"),
            _t("B5", "CF from Investing Activities"),
            _t("B6", "  CAPEX"),
            _n("D6", "2935", formula="D7+D8"),
            _t("B7", "IaaS CAPEX", style=1),
            _n("D7", "1817"),
            _n("E7", "1"),
            _n("F7", "1"),
            _t("B8", "DC CAPEX", style=1),
            _n("D8", "1118"),
            _t("B9", "Sale / (Purchase) of assets (ex. VAT)"),
            _n("D9", "10"),
            _n("E9", "11"),
            _n("F9", "12"),
        ],
    )
    style = SheetSpec(
        name="Style",
        cells=[
            _t("B1", "Item"),
            *_years(1),
            _t("B2", "CF from Investing Activities"),
            _t("B3", "CAPEX", style=1),
            _n("C3", "10"),
            _n("D3", "11"),
            _n("E3", "12"),
            _t("B4", "IaaS CAPEX", style=2),
            _n("C4", "1817"),
            _n("D4", "1"),
            _n("E4", "1"),
        ],
    )
    below = SheetSpec(
        name="Below",
        summary_below=True,
        row_outline={2: 2, 3: 2, 4: 1},
        cells=[
            _t("B1", "Item"),
            *_years(1),
            _t("B2", "IaaS CAPEX"),
            _n("C2", "1817"),
            _n("D2", "1"),
            _n("E2", "1"),
            _t("B3", "DC CAPEX"),
            _n("C3", "1118"),
            _t("B4", "CAPEX"),
            _n("C4", "2935"),
            _n("D4", "1"),
            _n("E4", "1"),
        ],
    )
    cols = SheetSpec(
        name="Cols",
        col_outline=[(3, 3, 2)],
        cells=[
            _t("B1", "Item"),
            *_years(1),
            _t("B2", "Section"),
            _t("B3", "Article"),
            _n("C3", "10"),
            _n("D3", "11"),
            _n("E3", "12"),
        ],
    )
    format_pr = SheetSpec(
        name="FormatPr",
        summary_below=False,
        summary_below_parent="sheetFormatPr",
        row_outline={2: 1, 3: 2},
        cells=[
            _t("B1", "Item"),
            *_years(1),
            _t("B2", "CAPEX"),
            _n("C2", "2935"),
            _n("D2", "1"),
            _n("E2", "1"),
            _t("B3", "IaaS CAPEX"),
            _n("C3", "1817"),
            _n("D3", "1"),
            _n("E3", "1"),
        ],
    )
    banner = SheetSpec(
        name="Banner",
        cells=[
            _t("B1", "Item"),
            *_years(1),
            _t("B2", "RURm"),
            _t("B3", "Revenue"),
            _n("C3", "1817"),
            _n("D3", "1"),
            _n("E3", "1"),
        ],
    )
    # One outline band under the next section. Level 1 is the band, not a new parent.
    flat = SheetSpec(
        name="Flat",
        summary_below=True,
        row_outline={3: 1, 5: 1},
        cells=[
            _t("B1", "Item"),
            *_years(1),
            _t("B2", "Cashflow Statement"),
            _t("B3", "Uses of funds"),
            _n("C3", "10"),
            _n("D3", "11"),
            _n("E3", "12"),
            _t("B4", "Income Statement"),
            _t("B5", "Revenue"),
            _n("C5", "20"),
            _n("D5", "21"),
            _n("E5", "22"),
        ],
    )
    return [output, style, below, cols, format_pr, banner, flat]


def _parents(layout, sheet: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for item in layout.sheets:
        if item.name != sheet:
            continue
        by_row = {row.row: row.label for block in item.blocks for row in block.rows}
        for block in item.blocks:
            for row in block.rows:
                parent = by_row.get(row.parent_row) if row.parent_row else None
                out[row.label] = parent
    return out


def test_outline_indent_and_rurm_survive_parse_and_context(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "selectel.xlsx"
    build_xlsx(source, sheets=_book(), xf_indents=[0, 1, 2])
    parse_workbook(source, dest)
    meta = load_workbook_json(dest)
    flags = {sheet["name"]: sheet.get("summary_below", True) for sheet in meta["sheets"]}
    assert flags["Output"] is False
    assert flags["FormatPr"] is False
    assert flags["Below"] is True
    below = next(sheet for sheet in meta["sheets"] if sheet["name"] == "Below")
    assert "summary_below" not in below

    raw = {(cell["sheet"], cell["addr"]): cell for cell in load_cells(dest)}
    iaas = raw[("Output", "B7")]
    assert iaas["style_indent"] == 1
    assert iaas["outline_level"] == 2
    assert iaas["col_outline_level"] == 0
    assert raw[("Output", "B6")]["outline_level"] == 1
    assert raw[("Output", "B6")]["cached_value"] == "  CAPEX"
    assert raw[("Cols", "C3")]["col_outline_level"] == 2
    assert raw[("Cols", "C3")]["outline_level"] == 0
    assert raw[("Cols", "B3")]["outline_level"] == 0

    compile_workbook(dest)
    ir = read_parquet(dest / "ir" / "cells.parquet")
    assert "style_indent" not in ir[0]
    layout = layout_workbook(dest)
    output = _parents(layout, "Output")
    # Two spaces make CAPEX indent 2; IaaS has only alignment indent 1.
    # Outline level 2 under level 1 still hangs IaaS off CAPEX.
    assert output["IaaS CAPEX"] == "CAPEX"
    assert output["DC CAPEX"] == "CAPEX"
    assert output["CAPEX"] == "CF from Investing Activities"
    assert output["Sale / (Purchase) of assets (ex. VAT)"] == "CF from Investing Activities"
    rows = {
        row.label: row
        for sheet in layout.sheets
        if sheet.name == "Output"
        for block in sheet.blocks
        for row in block.rows
    }
    assert rows["CAPEX"].indent == 2
    assert rows["IaaS CAPEX"].indent == 1

    assert _parents(layout, "Style")["IaaS CAPEX"] == "CAPEX"
    assert _parents(layout, "Below")["IaaS CAPEX"] == "CAPEX"
    assert _parents(layout, "Below")["DC CAPEX"] == "CAPEX"
    assert _parents(layout, "FormatPr")["IaaS CAPEX"] == "CAPEX"
    assert _parents(layout, "Cols")["Article"] == "Section"
    flat_parents = _parents(layout, "Flat")
    assert flat_parents["Uses of funds"] == "Cashflow Statement"
    assert flat_parents["Revenue"] == "Income Statement"

    doc = build_context(
        job_id="selectel",
        workbook_meta=json.loads((dest / "raw" / "workbook.json").read_text(encoding="utf-8")),
        cells=ir,
        layout=layout,
    )
    lines = {
        (row.sheet, row.label): row
        for block in doc.blocks
        for row in block.rows
    }
    operations = lines[("Output", "CF from Operations")]
    assert operations.hints.currency == "RUB"
    assert operations.hints.scale == "m"
    assert operations.scale_factor == 1_000_000
    assert "1109000000" in operations.normalized_values
    assert operations.parent_label == "Cash Flow Summary"

    local = lines[("Output", "Local cost")]
    assert (local.hints.currency, local.hints.scale) == ("GBP", "k")
    assert local.scale_factor == 1_000
    assert "5000" in local.normalized_values

    iaas_row = lines[("Output", "IaaS CAPEX")]
    assert iaas_row.parent_label == "CAPEX"
    assert iaas_row.hints.scale == "m"
    assert iaas_row.scale_factor == 1_000_000
    assert "1817000000" in iaas_row.normalized_values

    revenue = lines[("Banner", "Revenue")]
    assert (revenue.hints.currency, revenue.hints.scale) == ("RUB", "m")
    assert revenue.scale_factor == 1_000_000
    assert "1817000000" in revenue.normalized_values
