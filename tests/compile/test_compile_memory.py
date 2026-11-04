"""Compile memory stays linear in cells and below a per-id string set."""

from __future__ import annotations

import gc
import subprocess
import sys
import tracemalloc
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.known import KnownNodes
from finance_context.formulas.stage import canonical_node_id, compile_workbook
from finance_context.settings import Settings

# tracemalloc counts every thread in the process. The suite leaves the log
# listener and job-progress threads alive, and a burst on one of them lands
# inside this window. CI run 41785689099 traced 8_087_346 bytes that way, about
# twice the compile. The child has no such threads.
_CHILD = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tests.compile.test_compile_memory import emit_peaks

emit_peaks(Path(sys.argv[2]))
"""


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None)


def _book(path: Path, values: int, formulas: int) -> None:
    cells = [CellSpec(addr=f"A{row}", value="1") for row in range(1, values + 1)]
    cells.extend(
        CellSpec(addr=f"B{row}", value="2", formula=f"=A{row}+A{row}")
        for row in range(1, formulas + 1)
    )
    build_xlsx(path, sheets=[SheetSpec(name="Data", cells=cells)])


def _compile_peak(tmp_path: Path, values: int, formulas: int) -> int:
    dest = tmp_path / f"v{values}-f{formulas}"
    dest.mkdir(parents=True)
    _book(dest / "book.xlsx", values, formulas)
    settings = _settings(tmp_path)
    parse_workbook(dest / "book.xlsx", dest, settings=settings)
    # Drop import garbage and the parsed book before the window. Automatic
    # collections allocate their own lists; those are not compile traffic.
    gc.collect()
    paused = gc.get_threshold()
    gc.set_threshold(0)
    tracemalloc.start()
    try:
        compile_workbook(dest, settings=settings)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        gc.set_threshold(*paused)
    return peak


def emit_peaks(root: Path) -> None:
    """Print the small and large peaks. A warmup compile pays for imports first."""
    _compile_peak(root / "warm", 8, 2)
    small = _compile_peak(root / "small", 1_500, 400)
    large = _compile_peak(root / "large", 6_000, 1_600)
    print(f"PEAKS {small} {large}")


def test_known_nodes_peak_is_below_the_id_set() -> None:
    count = 20_000
    gc.collect()
    tracemalloc.start()
    try:
        index = KnownNodes()
        for row in range(1, count + 1):
            index.add("Sheet", f"A{row}")
        assert len(index) == count
        assert f"Sheet!A{count}" in index
        _current, index_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del index
    gc.collect()
    tracemalloc.start()
    try:
        old = {canonical_node_id("Sheet", f"A{row}") for row in range(1, count + 1)}
        assert len(old) == count
        _current, set_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert index_peak < set_peak


def test_compile_tracemalloc_scales_with_cells_and_formulas(tmp_path: Path) -> None:
    repo = str(Path(__file__).resolve().parents[2])
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, repo, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    line = next(line for line in proc.stdout.splitlines() if line.startswith("PEAKS "))
    small, large = (int(part) for part in line.split()[1:])
    # 4× cells and 4× formulas. Main (d12fba5) traced 4_411_834 bytes on the
    # larger book; this compile has to stay at or under that peak.
    assert large < small * 5
    assert large <= 4_411_834
