"""Derived-column counts are one pass per column set, and they match the per-band walk."""

from __future__ import annotations

import random

import pytest

from finance_context.excel.a1 import index_to_col, parse_addr
from finance_context.layout.cells import _cell_at
from finance_context.layout.detect import detect_layout
from finance_context.layout.markers import (
    _SHARE,
    DerivedScan,
    _formula_is_derived,
    _has_formula,
    _row_numbers,
    _value_roles,
    derived_columns,
)
from finance_context.layout.models import AxisPeriod


def _cell(addr: str, value: str | None, *, formula: str | None = None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": "P&L",
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": formula,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def _periods(cols: list[int]) -> list[AxisPeriod]:
    return [AxisPeriod(col=col, text=str(col), period_key="2024") for col in cols]


def _per_band(
    cols: list[int],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
) -> dict[int, tuple[int, int, int, int]]:
    """The old count: every row under ``header_row``, one band at a time."""
    formula_hits = dict.fromkeys(cols, 0)
    formula_seen = dict.fromkeys(cols, 0)
    value_hits = dict.fromkeys(cols, 0)
    value_seen = dict.fromkeys(cols, 0)
    for row_n, cells in by_row.items():
        if row_n <= header_row:
            continue
        numbers = _row_numbers(cells, cols, date1904)
        valued = [col for col in cols if _has_formula(cells, col) is False and col in numbers]
        roles = _value_roles(cols, numbers) if numbers else set()
        for col in cols:
            if _has_formula(cells, col) is None:
                continue
            if _has_formula(cells, col):
                formula_seen[col] += 1
                cell = _cell_at(cells, col)
                formula = str(cell.get("formula_raw") or "") if cell else ""
                if formula and _formula_is_derived(formula, col, row_n, cols):
                    formula_hits[col] += 1
                continue
            if col not in valued:
                continue
            value_seen[col] += 1
            if col in roles:
                value_hits[col] += 1
    return {
        col: (formula_seen[col], formula_hits[col], value_seen[col], value_hits[col])
        for col in cols
    }


def _assert_same(
    by_row: dict[int, list[dict]],
    headers: list[int],
    column_sets: list[list[int]],
    *,
    date1904: bool,
) -> None:
    scan = DerivedScan(by_row, date1904, headers)
    for cols in column_sets:
        for header in headers:
            assert scan.counts(list(cols), header) == _per_band(cols, by_row, header, date1904)
            again = DerivedScan(by_row, date1904, headers)
            assert derived_columns(_periods(cols), again, header) == _found(
                _per_band(cols, by_row, header, date1904)
            )


def _found(counts: dict[int, tuple[int, int, int, int]]) -> set[int]:
    found: set[int] = set()
    for col, (formula_seen, formula_hits, value_seen, value_hits) in counts.items():
        if formula_seen and formula_hits / formula_seen >= _SHARE:
            found.add(col)
        elif value_seen >= 2 and value_hits / value_seen >= _SHARE:
            found.add(col)
    return found


def test_counts_match_the_per_band_walk_on_fixed_layouts() -> None:
    cols = [2, 3, 4]
    by_row: dict[int, list[dict]] = {
        1: [_cell("B1", "1"), _cell("C1", "1"), _cell("D1", "2")],
        4: [_cell("B4", "10"), _cell("C4", "20"), _cell("D4", "30")],
        5: [
            _cell("B5", "3"),
            _cell("C5", "4"),
            _cell("D5", "7", formula="=SUM(B5:C5)"),
        ],
        9: [_cell("B9", "0"), _cell("C9", "1"), _cell("D9", "1")],
    }
    _assert_same(by_row, [0, 1, 3, 4, 8, 9, 12], [cols, [4, 3, 2], [2, 4]], date1904=False)
    _assert_same({}, [1, 5], [cols], date1904=True)
    # A header the scan was not told about still counts only the rows under it.
    scan = DerivedScan(by_row, False, [4])
    assert scan.counts(cols, 2) == _per_band(cols, by_row, 2, False)


def test_random_band_layouts_match_the_per_band_walk() -> None:
    rnd = random.Random(20260304)
    for _ in range(40):
        cols = list(range(2, rnd.randint(4, 7)))
        if rnd.random() < 0.5:
            rnd.shuffle(cols)
        by_row: dict[int, list[dict]] = {}
        for row_n in range(1, rnd.randint(2, 36)):
            if rnd.random() < 0.15:
                continue
            cells: list[dict] = []
            for col in sorted(cols):
                if rnd.random() < 0.1:
                    continue
                formula = None
                if rnd.random() < 0.25 and col == max(cols):
                    left = index_to_col(min(cols))
                    right = index_to_col(max(cols) - 1)
                    formula = f"=SUM({left}{row_n}:{right}{row_n})"
                elif rnd.random() < 0.15:
                    formula = f"={index_to_col(cols[0])}{row_n}-{index_to_col(cols[1])}{row_n}"
                value = str(rnd.choice([0, 1, 2, 5, 10, row_n, row_n * 3]))
                cells.append(_cell(f"{index_to_col(col)}{row_n}", value, formula=formula))
            if cells:
                by_row[row_n] = cells
        present = sorted(by_row)
        headers = [rnd.choice([-2, 0, 1, 3, 10, 50, *present])] if present else [1]
        headers.extend(rnd.sample(present, k=min(len(present), rnd.randint(1, 5))))
        sets = [list(cols), list(reversed(cols)), cols[:2]]
        _assert_same(by_row, headers, sets, date1904=rnd.choice([False, True]))


def _shared_body(bands: int, rows: int) -> tuple[dict[int, list[dict]], list[int]]:
    """``bands`` headers over one body of ``rows``. Every header used to rescan that body."""
    by_row: dict[int, list[dict]] = {}
    headers = list(range(1, bands + 1))
    for header in headers:
        by_row[header] = [
            _cell(f"{index_to_col(col)}{header}", str(2019 + col)) for col in (2, 3, 4)
        ]
    for offset in range(rows):
        row_n = bands + 1 + offset
        by_row[row_n] = [
            _cell(f"B{row_n}", "10"),
            _cell(f"C{row_n}", "20"),
            _cell(f"D{row_n}", "30"),
        ]
    return by_row, headers


def test_doubling_bands_and_rows_does_not_quadruple_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visits = 0

    def counting(cells: list[dict], cols: list[int], date1904: bool) -> dict[int, float]:
        nonlocal visits
        visits += 1
        return _row_numbers(cells, cols, date1904)

    monkeypatch.setattr("finance_context.layout.markers._row_numbers", counting)
    cols = [2, 3, 4]

    def measured(bands: int, rows: int) -> int:
        nonlocal visits
        visits = 0
        by_row, headers = _shared_body(bands, rows)
        scan = DerivedScan(by_row, False, headers)
        for header in headers:
            derived_columns(_periods(cols), scan, header)
        return visits

    small = measured(4, 80)
    large = measured(8, 160)
    # Headers sit on top of one body, so the old walk was bands × rows.
    # One pass reads the body plus the headers under the first one.
    assert small == (4 - 1) + 80
    assert large == (8 - 1) + 160
    assert large < small * 4


def test_one_scan_serves_every_band(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[int] = []
    real = DerivedScan.__init__

    def wrapped(self: DerivedScan, *args: object, **kwargs: object) -> None:
        created.append(1)
        real(self, *args, **kwargs)

    monkeypatch.setattr(DerivedScan, "__init__", wrapped)
    cells: list[dict] = []
    row_n = 1
    for start in (2020, 2024):
        cells.append(_cell(f"A{row_n}", "Item"))
        for offset, year in enumerate((start, start + 1, start + 2)):
            cells.append(_cell(f"{index_to_col(2 + offset)}{row_n}", str(year)))
        row_n += 1
        for _ in range(3):
            cells.append(_cell(f"A{row_n}", "Revenue"))
            cells.append(_cell(f"B{row_n}", "10"))
            cells.append(_cell(f"C{row_n}", "20"))
            cells.append(_cell(f"D{row_n}", "30"))
            row_n += 1
    detect_layout(cells)
    assert created == [1]
