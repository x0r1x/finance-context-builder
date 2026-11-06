"""Formula text stays off the cycle-matrix peak.

The book is thousands of long formulas and one short edge each. Edges and the
matrix are small, so RSS at the start of ``_cycles_packed`` is the formula
dictionary. Keeping the text there, which is what graph did before, has to
cost about the size of those strings. The bytes of ``graph.json`` stay the same.
"""

from __future__ import annotations

import gc
import subprocess
import sys
from pathlib import Path

import pytest

from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS
from finance_context.graph.stage import build_formula_graph
from finance_context.layout.models import Block, Layout, SheetLayout
from finance_context.memory import rss_bytes
from finance_context.settings import Settings
from finance_context.store.fs import write_parquet

_COUNT = 5_000
_CELL_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("row", "INTEGER"),
    ("col", "INTEGER"),
    ("addr", "VARCHAR"),
    ("formula_raw", "VARCHAR"),
    ("cached_value", "VARCHAR"),
)

_CHILD = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tests.graph.test_formula_text_rss import emit

emit(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
"""


def _formula() -> str:
    return "=" + ("A1+" * 2_000)


def _write_book(book: Path) -> int:
    formula = _formula()
    cells = [("S", row, 1, f"A{row}", formula, None) for row in range(1, _COUNT + 1)]
    edges = [_edge(f"S!A{row}", "S!B1") for row in range(1, _COUNT + 1)]
    write_parquet(book / "ir" / "cells.parquet", _CELL_COLUMNS, cells)
    write_parquet(book / "ir" / "cell_edges.parquet", IR_CELL_EDGE_COLUMNS, edges)
    return len(formula) * _COUNT


def _edge(source: str, target: str) -> tuple:
    return (
        source,
        target,
        "ref",
        False,
        False,
        False,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        False,
    )


def _layout() -> Layout:
    sheet = SheetLayout(name="S", blocks=[Block(block_id="b", label_col=1, rows=[])])
    return Layout(sheets=[sheet])


def emit(book: Path, dest: Path, keep: str) -> None:
    """Print RSS at the cycle matrix. ``keep=1`` loads formula text first."""
    import finance_context.graph.stage as stage

    if keep == "1":
        real_load = stage._load_formulas

        def load_with_text(path, row_meta, period_by_cell):
            found = real_load(path, row_meta, period_by_cell)
            stage._attach_formula_text(path, found)
            return found

        stage._load_formulas = load_with_text
    seen: dict[str, int] = {}
    real_cycles = stage._cycles_packed

    def cycles(*args, **kwargs):
        gc.collect()
        seen["rss"] = rss_bytes()
        return real_cycles(*args, **kwargs)

    stage._cycles_packed = cycles
    build_formula_graph(
        dest,
        job_id="formula-text",
        layout=_layout(),
        book_dir=book,
        settings=Settings(data_dir=dest / "data", _env_file=None),
    )
    print(f"RSS {seen['rss']}")


def _run(repo: str, book: Path, dest: Path, keep: str) -> int:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, repo, str(book), str(dest), keep],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    line = next(line for line in proc.stdout.splitlines() if line.startswith("RSS "))
    return int(line.split()[1])


def test_cycle_peak_drops_when_formula_text_stays_on_disk(tmp_path: Path) -> None:
    if not Path("/proc/self/statm").is_file():
        pytest.skip("RSS is read from /proc")
    book = tmp_path / "book"
    payload = _write_book(book)
    repo = str(Path(__file__).resolve().parents[2])
    kept = _run(repo, book, tmp_path / "kept", "1")
    deferred = _run(repo, book, tmp_path / "deferred", "0")
    assert deferred < kept
    assert kept - deferred >= payload // 2
    kept_json = (tmp_path / "kept" / "graph" / "graph.json").read_bytes()
    deferred_json = (tmp_path / "deferred" / "graph" / "graph.json").read_bytes()
    assert kept_json == deferred_json
    assert _formula() in deferred_json.decode("utf-8")
