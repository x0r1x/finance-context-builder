from __future__ import annotations

import io
import json
import logging
import weakref
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.excel.a1 import parse_addr
from finance_context.formulas import compile_workbook
from finance_context.layout import layout_workbook
from finance_context.layout import stage as layout_stage
from finance_context.layout.relations import formula_relations, relation_period_cols
from finance_context.observability import JsonFormatter, job_id_var, stage_var
from finance_context.store.fs import read_parquet, write_cells_parquet


def test_layout_writes_json_registers_axes_and_no_findings(
    tmp_path: Path, dest: Path
) -> None:
    source = tmp_path / "model.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                    CellSpec(addr="C2", value="110"),
                    CellSpec(addr="A3", value="Check", type="s"),
                    CellSpec(addr="B3", value="0"),
                    CellSpec(addr="C3", value="0"),
                    CellSpec(addr="A12", value="Item", type="s"),
                    CellSpec(addr="B12", value="1 кв. 2025", type="s"),
                    CellSpec(addr="C12", value="2 кв. 2025", type="s"),
                    CellSpec(addr="A13", value="Revenue", type="s"),
                    CellSpec(addr="B13", value="20"),
                    CellSpec(addr="C13", value="30"),
                ],
            )
        ],
        shared_strings=[
            "Item",
            "2023",
            "2024E",
            "Revenue",
            "Check",
            "1 кв. 2025",
            "2 кв. 2025",
        ],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    layout = layout_workbook(dest)
    assert (dest / "layout" / "layout.json").is_file()
    assert not (dest / "candidates.json").exists()
    assert not (dest / "report.json").exists()
    assert len(layout.sheets[0].blocks) == 2
    periods = [period for axis in layout.sheets[0].axes for period in axis.periods]
    assert len(periods) >= 4
    kinds = {period.kind for period in periods}
    assert "period" in kinds
    checks = [
        row.label
        for sheet in layout.sheets
        for block in sheet.blocks
        for row in block.rows
        if row.check_row
    ]
    assert "Check" in checks


def test_layout_drops_one_sheet_before_reading_the_next(
    tmp_path: Path, dest: Path, monkeypatch
) -> None:
    source = tmp_path / "two.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="One",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="10"),
                ],
            ),
            SheetSpec(
                name="Two",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Cost", type="s"),
                    CellSpec(addr="B2", value="4"),
                ],
            ),
        ],
        shared_strings=["Item", "2023", "2024", "Revenue", "Cost"],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    held: list[weakref.ReferenceType[list]] = []
    real = layout_stage._read_layout_sheet

    class _SheetCells(list):
        pass

    def spy(dest_dir, sheet, meta):
        if held:
            assert held[-1]() is None
        rows = _SheetCells(real(dest_dir, sheet, meta))
        held.append(weakref.ref(rows))
        return rows

    monkeypatch.setattr(layout_stage, "_read_layout_sheet", spy)
    layout = layout_workbook(dest)
    assert [sheet.name for sheet in layout.sheets] == ["One", "Two"]
    assert len(held) == 2
    assert "ast_json" not in layout_stage._LAYOUT_COLUMNS
    assert "comment" not in layout_stage._LAYOUT_COLUMNS
    assert "comment" not in layout_stage._RELATION_COLUMNS


def test_layout_decodes_date_serial_from_workbook_meta(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "dates.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="44743", style=1),
                    CellSpec(addr="C1", value="44835", style=1),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="C2", value="2"),
                ],
            )
        ],
        shared_strings=["Item", "Revenue"],
        cell_xfs=[0, 14],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    layout = layout_workbook(dest)
    axis = layout.sheets[0].blocks[0].axis
    assert axis is not None
    keys = [header.period_key for header in axis.headers]
    assert keys == ["2022Q3", "2022Q4"]


def _ir(sheet: str, addr: str, value: str) -> dict:
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


def _raw(cell: dict, *, style_indent: int = 0) -> dict:
    return {
        "sheet": cell["sheet"],
        "row": cell["row"],
        "col": cell["col"],
        "addr": cell["addr"],
        "formula_raw": None,
        "cached_value": cell["cached_value"],
        "hidden": False,
        "number_format": None,
        "comment": None,
        "shared_si": None,
        "shared_master": False,
        "style_indent": style_indent,
        "outline_level": 0,
        "col_outline_level": 0,
    }


def test_layout_workbook_logs_cells_read_detect_and_write(tmp_path: Path) -> None:
    dest = tmp_path / "book"
    (dest / "raw").mkdir(parents=True)
    cells = [
        _ir("P&L", "A1", "Item"),
        _ir("P&L", "B1", "2023"),
        _ir("P&L", "C1", "2024"),
        _ir("P&L", "A2", "Revenue"),
        _ir("P&L", "B2", "100"),
        _ir("P&L", "C2", "110"),
    ]
    write_cells_parquet(
        dest / "raw" / "cells.parquet",
        [_raw(cell, style_indent=2 if cell["addr"] == "A2" else 0) for cell in cells],
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("finance_context.pipeline")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    job = job_id_var.set("job-layout")
    stage = stage_var.set("layout")
    try:
        layout = layout_workbook(dest, cells=cells, edges=[])
    finally:
        job_id_var.reset(job)
        stage_var.reset(stage)
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    revenue = next(row for row in layout.sheets[0].blocks[0].rows if row.label == "Revenue")
    assert revenue.indent == 2
    written = json.loads((dest / "layout" / "layout.json").read_text(encoding="utf-8"))
    written_rows = written["sheets"][0]["blocks"][0]["rows"]
    assert next(row for row in written_rows if row["label"] == "Revenue")["indent"] == 2

    payloads = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert [row["event"] for row in payloads] == [
        "layout_cells_read",
        "layout_detect",
        "layout_write",
    ]
    assert [row["msg"] for row in payloads] == [
        "raw/cells.parquet read done",
        "detect_layout done",
        "layout.json write done",
    ]
    for row in payloads:
        assert row["logger"] == "finance_context.pipeline"
        assert row["level"] == "INFO"
        assert row["job_id"] == "job-layout"
        assert row["stage"] == "layout"
        assert isinstance(row["duration_ms"], int)
        assert row["duration_ms"] >= 0


def test_relation_scan_matches_the_full_cell_list(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "model.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                    CellSpec(addr="C2", value="110", formula="=B2+10"),
                    CellSpec(addr="A3", value="Cost", type="s"),
                    CellSpec(addr="B3", value="40"),
                    CellSpec(addr="C3", value="30", formula="=B3-10"),
                    CellSpec(addr="A4", value="Total", type="s"),
                    CellSpec(addr="B4", value="140", formula="=SUM(B2:B3)"),
                    CellSpec(addr="C4", value="140", formula="=SUM(C2:C3)"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024", "Revenue", "Cost", "Total"],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    layout = layout_workbook(dest)
    full = read_parquet(dest / "ir" / "cells.parquet")
    expected = formula_relations(layout, full)
    scan = layout_stage.load_relation_cells(dest, layout)
    found = formula_relations(layout, scan)
    assert [item.model_dump() for item in found] == [item.model_dump() for item in expected]
    assert found and found[0].kind == "aggregate"
    rows: set[int] = set()
    cols: set[int] = set()
    for sheet in layout.sheets:
        for block in sheet.blocks:
            picked = relation_period_cols(sheet, block)
            if not picked:
                continue
            cols.update(picked)
            rows.update(row.row for row in block.rows)
    analysed = [
        cell
        for cell in full
        if int(cell["row"]) in rows
        and int(cell["col"]) in cols
        and (cell.get("formula_raw") or cell.get("formula_template") or cell.get("ast_json"))
    ]
    assert scan.count == len(analysed)
    assert scan.count < len(full)
    assert list(scan) == []
    assert scan.count == len(analysed)
