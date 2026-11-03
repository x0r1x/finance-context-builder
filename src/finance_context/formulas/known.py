"""Row-run index of canonical cell ids for compile.

Same answers as a ``set`` of :func:`finance_context.formulas.stage.canonical_node_id`
values, without storing one string per cell.
"""

from __future__ import annotations

from finance_context.excel.a1 import format_addr, parse_addr
from finance_context.formulas.csr import _merge_spans, _span_has


class KnownNodes:
    """Cells compile has seen, as inclusive row runs per sheet and column.

    ``target in index`` is true exactly when ``target`` is one of those
    canonical ids. Column case, ``$``, and leading zeros are folded on insert;
    the query must already be the canonical spelling. Sheet names compare
    exactly, including names that contain ``!`` or quotes. An address
    :func:`parse_addr` rejects is kept as the literal ``sheet!addr``.
    """

    def __init__(self) -> None:
        self._open: dict[tuple[str, int], list[int]] = {}
        self._closed: dict[str, dict[int, list[tuple[int, int]]]] = {}
        self._literals: set[str] = set()
        self._rows: dict[str, dict[int, tuple[tuple[int, int], ...]]] = {}
        self._ready = False

    def add(self, sheet: str, addr: str) -> None:
        if self._ready:
            self._reopen()
        try:
            col, row = parse_addr(addr)
        except ValueError:
            self._literals.add(f"{sheet}!{addr}")
            return
        self._touch(sheet, col, row)

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str) or "!" not in item:
            return False
        self._finalize()
        if item in self._literals:
            return True
        return self._in_runs(item)

    def __len__(self) -> int:
        self._finalize()
        total = sum(
            end - start + 1
            for columns in self._rows.values()
            for spans in columns.values()
            for start, end in spans
        )
        for literal in self._literals:
            if not self._in_runs(literal):
                total += 1
        return total

    def __bool__(self) -> bool:
        if self._literals or self._open or self._closed:
            return True
        return any(columns for columns in self._rows.values())

    def _touch(self, sheet: str, col: int, row: int) -> None:
        key = (sheet, col)
        run = self._open.get(key)
        if run is not None and row <= run[1] + 1 and row + 1 >= run[0]:
            if row < run[0]:
                run[0] = row
            if row > run[1]:
                run[1] = row
            return
        if run is not None:
            self._closed.setdefault(sheet, {}).setdefault(col, []).append((run[0], run[1]))
        self._open[key] = [row, row]

    def _finalize(self) -> None:
        if self._ready:
            return
        for (sheet, col), run in self._open.items():
            self._closed.setdefault(sheet, {}).setdefault(col, []).append((run[0], run[1]))
        self._open.clear()
        self._rows = {
            sheet: {col: _merge_spans(items) for col, items in columns.items()}
            for sheet, columns in self._closed.items()
        }
        self._closed.clear()
        self._ready = True

    def _reopen(self) -> None:
        for sheet, columns in self._rows.items():
            for col, spans in columns.items():
                self._closed.setdefault(sheet, {}).setdefault(col, []).extend(spans)
        self._rows = {}
        self._ready = False

    def _in_runs(self, item: str) -> bool:
        sheet, body = item.rsplit("!", 1)
        try:
            col, row = parse_addr(body)
        except ValueError:
            return False
        if format_addr(col, row) != body:
            return False
        return _span_has(self._rows.get(sheet, {}).get(col, ()), row)
