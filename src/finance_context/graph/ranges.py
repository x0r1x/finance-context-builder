"""Range nodes at read time: their cells, the nodes covering a cell, and cycle hops.

A range node (``Data!A:A``, ``Data!B2:B5000``) is stored as one edge per formula.
Its cells are not written anywhere; they come from the node's bounds and the
cells of the sheet. These helpers answer the questions readers ask of a node
without listing its cells per node, so memory stays linear in formulas plus cells.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Iterator

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.csr import (
    RangeNode,
    SheetUsage,
    first_present,
    parse_range_node,
    present_pairs,
    sheet_usage,
    whole_axis,
)
from finance_context.graph.refs import parse_node_id

# A node wider than this many columns is indexed once per sheet, not per column.
_NARROW_COLUMNS = 64
# Ids of RangeHops segments: "#seg|sheet|col|lo|hi". Such an id never parses as
# a cell (it ends in "|lo|hi", not an address), so ``is_cell_id`` needs no prefix
# test, and a sheet named "#seg|..." keeps its cells.
SEGMENT_PREFIX = "#seg|"


def is_whole_column(node: RangeNode) -> bool:
    return node.r1 == 1 and node.r2 == 1_048_576


def is_whole_row(node: RangeNode) -> bool:
    return node.c1 == 1 and node.c2 == 16_384


class CellGrid:
    """Cells of a book by sheet and column, for range members in row-major order."""

    def __init__(self, usage: SheetUsage) -> None:
        self.usage = usage
        self._first: dict[str, tuple[int, int] | None] = {}

    @classmethod
    def from_cells(cls, cell_ids: Iterable[str]) -> CellGrid:
        return cls(sheet_usage(cell_ids))

    def members(self, node: RangeNode) -> list[str]:
        return [
            f"{node.sheet}!{index_to_col(col)}{row}" for row, col in present_pairs(node, self.usage)
        ]

    def first(self, node: RangeNode) -> tuple[int, int] | None:
        """First cell of the node as (col, row): its corner, or for a whole axis
        the first present cell. This is the member a per-formula expansion lists first."""
        if not whole_axis(node.c1, node.c2, node.r1, node.r2):
            return node.c1, node.r1
        if node.node_id not in self._first:
            found = first_present(node, self.usage)
            self._first[node.node_id] = None if found is None else (found[1], found[0])
        return self._first[node.node_id]

    def member_columns(self, node: RangeNode) -> list[int]:
        """Columns that hold members. A bounded rectangle counts its blank cells."""
        if not whole_axis(node.c1, node.c2, node.r1, node.r2):
            return list(range(node.c1, node.c2 + 1))
        if is_whole_column(node):
            columns = self.usage.rows_by_col.get(node.sheet, {})
            return sorted(col for col in columns if node.c1 <= col <= node.c2)
        rows = self.usage.cols_by_row.get(node.sheet, {})
        found: set[int] = set()
        for row, cols in rows.items():
            if node.r1 <= row <= node.r2:
                found.update(cols)
        return sorted(found)


class _Intervals:
    """Row intervals with payloads. ``covering(row)`` costs log n plus the matches."""

    def __init__(self, items: list[tuple[int, int, object]]) -> None:
        items.sort(key=lambda item: item[0])
        self.starts = [item[0] for item in items]
        self.ends = [item[1] for item in items]
        self.payloads = [item[2] for item in items]
        size = 1
        while size < max(1, len(items)):
            size *= 2
        self.size = size
        tree = [-1] * (2 * size)
        for index, end in enumerate(self.ends):
            tree[size + index] = end
        for index in range(size - 1, 0, -1):
            tree[index] = max(tree[2 * index], tree[2 * index + 1])
        self.tree = tree

    def covering(self, row: int) -> Iterator[object]:
        limit = bisect.bisect_right(self.starts, row)
        if limit == 0:
            return
        stack = [(1, 0, self.size - 1)]
        while stack:
            slot, lo, hi = stack.pop()
            if lo >= limit or self.tree[slot] < row:
                continue
            if lo == hi:
                yield self.payloads[lo]
                continue
            mid = (lo + hi) // 2
            stack.append((2 * slot + 1, mid + 1, hi))
            stack.append((2 * slot, lo, mid))


class CoverIndex:
    """Which range nodes contain a cell."""

    def __init__(self, nodes: Iterable[RangeNode]) -> None:
        narrow: dict[tuple[str, int], list[tuple[int, int, object]]] = {}
        wide: dict[str, list[tuple[int, int, object]]] = {}
        for node in nodes:
            if node.c2 - node.c1 + 1 <= _NARROW_COLUMNS:
                for col in range(node.c1, node.c2 + 1):
                    narrow.setdefault((node.sheet, col), []).append((node.r1, node.r2, node))
            else:
                wide.setdefault(node.sheet, []).append((node.r1, node.r2, node))
        self._narrow = {key: _Intervals(items) for key, items in narrow.items()}
        self._wide = {key: _Intervals(items) for key, items in wide.items()}

    def __bool__(self) -> bool:
        return bool(self._narrow or self._wide)

    def covering(self, sheet: str, col: int, row: int) -> list[RangeNode]:
        found: list[RangeNode] = []
        column = self._narrow.get((sheet, col))
        if column is not None:
            found.extend(column.covering(row))  # type: ignore[arg-type]
        spread = self._wide.get(sheet)
        if spread is not None:
            for node in spread.covering(row):
                if node.c1 <= col <= node.c2:  # type: ignore[attr-defined]
                    found.append(node)  # type: ignore[arg-type]
        return found


def range_nodes(targets: Iterable[str]) -> dict[str, RangeNode]:
    """Parsed range nodes among edge targets, by id."""
    nodes: dict[str, RangeNode] = {}
    for target in targets:
        if target in nodes:
            continue
        node = parse_range_node(target)
        if node is not None:
            nodes[target] = node
    return nodes


def is_cell_id(name: str) -> bool:
    """A cell id, not a range node or a RangeHops segment. ``#seg|Inputs!A1`` is a cell."""
    return parse_node_id(name) is not None and ":" not in name


class RangeHops:
    """Edges that let a strongly-connected-components pass see through range nodes.

    Each node points at segments of a balanced tree over the formula cells of a
    column, and each segment at its halves, down to the cells. Only formula
    cells can sit on a cycle, so inputs are left out. A node costs about
    log(cells) edges per column it spans instead of one edge per cell.
    """

    def __init__(self, formula_cells: Iterable[str]) -> None:
        rows: dict[tuple[str, int], list[int]] = {}
        for cell in formula_cells:
            parsed = parse_node_id(cell)
            if parsed is None or ":" in cell:
                continue
            sheet, col, row = parsed
            rows.setdefault((sheet, col), []).append(row)
        self.rows = {key: sorted(set(values)) for key, values in rows.items()}
        self.columns: dict[str, list[int]] = {}
        for sheet, col in self.rows:
            self.columns.setdefault(sheet, []).append(col)
        for cols in self.columns.values():
            cols.sort()
        self._made: set[tuple[str, int, int, int]] = set()

    def _cols(self, node: RangeNode) -> list[int]:
        cols = self.columns.get(node.sheet, [])
        lo = bisect.bisect_left(cols, node.c1)
        hi = bisect.bisect_right(cols, node.c2)
        return cols[lo:hi]

    def _span(self, node: RangeNode, col: int) -> tuple[int, int] | None:
        rows = self.rows[(node.sheet, col)]
        lo = bisect.bisect_left(rows, node.r1)
        hi = bisect.bisect_right(rows, node.r2) - 1
        return (lo, hi) if lo <= hi else None

    def members(self, node: RangeNode) -> Iterator[str]:
        """Formula cells inside the node."""
        for col in self._cols(node):
            span = self._span(node, col)
            if span is None:
                continue
            rows = self.rows[(node.sheet, col)]
            letters = index_to_col(col)
            for index in range(span[0], span[1] + 1):
                yield f"{node.sheet}!{letters}{rows[index]}"

    def edges(self, node: RangeNode) -> Iterator[tuple[str, str]]:
        for col in self._cols(node):
            span = self._span(node, col)
            if span is None:
                continue
            size = len(self.rows[(node.sheet, col)])
            for lo, hi in _cover(0, size - 1, span[0], span[1]):
                yield node.node_id, self._name(node.sheet, col, lo, hi)
                yield from self._tree(node.sheet, col, lo, hi)

    def _name(self, sheet: str, col: int, lo: int, hi: int) -> str:
        if lo == hi:
            return f"{sheet}!{index_to_col(col)}{self.rows[(sheet, col)][lo]}"
        return f"{SEGMENT_PREFIX}{sheet}|{col}|{lo}|{hi}"

    def _tree(self, sheet: str, col: int, lo: int, hi: int) -> Iterator[tuple[str, str]]:
        stack = [(lo, hi)]
        while stack:
            a, b = stack.pop()
            if a == b or (sheet, col, a, b) in self._made:
                continue
            self._made.add((sheet, col, a, b))
            mid = (a + b) // 2
            parent = self._name(sheet, col, a, b)
            yield parent, self._name(sheet, col, a, mid)
            yield parent, self._name(sheet, col, mid + 1, b)
            stack.append((a, mid))
            stack.append((mid + 1, b))


def _cover(lo: int, hi: int, want_lo: int, want_hi: int) -> Iterator[tuple[int, int]]:
    """Segments of the balanced tree over [lo, hi] that exactly cover [want_lo, want_hi]."""
    stack = [(lo, hi)]
    while stack:
        a, b = stack.pop()
        if b < want_lo or a > want_hi:
            continue
        if want_lo <= a and b <= want_hi:
            yield a, b
            continue
        mid = (a + b) // 2
        stack.append((mid + 1, b))
        stack.append((a, mid))


def hop_flags(
    label_of: Callable[[str], int | None],
    range_edges: Iterable[tuple[str, RangeNode]],
    hops: RangeHops,
    period_chain: Callable[[str, str], bool],
) -> Iterator[tuple[int, bool, bool]]:
    """(component, period-chain bit, self loop) for each formula -> member edge
    inside one component, the same edges a per-formula expansion would have."""
    for source, node in range_edges:
        label = label_of(source)
        if label is None or label_of(node.node_id) != label:
            continue
        for member in hops.members(node):
            if label_of(member) != label:
                continue
            yield label, period_chain(source, member), member == source
