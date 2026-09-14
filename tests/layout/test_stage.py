from __future__ import annotations

from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.ir.catalog import IrCatalog
from finance_context.layout import layout_workbook


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
    catalog = IrCatalog.open(dest)
    try:
        layout = layout_workbook(dest, catalog)
        assert (dest / "layout.json").is_file()
        assert not (dest / "candidates.json").exists()
        assert not (dest / "report.json").exists()
        assert len(layout.sheets[0].blocks) == 2
        headers = catalog.sql(
            "SELECT sheet, block_id, col, role FROM axis_headers ORDER BY block_id, col"
        ).fetchall()
        assert len(headers) >= 4
        roles = {row[3] for row in headers}
        assert "historical" in roles or "forecast" in roles
        checks = catalog.sql(
            "SELECT label FROM layout_rows WHERE check_row"
        ).fetchall()
        assert ("Check",) in checks
    finally:
        catalog.close()


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
    catalog = IrCatalog.open(dest)
    try:
        layout = layout_workbook(dest, catalog)
        keys = [h.period_key for h in layout.sheets[0].blocks[0].axis.headers]
        assert keys == ["2022Q3", "2022Q4"]
    finally:
        catalog.close()
