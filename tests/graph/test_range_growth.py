"""Edges and context.json grow linearly with rows for copied-down large ranges.

Two shapes that were quadratic before range nodes: a whole-column SUMIF copied
down every row, and a running total SUM($B$2:B{n}). Doubling the rows should
roughly double the edges and the context file, not quadruple them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.excel import parse_workbook
from finance_context.formulas import csr
from finance_context.layout import adjacency
from finance_context.memory import assess_raw
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet

SIZES = (5000, 10000, 20000)


def _book(path: Path, rows: int) -> Path:
    data = [CellSpec(addr="A1", value="key", type="s"), CellSpec(addr="B1", value="2025", type="s")]
    calc = [CellSpec(addr="A1", value="key", type="s"), CellSpec(addr="B1", value="2025", type="s")]
    for row in range(2, rows + 2):
        data.append(CellSpec(addr=f"A{row}", value=str(row % 50)))
        data.append(CellSpec(addr=f"B{row}", value=str(row)))
        calc.append(CellSpec(addr=f"A{row}", value=str(row % 50)))
        calc.append(
            CellSpec(addr=f"B{row}", value="1", formula=f"SUMIF(Data!$A:$A,$A{row},Data!$B:$B)")
        )
        calc.append(CellSpec(addr=f"C{row}", value="1", formula=f"SUM(Data!$B$2:B{row})"))
    return build_xlsx(
        path,
        sheets=[SheetSpec(name="Data", cells=data), SheetSpec(name="Calc", cells=calc)],
        shared_strings=["key", "2025"],
    )


def _measure(root: Path, rows: int) -> tuple[int, int, int]:
    source = _book(root / f"book{rows}.xlsx", rows)
    dest = root / f"job{rows}"
    dest.mkdir()
    (dest / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (dest / "source" / "source.xlsx").write_bytes(source.read_bytes())
    Pipeline(Settings(data_dir=root / f"data{rows}", _env_file=None)).run(
        dest, job_id=f"g{rows}", source_filename="book.xlsx"
    )
    cell_edges = len(read_parquet(dest / "ir" / "cell_edges.parquet"))
    graph_edges = len(read_parquet(dest / "graph" / "edges.parquet"))
    context_bytes = (dest / "context" / "context.json").stat().st_size
    return cell_edges, graph_edges, context_bytes


@pytest.mark.slow
def test_edges_and_context_grow_linearly(tmp_path: Path) -> None:
    measured = [_measure(tmp_path, rows) for rows in SIZES]
    for smaller, larger in zip(measured, measured[1:], strict=False):
        for before, after in zip(smaller, larger, strict=True):
            # Linear is about 2x per doubling; quadratic would be about 4x.
            assert after / before < 2.3, measured


def test_estimate_is_not_above_per_formula_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = SIZES[0]
    dest = tmp_path / "parsed"
    parse_workbook(_book(tmp_path / "book.xlsx", rows), dest)
    with_nodes = assess_raw(dest)
    monkeypatch.setattr(csr, "range_node_for", lambda target, usage: None)
    monkeypatch.setattr(adjacency, "range_node_for", lambda target, usage: None)
    expanded = assess_raw(dest)
    assert with_nodes.formulas == expanded.formulas == 2 * rows
    # Per row: SUMIF has two whole-column nodes and one cell ref, the running
    # total B2:B{n} is one node once it is over 64 cells and n cells before that.
    running = sum(n if n <= 64 else 1 for n in range(1, rows + 1))
    assert with_nodes.expanded_cells == 3 * rows + running
    assert with_nodes.estimate_bytes <= expanded.estimate_bytes
