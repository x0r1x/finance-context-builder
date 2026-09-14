from __future__ import annotations

from pathlib import Path

from tests.helpers.parquet import load_parquet
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.ir.catalog import IrCatalog


def test_compile_writes_ir_artifacts_and_templates(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "model.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Inputs",
                cells=[CellSpec(addr="D5", value="0.2")],
            ),
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="D24", value="1", formula="Inputs!D5"),
                    CellSpec(addr="E24", value="1", formula="D24"),
                ],
            ),
        ],
    )
    parse_workbook(source, dest)
    result = compile_workbook(dest)
    cells = load_parquet(dest / "ir" / "cells.parquet")
    edges = load_parquet(dest / "ir" / "edges.parquet")
    by = {(c["sheet"], c["addr"]): c for c in cells}
    assert by[("P&L", "D24")]["formula_template"] == "=Inputs!R[-19]C[0]"
    assert by[("P&L", "D24")]["unparsed"] is False
    assert any(e["kind"] == "cross_sheet" and e["target"] == "Inputs!D5" for e in edges)
    assert result.csr.matrix.shape[0] == result.csr.matrix.shape[1]
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "ir" / "edges.parquet").is_file()


def test_compile_does_not_reparse_when_reading_ir_via_catalog(
    tmp_path: Path, dest: Path
) -> None:
    source = tmp_path / "ok.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="B1", value="1", formula="A1"),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    catalog = IrCatalog.open(dest)
    try:
        rows = catalog.sql("SELECT formula_template FROM cells WHERE addr = 'B1'").fetchall()
        assert rows[0][0] == "=R[0]C[-1]"
    finally:
        catalog.close()


def test_open_column_range_is_truncated_in_edges(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "wide.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[CellSpec(addr="A1", value="1", formula="SUM(B:B)")],
            )
        ],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    edges = load_parquet(dest / "ir" / "edges.parquet")
    range_edges = [e for e in edges if e["kind"] == "range"]
    assert range_edges
    assert any(e["truncated"] for e in range_edges)
