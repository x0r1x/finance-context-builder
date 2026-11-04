"""Doubling a book stays linear.

Wall time moves around under one CPU, so the fast checks count ``is_unit_text``
calls or formula edges. The text book is traced in a fresh interpreter: the
suite's log threads have landed inside a tracemalloc window before.
"""

from __future__ import annotations

import gc
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest
from tests.fixtures.synthetic import hops_tl, outline, rel, sumif, text

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout import rows as row_mod
from finance_context.layout.stage import layout_workbook
from finance_context.settings import Settings

# 4× the rows. A quadratic shared-string table would be about 16×.
# 2,000 rows traced 2,019,908 bytes and 8,000 traced 4,161,750 (same child,
# two runs of the smaller size matched to the byte). The ceiling is that
# 8,000-row peak with headroom. The ratio still has to stay under 5×.
_TEXT_SMALL = 2_000
_TEXT_LARGE = 8_000
_TEXT_LARGE_PEAK = 6_500_000

_CHILD = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from tests.synthetic.test_growth import emit_peaks

emit_peaks(Path(sys.argv[2]))
"""


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None)


def _unit_text_calls(path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    dest = tmp_path / path.stem
    dest.mkdir(parents=True)
    settings = _settings(tmp_path)
    parse_workbook(path, dest, settings=settings)
    compile_workbook(dest, settings=settings)
    calls = 0
    real = row_mod.is_unit_text

    def wrapped(value: str | None) -> bool:
        nonlocal calls
        calls += 1
        return real(value)

    monkeypatch.setattr(row_mod, "is_unit_text", wrapped)
    layout_workbook(dest, settings=settings)
    return calls


def _assert_linear(small: int, large: int) -> None:
    assert small > 0
    assert large <= small * 2.6
    assert large <= small * 4


def test_rel_parent_walk_is_linear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small_book = tmp_path / "rel-small.xlsx"
    large_book = tmp_path / "rel-large.xlsx"
    rel(small_book, 200)
    rel(large_book, 400)
    small = _unit_text_calls(small_book, tmp_path / "s", monkeypatch)
    large = _unit_text_calls(large_book, tmp_path / "l", monkeypatch)
    _assert_linear(small, large)


def test_hop_timeline_parent_walk_is_linear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_book = tmp_path / "hop-small.xlsx"
    large_book = tmp_path / "hop-large.xlsx"
    hops_tl(small_book, 300)
    hops_tl(large_book, 600)
    small = _unit_text_calls(small_book, tmp_path / "s", monkeypatch)
    large = _unit_text_calls(large_book, tmp_path / "l", monkeypatch)
    _assert_linear(small, large)


def test_outline_parent_walk_is_linear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small_book = tmp_path / "outline-small.xlsx"
    large_book = tmp_path / "outline-large.xlsx"
    outline(small_book, cycles=4)
    outline(large_book, cycles=8)
    small = _unit_text_calls(small_book, tmp_path / "s", monkeypatch)
    large = _unit_text_calls(large_book, tmp_path / "l", monkeypatch)
    _assert_linear(small, large)


def _sumif_edges(rows: int, tmp_path: Path) -> int:
    path = tmp_path / f"sumif-{rows}.xlsx"
    sumif(path, rows)
    dest = tmp_path / f"job-{rows}"
    dest.mkdir()
    settings = _settings(tmp_path)
    parse_workbook(path, dest, settings=settings)
    compiled = compile_workbook(dest, settings=settings)
    return len(compiled.cell_edges)


def test_sumif_edges_grow_linearly(tmp_path: Path) -> None:
    small = _sumif_edges(200, tmp_path / "s")
    large = _sumif_edges(400, tmp_path / "l")
    assert large / small < 2.3


def _parse_peak(root: Path, rows: int) -> int:
    dest = root / "job"
    dest.mkdir(parents=True)
    source = root / "book.xlsx"
    text(source, rows, strlen=32)
    settings = _settings(root)
    gc.collect()
    paused = gc.get_threshold()
    gc.set_threshold(0)
    tracemalloc.start()
    try:
        parse_workbook(source, dest, settings=settings)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        gc.set_threshold(*paused)
    return peak


def emit_peaks(root: Path) -> None:
    """Print the small and large parse peaks. A warmup parse pays for imports."""
    _parse_peak(root / "warm", 20)
    small = _parse_peak(root / "small", _TEXT_SMALL)
    large = _parse_peak(root / "large", _TEXT_LARGE)
    print(f"PEAKS {small} {large}")


def test_text_parse_tracemalloc_scales(tmp_path: Path) -> None:
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
    assert large < small * 5
    if _TEXT_LARGE_PEAK:
        assert large <= _TEXT_LARGE_PEAK
