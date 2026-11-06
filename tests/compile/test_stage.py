from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.parquet import load_parquet
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.formulas.models import IrCell, release_cell_asts
from finance_context.formulas.stage import compile_schema_id


def test_edge_file_columns_match_compile() -> None:
    from finance_context.formulas.edge_file import _EDGE_COLUMNS
    from finance_context.formulas.stage import IR_EDGE_COLUMNS

    assert _EDGE_COLUMNS == tuple(name for name, _dtype in IR_EDGE_COLUMNS)


def test_compile_reads_presence_once(tmp_path: Path, dest: Path, monkeypatch) -> None:
    source = tmp_path / "once.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="1", formula="B1"),
                    CellSpec(addr="B1", value="2"),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    from finance_context.formulas import stage as compile_stage
    from finance_context.memory import load_presence

    calls: list[Path] = []
    real = load_presence

    def spy(path: Path, **kwargs):
        calls.append(Path(path))
        return real(path, **kwargs)

    monkeypatch.setattr(compile_stage, "load_presence", spy)
    monkeypatch.setattr("finance_context.memory.load_presence", spy)
    result = compile_workbook(dest)
    assert len(calls) == 1
    assert result.usage.rows_by_col["P&L"][2] == ((1, 1),)


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
    assert result.cell_count == 3
    assert not result.cells
    assert not result.edges
    assert result.edge_count == len(edges)
    assert result.cell_edges
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "ir" / "edges.parquet").is_file()
    assert (dest / "ir" / "cell_edges.parquet").is_file()
    cell_edges = load_parquet(dest / "ir" / "cell_edges.parquet")
    assert any(e["source"] == "P&L!D24" and e["target"] == "Inputs!D5" for e in cell_edges)
    assert all(edge["period_lag"] is None and edge["col_offset"] is None for edge in cell_edges)
    stamp = json.loads((dest / "ir" / "compile.json").read_text(encoding="utf-8"))
    assert stamp["schema_id"] == compile_schema_id()
    assert stamp["files"] == ["cells.parquet", "edges.parquet", "cell_edges.parquet"]


def test_compile_resolves_a_defined_name_into_the_edge(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "names.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Inputs",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="H32", value="1000"),
                    CellSpec(addr="B1", value="1", formula="A1/Thousand"),
                ],
            )
        ],
        defined_names=[("Thousand", "Inputs!$H$32")],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    edges = load_parquet(dest / "ir" / "edges.parquet")
    assert any(
        edge["source"] == "Inputs!B1"
        and edge["target"] == "Inputs!H32"
        and edge["unresolved"] is False
        and edge["named"] is True
        for edge in edges
    )
    assert not any(edge["target"] == "Thousand" for edge in edges)


def test_compile_template_roundtrips_through_parquet(tmp_path: Path, dest: Path) -> None:
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
    rows = [row for row in load_parquet(dest / "ir" / "cells.parquet") if row["addr"] == "B1"]
    assert rows[0]["formula_template"] == "=R[0]C[-1]"


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
    # Column B has no cells, so the whole-column sum keeps no empty members.
    assert all(edge["truncated"] is False for edge in range_edges)
    cell_edges = load_parquet(dest / "ir" / "cell_edges.parquet")
    # One edge to the empty range node, and no member edges.
    assert [str(edge["target"]) for edge in cell_edges] == ["Sheet1!B:B"]


def test_array_constant_keeps_en_argument_separator(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "array.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Inputs",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="B1", value="2"),
                    CellSpec(addr="C1", value="3", formula="SUM(A1,B1)"),
                    CellSpec(addr="D1", value="2", formula="INDEX({1;2},2)"),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    cells = load_parquet(dest / "ir" / "cells.parquet")
    by = {(c["sheet"], c["addr"]): c for c in cells}
    assert by[("Inputs", "C1")]["unparsed"] is False
    assert by[("Inputs", "C1")]["formula_template"] == "=SUM(R[0]C[-2],R[0]C[-1])"
    edges = load_parquet(dest / "ir" / "edges.parquet")
    targets = {e["target"] for e in edges if e["source"] == "Inputs!C1"}
    assert targets == {"Inputs!A1", "Inputs!B1"}
    assert by[("Inputs", "D1")]["unparsed"] is False


def test_compiled_cell_is_a_slot_object_that_still_copies_like_a_row() -> None:
    cell = IrCell(sheet="P&L", row=2, col=1, addr="A2", formula_raw="=B2", ast_json='{"op":"ref"}')
    copied = dict(cell)
    copied["merge_end_col"] = 4
    assert isinstance(cell, IrCell)
    assert cell["sheet"] == "P&L"
    assert cell.get("formula_raw") == "=B2"
    assert cell.get("missing", 7) == 7
    assert copied["merge_end_col"] == 4
    assert copied["ast_json"] == '{"op":"ref"}'
    release_cell_asts([cell, copied])
    assert cell.ast_json is None
    assert "ast_json" not in copied
