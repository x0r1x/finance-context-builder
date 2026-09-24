from __future__ import annotations

from pathlib import Path

from tests.helpers.parquet import load_cells, load_parquet
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.formulas.engine import FormulaEngine


def test_shared_group_is_parsed_from_the_master_once(
    tmp_path: Path, dest: Path, monkeypatch
) -> None:
    source = tmp_path / "shared.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="A2", value="2"),
                    CellSpec(
                        addr="B1",
                        value="1",
                        formula="A1",
                        shared_si=0,
                        shared_ref="B1:B2",
                    ),
                    CellSpec(addr="B2", value="2", shared_si=0),
                    CellSpec(
                        addr="C1",
                        value="1",
                        formula="$A$1",
                        shared_si=1,
                        shared_ref="C1:C2",
                    ),
                    CellSpec(addr="C2", value="1", shared_si=1),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    raw = {(row["sheet"], row["addr"]): row for row in load_cells(dest)}
    assert raw[("Sheet1", "B1")]["shared_si"] == 0
    assert raw[("Sheet1", "B1")]["shared_master"] is True
    assert raw[("Sheet1", "B2")]["shared_si"] == 0
    assert raw[("Sheet1", "B2")]["shared_master"] is False
    assert raw[("Sheet1", "B2")]["formula_raw"] == "A2"
    assert raw[("Sheet1", "C2")]["formula_raw"] == "$A$1"

    calls = {"n": 0}
    original = FormulaEngine.parse

    def counting(self: FormulaEngine, formula: str, *, sheet: str, addr: str):
        calls["n"] += 1
        return original(self, formula, sheet=sheet, addr=addr)

    monkeypatch.setattr(FormulaEngine, "parse", counting)
    compile_workbook(dest)
    assert calls["n"] == 2

    monkeypatch.undo()
    compiled = {
        (row["sheet"], row["addr"]): row
        for row in load_parquet(dest / "ir" / "cells.parquet")
    }
    edges = load_parquet(dest / "ir" / "edges.parquet")
    direct = FormulaEngine()
    for addr in ("B1", "B2", "C1", "C2"):
        row = compiled[("Sheet1", addr)]
        parsed = direct.parse(str(row["formula_raw"]), sheet="Sheet1", addr=addr)
        assert row["formula_template"] == parsed.template
        assert row["unparsed"] is False
        got = {
            (edge["source"], edge["target"], edge["kind"])
            for edge in edges
            if edge["source"] == f"Sheet1!{addr}"
        }
        expected = {
            (edge.source, edge.target, edge.kind) for edge in parsed.edges
        }
        assert got == expected
