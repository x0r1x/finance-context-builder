from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from finance_context.excel.a1 import col_to_index, format_addr, index_to_col
from finance_context.formulas.models import Edge
from finance_context.settings import DEFAULT_RANGE_EXPAND_CAP, DEFAULT_RANGE_NODE_MIN_CELLS

_RANGE_CAP: ContextVar[int | None] = ContextVar("finance_range_expand_cap", default=None)
# A whole column or row, or a rectangle over this many cells, is one range node.
# The formula gets one edge to the node and nothing else is written for it: its
# cells come from the node's bounds and the sheet's present cells when a reader
# needs them. A copied-down SUMIF(A:A, ...) or a running total SUM($B$2:B{n})
# then costs one edge per formula.
_NODE_MIN: ContextVar[int | None] = ContextVar("finance_range_node_min_cells", default=None)
_EXCEL_MAX_ROW = 1_048_576
_EXCEL_MAX_COL = 16_384

_CELL = re.compile(r"^\$?([A-Za-z]+)\$?(\d+)$", re.I)
_COLS = re.compile(r"^\$?([A-Za-z]+):\$?([A-Za-z]+)$", re.I)
_ROWS = re.compile(r"^\$?(\d+):\$?(\d+)$")


def split_sheet_ref(target: str) -> tuple[str, str]:
    if target.startswith("'"):
        end = target.find("'!")
        if end < 0:
            raise ValueError(target)
        return target[1:end], target[end + 2 :]
    if "!" not in target:
        raise ValueError(target)
    sheet, body = target.split("!", 1)
    return sheet, body


def _rectangle(body: str) -> tuple[int, int, int, int] | None:
    """Inclusive column/row bounds of a range body, or None when it is not a range."""
    cells_range = re.fullmatch(r"\$?([A-Za-z]+)\$?(\d+):\$?([A-Za-z]+)\$?(\d+)", body, re.I)
    if cells_range:
        c1 = col_to_index(cells_range.group(1))
        r1 = int(cells_range.group(2))
        c2 = col_to_index(cells_range.group(3))
        r2 = int(cells_range.group(4))
        if c1 > c2:
            c1, c2 = c2, c1
        if r1 > r2:
            r1, r2 = r2, r1
        return c1, c2, r1, r2

    cols = _COLS.fullmatch(body)
    if cols:
        c1 = col_to_index(cols.group(1))
        c2 = col_to_index(cols.group(2))
        if c1 > c2:
            c1, c2 = c2, c1
        return c1, c2, 1, _EXCEL_MAX_ROW

    rows = _ROWS.fullmatch(body)
    if rows:
        r1, r2 = int(rows.group(1)), int(rows.group(2))
        if r1 > r2:
            r1, r2 = r2, r1
        return 1, _EXCEL_MAX_COL, r1, r2
    return None


@dataclass(frozen=True, slots=True)
class SheetUsage:
    """Cells that exist on each sheet, as row runs per column.

    Sheet names compare case-insensitively. ``canonical`` maps the folded name to
    the spelling stored on the cell, which is the id ``known_nodes`` uses.
    """

    # Column to inclusive row runs, sorted and non-overlapping. One styled
    # rectangle is one run per column, not one integer per cell.
    rows_by_col: dict[str, dict[int, tuple[tuple[int, int], ...]]]
    canonical: dict[str, str]

    def sheet_name(self, sheet: str) -> str:
        return self.canonical.get(sheet.casefold(), sheet)


def bind_range_expand_cap(cap: int) -> Token[int | None]:
    """Use this job's ``RANGE_EXPAND_CAP`` until the token is reset."""
    return _RANGE_CAP.set(cap)


def reset_range_expand_cap(token: Token[int | None]) -> None:
    _RANGE_CAP.reset(token)


def active_range_cap() -> int:
    bound = _RANGE_CAP.get()
    return DEFAULT_RANGE_EXPAND_CAP if bound is None else bound


def bind_range_node_min_cells(cells: int) -> Token[int | None]:
    """Use this job's ``RANGE_NODE_MIN_CELLS`` until the token is reset."""
    return _NODE_MIN.set(cells)


def reset_range_node_min_cells(token: Token[int | None]) -> None:
    _NODE_MIN.reset(token)


def active_range_node_min_cells() -> int:
    bound = _NODE_MIN.get()
    return DEFAULT_RANGE_NODE_MIN_CELLS if bound is None else bound


def present_cell_count(usage: SheetUsage) -> int:
    """Present cells already indexed on ``usage``. No parquet read."""
    return sum(
        end - start + 1
        for columns in usage.rows_by_col.values()
        for spans in columns.values()
        for start, end in spans
    )


def sheet_usage(nodes: Iterable[str]) -> SheetUsage:
    """Index present cells. Whole-column expansion intersects this instead of empty rows."""
    spans: list[tuple[str, int, int, int]] = []
    for node in nodes:
        parsed = _node_coord(node)
        if parsed is None:
            continue
        sheet, col, row = parsed
        spans.append((sheet, col, row, row))
    return sheet_usage_spans(spans)


def sheet_usage_spans(spans: Iterable[tuple[str, int, int, int]]) -> SheetUsage:
    """Index ``(sheet, col, row_start, row_end)`` runs. Adjacent runs merge."""
    by_col: dict[str, dict[int, list[tuple[int, int]]]] = {}
    canonical: dict[str, str] = {}
    for sheet, col, start, end in spans:
        if start > end:
            start, end = end, start
        canonical.setdefault(sheet.casefold(), sheet)
        by_col.setdefault(sheet, {}).setdefault(col, []).append((start, end))
    return SheetUsage(
        rows_by_col={
            sheet: {col: _merge_spans(items) for col, items in cols.items()}
            for sheet, cols in by_col.items()
        },
        canonical=canonical,
    )


def _merge_spans(items: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    if not items:
        return ()
    items.sort()
    merged: list[tuple[int, int]] = [items[0]]
    for start, end in items[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
            continue
        merged.append((start, end))
    return tuple(merged)


def _span_has(spans: Sequence[tuple[int, int]], row: int) -> bool:
    """True when ``row`` is inside a sorted, non-overlapping run.

    The probe is the run's start. A probe of ``(row, 1_048_576)`` misses the
    first row of a run whose end is past Excel's last row: that tuple sorts
    after the probe, ``bisect_right`` returns 0, and the row is reported absent.
    ``parse_addr`` and the OOXML reader both keep those rows.
    """
    if not spans:
        return False
    index = bisect.bisect_right(spans, row, key=lambda span: span[0]) - 1
    if index < 0:
        return False
    start, end = spans[index]
    return start <= row <= end


def _span_overlap(spans: Sequence[tuple[int, int]], lo: int, hi: int) -> int:
    total = 0
    for start, end in spans:
        if end < lo:
            continue
        if start > hi:
            break
        total += min(end, hi) - max(start, lo) + 1
    return total


def _first_span_row(spans: Sequence[tuple[int, int]], lo: int, hi: int) -> int | None:
    for start, end in spans:
        if end < lo:
            continue
        if start > hi:
            return None
        return max(start, lo)
    return None


def _node_coord(node: str) -> tuple[str, int, int] | None:
    try:
        sheet, body = split_sheet_ref(node)
    except ValueError:
        return None
    cell = _CELL.fullmatch(body)
    if cell is None:
        return None
    return sheet, col_to_index(cell.group(1)), int(cell.group(2))


def _area(c1: int, c2: int, r1: int, r2: int) -> int:
    return (c2 - c1 + 1) * (r2 - r1 + 1)


def _whole_axis(c1: int, c2: int, r1: int, r2: int) -> bool:
    """A whole column or whole row. A bounded rectangle keeps the safety cap."""
    whole_column = r1 == 1 and r2 == _EXCEL_MAX_ROW
    whole_row = c1 == 1 and c2 == _EXCEL_MAX_COL
    return whole_column or whole_row


def _present_count(sheet: str, c1: int, c2: int, r1: int, r2: int, usage: SheetUsage) -> int:
    """How many present cells sit in the rectangle. No pair list is built."""
    sheet = usage.sheet_name(sheet)
    columns = usage.rows_by_col.get(sheet, {})
    return sum(_span_overlap(spans, r1, r2) for col, spans in columns.items() if c1 <= col <= c2)


def _first_present(
    sheet: str, c1: int, c2: int, r1: int, r2: int, usage: SheetUsage
) -> tuple[int, int] | None:
    """The first present cell of the rectangle, row then column, as (row, col)."""
    sheet = usage.sheet_name(sheet)
    best: tuple[int, int] | None = None
    columns = usage.rows_by_col.get(sheet, {})
    for col, spans in columns.items():
        if col < c1 or col > c2:
            continue
        row = _first_span_row(spans, r1, r2)
        if row is None:
            continue
        if best is None or row < best[0] or (row == best[0] and col < best[1]):
            best = (row, col)
    return best


def _present_pairs(
    sheet: str, c1: int, c2: int, r1: int, r2: int, usage: SheetUsage
) -> list[tuple[int, int]]:
    """Present cells inside the rectangle, row then column."""
    sheet = usage.sheet_name(sheet)
    found: list[tuple[int, int]] = []
    columns = usage.rows_by_col.get(sheet, {})
    for col, spans in columns.items():
        if col < c1 or col > c2:
            continue
        for start, end in spans:
            lo = start if start > r1 else r1
            hi = end if end < r2 else r2
            if lo > hi:
                continue
            for row in range(lo, hi + 1):
                found.append((row, col))
    found.sort()
    return found


def is_range_node(node: str | None) -> bool:
    """A range node id such as ``Data!A:A`` or ``Data!C1:C2500``. Cell ids have no colon."""
    if not node or "!" not in node:
        return False
    return ":" in node.rsplit("!", 1)[1]


def whole_axis(c1: int, c2: int, r1: int, r2: int) -> bool:
    return _whole_axis(c1, c2, r1, r2)


def _range_node_id(sheet: str, c1: int, c2: int, r1: int, r2: int) -> str:
    if r1 == 1 and r2 == _EXCEL_MAX_ROW:
        return f"{sheet}!{index_to_col(c1)}:{index_to_col(c2)}"
    if c1 == 1 and c2 == _EXCEL_MAX_COL:
        return f"{sheet}!{r1}:{r2}"
    return f"{sheet}!{index_to_col(c1)}{r1}:{index_to_col(c2)}{r2}"


class RangeNode(NamedTuple):
    node_id: str
    sheet: str
    c1: int
    c2: int
    r1: int
    r2: int


def range_node_for(target: str, usage: SheetUsage | None) -> RangeNode | None:
    """The range node a range edge goes through, or None when it expands per formula.

    Only a whole column or row, or a rectangle over ``RANGE_NODE_MIN_CELLS``,
    becomes a node, and only on a sheet ``usage`` knows. A range on a sheet with
    no present cells, or on a missing sheet, still expands per formula so its
    members keep their empty or dangling class.
    """
    if usage is None:
        return None
    try:
        sheet, body = split_sheet_ref(target)
    except ValueError:
        return None
    if sheet.casefold() not in usage.canonical:
        return None
    rect = _rectangle(body)
    if rect is None:
        return None
    c1, c2, r1, r2 = rect
    small = _area(c1, c2, r1, r2) <= active_range_node_min_cells()
    if not _whole_axis(c1, c2, r1, r2) and small:
        return None
    sheet = usage.sheet_name(sheet)
    return RangeNode(_range_node_id(sheet, c1, c2, r1, r2), sheet, c1, c2, r1, r2)


def parse_range_node(node_id: str) -> RangeNode | None:
    """Bounds of a range node id written by ``range_node_for``."""
    if not is_range_node(node_id):
        return None
    sheet, body = node_id.rsplit("!", 1)
    rect = _rectangle(body)
    if rect is None:
        return None
    c1, c2, r1, r2 = rect
    return RangeNode(node_id, sheet, c1, c2, r1, r2)


def present_count(node: RangeNode, usage: SheetUsage) -> int:
    """Present cells inside a node, counted without listing them."""
    return _present_count(node.sheet, node.c1, node.c2, node.r1, node.r2, usage)


def first_present(node: RangeNode, usage: SheetUsage) -> tuple[int, int] | None:
    """First present (row, col) inside a node, row then column; None when it has none."""
    return _first_present(node.sheet, node.c1, node.c2, node.r1, node.r2, usage)


def present_pairs(node: RangeNode, usage: SheetUsage) -> list[tuple[int, int]]:
    """Present (row, col) pairs inside a node, row then column."""
    return _present_pairs(node.sheet, node.c1, node.c2, node.r1, node.r2, usage)


def expansion_count(target: str, cap: int | None = None, usage: SheetUsage | None = None) -> int:
    """How many cells expand_range would emit. A whole column is not materialised."""
    cap = active_range_cap() if cap is None else cap
    try:
        sheet, body = split_sheet_ref(target)
    except ValueError:
        return 1
    if _CELL.fullmatch(body):
        return 1
    rect = _rectangle(body)
    if rect is None:
        return 1
    c1, c2, r1, r2 = rect
    area = _area(c1, c2, r1, r2)
    if area <= 0:
        return 0
    if usage is not None and _whole_axis(c1, c2, r1, r2):
        return _present_count(sheet, c1, c2, r1, r2, usage)
    return area if area <= cap else cap


def expanded_edge_count(edges: list[Edge], usage: SheetUsage | None = None) -> int:
    """Cell targets expand_cell_edges would walk, before de-duplication.

    A range node is one edge: its cells are not written per formula or per node.
    """
    total = 0
    for edge in edges:
        if not edge.target:
            continue
        if edge.kind == "range" and not edge.unresolved and "!" in edge.target:
            if range_node_for(edge.target, usage) is not None:
                total += 1
                continue
            try:
                total += expansion_count(edge.target, usage=usage)
            except ValueError:
                total += 1
            continue
        total += 1
    return total


def expand_range(
    target: str, cap: int | None = None, usage: SheetUsage | None = None
) -> tuple[list[str], bool]:
    """Expand a range.

    With `usage`, a whole column or whole row keeps the present cells and is not
    truncated. A bounded rectangle over the cap still stops at `cap` and reports
    truncated, including the holes inside that prefix. Without `usage`, the cap
    is the same safety bound. Sheet names resolve to the workbook spelling.
    """
    cap = active_range_cap() if cap is None else cap
    sheet, body = split_sheet_ref(target)
    if usage is not None:
        sheet = usage.sheet_name(sheet)
    cell = _CELL.fullmatch(body)
    if cell:
        return [f"{sheet}!{format_addr(col_to_index(cell.group(1)), int(cell.group(2)))}"], False
    rect = _rectangle(body)
    if rect is None:
        return [target], False
    c1, c2, r1, r2 = rect
    if usage is not None and _whole_axis(c1, c2, r1, r2):
        cells = [
            f"{sheet}!{index_to_col(col)}{row}"
            for row, col in _present_pairs(sheet, c1, c2, r1, r2, usage)
        ]
        return cells, False
    return _take(sheet, c1, c2, r1, r2, cap)


def _take(sheet: str, c1: int, c2: int, r1: int, r2: int, cap: int) -> tuple[list[str], bool]:
    out: list[str] = []
    truncated = False
    for row in range(r1, r2 + 1):
        for col in range(c1, c2 + 1):
            if len(out) >= cap:
                return out, True
            out.append(f"{sheet}!{index_to_col(col)}{row}")
    if (c2 - c1 + 1) * (r2 - r1 + 1) > len(out):
        truncated = True
    return out, truncated


class CellEdge(NamedTuple):
    source: str
    target: str
    kind: str
    unresolved: bool
    truncated: bool
    dangling: bool
    dangling_reason: str | None
    status: str | None
    reason: str | None
    evidence: str | None
    range_ref: str | None
    abs_col: bool | None = None
    abs_row: bool | None = None
    abs_col_end: bool | None = None
    abs_row_end: bool | None = None
    named: bool = False
    col_offset: int | None = None
    period_lag: str | None = None


def as_cell_edge(value: CellEdge | Mapping[str, Any]) -> CellEdge:
    """One cell edge. A dict is copied once; a CellEdge is the same object."""
    if isinstance(value, CellEdge):
        return value
    offset = value.get("col_offset")
    return CellEdge(
        str(value.get("source") or ""),
        str(value.get("target") or ""),
        str(value.get("kind") or ""),
        bool(value.get("unresolved")),
        bool(value.get("truncated")),
        bool(value.get("dangling")),
        _opt_text(value.get("dangling_reason")),
        _opt_text(value.get("status")),
        _opt_text(value.get("reason")),
        _opt_text(value.get("evidence")),
        _opt_text(value.get("range_ref")),
        _opt_bool(value.get("abs_col")),
        _opt_bool(value.get("abs_row")),
        _opt_bool(value.get("abs_col_end")),
        _opt_bool(value.get("abs_row_end")),
        bool(value.get("named")),
        None if offset is None else int(offset),
        _opt_text(value.get("period_lag")),
    )


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _opt_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


class MemberStatus(NamedTuple):
    dangling_reason: str | None
    dangling: bool
    status: str | None
    reason: str | None
    evidence: str | None


def _share(pool: dict[str, str] | None, text: str) -> str:
    if pool is None:
        return text
    return pool.setdefault(text, text)


class NodeMembers(Protocol):
    """``set`` of canonical ids, or the compile row-run index. ``in`` / ``len`` / bool."""

    def __contains__(self, item: object) -> bool: ...

    def __len__(self) -> int: ...


def expand_cell_edges(
    edges: list[Edge],
    known_nodes: NodeMembers | None = None,
    *,
    known_sheets: set[str] | None = None,
    presence: dict[str, str] | None = None,
    usage: SheetUsage | None = None,
    pool: dict[str, str] | None = None,
) -> list[CellEdge]:
    """Expand formula edges to cell-to-cell rows with blank vs unresolved status."""
    known = known_nodes or set()
    sheets = known_sheets or set()
    seen_cells = presence or {}
    rows: list[CellEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        for row in _edge_rows(edge, usage):
            target = _share(pool, row.target)
            source = _share(pool, row.source)
            key = (source, target, row.kind)
            if key in seen:
                continue
            seen.add(key)
            classified = _classified(row, target, known, sheets, seen_cells)
            rows.append(
                CellEdge(
                    source,
                    target,
                    row.kind,
                    row.unresolved,
                    row.truncated,
                    classified.dangling,
                    classified.dangling_reason,
                    classified.status,
                    classified.reason,
                    classified.evidence,
                    row.range_ref,
                    row.abs_col,
                    row.abs_row,
                    row.abs_col_end,
                    row.abs_row_end,
                    row.named,
                )
            )
    return rows


_CELL_EDGE_COLUMNS = (
    "source",
    "target",
    "kind",
    "unresolved",
    "truncated",
    "dangling",
    "col_offset",
    "period_lag",
    "dangling_reason",
    "status",
    "reason",
    "evidence",
    "range_ref",
    "abs_col",
    "abs_row",
    "abs_col_end",
    "abs_row_end",
    "named",
)


def expand_cell_edges_column_batches(
    edges: list[Edge],
    known_nodes: NodeMembers | None = None,
    *,
    consume: Any,
    known_sheets: set[str] | None = None,
    presence: dict[str, str] | None = None,
    usage: SheetUsage | None = None,
    pool: dict[str, str] | None = None,
    batch_size: int = 65536,
    after_edge: Any = None,
) -> int:
    """Same rows as expand_cell_edges, flushed as column lists. No CellEdge objects.

    ``consume`` must finish with the batch before it returns. The lists are then cleared.
    ``batch_size`` <= 0 keeps one batch for the whole expansion.
    """
    known = known_nodes or set()
    sheets = known_sheets or set()
    seen_cells = presence or {}
    columns: dict[str, list[Any]] = {name: [] for name in _CELL_EDGE_COLUMNS}
    count = 0
    limit = batch_size if batch_size > 0 else 0

    def flush() -> None:
        nonlocal count
        if not columns["source"]:
            return
        consume(columns)
        count += len(columns["source"])
        for values in columns.values():
            values.clear()

    for edge in edges:
        for row in _edge_rows(edge, usage):
            target = _share(pool, row.target)
            source = _share(pool, row.source)
            classified = _classified(row, target, known, sheets, seen_cells)
            columns["source"].append(source)
            columns["target"].append(target)
            columns["kind"].append(row.kind)
            columns["unresolved"].append(row.unresolved)
            columns["truncated"].append(row.truncated)
            columns["dangling"].append(classified.dangling)
            columns["col_offset"].append(None)
            columns["period_lag"].append(None)
            columns["dangling_reason"].append(classified.dangling_reason)
            columns["status"].append(classified.status)
            columns["reason"].append(classified.reason)
            columns["evidence"].append(classified.evidence)
            columns["range_ref"].append(row.range_ref)
            columns["abs_col"].append(row.abs_col)
            columns["abs_row"].append(row.abs_row)
            columns["abs_col_end"].append(row.abs_col_end)
            columns["abs_row_end"].append(row.abs_row_end)
            columns["named"].append(row.named)
            if limit and len(columns["source"]) >= limit:
                flush()
        # ``truncated`` is set while the edge's rows are walked. The caller can
        # store the edge after that, still one edge at a time.
        if after_edge is not None:
            after_edge(edge)
    flush()
    return count


def classify_member(
    target: str,
    *,
    kind: str,
    known: NodeMembers,
    sheets: set[str],
    unresolved: bool,
    presence: dict[str, str] | None = None,
) -> MemberStatus:
    if not target or unresolved:
        return MemberStatus(None, False, None, None, None)
    if target in known:
        return MemberStatus(None, False, None, None, None)
    try:
        sheet, _body = split_sheet_ref(target)
    except ValueError:
        return MemberStatus(
            "parser_resolution_failure",
            True,
            "unresolved",
            "parser_resolution_failure",
            "bad_address",
        )
    if sheets and sheet not in sheets:
        return MemberStatus(
            "missing_sheet",
            True,
            "unresolved",
            "missing_sheet",
            "sheet_not_in_workbook",
        )
    seen = (presence or {}).get(target)
    if seen == "populated":
        return MemberStatus(
            "parser_resolution_failure",
            True,
            "unresolved",
            "parser_resolution_failure",
            "populated_missing_from_index",
        )
    if not sheets:
        if kind == "range":
            return _blank("empty_range_member", "omitted_by_excel")
        return MemberStatus(
            "missing_cell",
            True,
            "unresolved",
            "parser_resolution_failure",
            "not_in_sparse_index",
        )
    evidence = "styled_blank" if seen == "styled_blank" else "omitted_by_excel"
    klass = "empty_range_member" if kind == "range" else "empty_ref"
    return _blank(klass, evidence)


def _blank(klass: str, evidence: str) -> MemberStatus:
    return MemberStatus(klass, False, "empty", "actual_blank_cell", evidence)


class _EdgeRow(NamedTuple):
    source: str
    target: str
    kind: str
    unresolved: bool
    truncated: bool
    range_ref: str | None
    abs_col: bool | None
    abs_row: bool | None
    abs_col_end: bool | None
    abs_row_end: bool | None
    named: bool
    # Kind classify_member sees, or None for an edge into a range node.
    member_kind: str | None


_UNCLASSIFIED = MemberStatus(None, False, None, None, None)


def _classified(
    row: _EdgeRow,
    target: str,
    known: NodeMembers,
    sheets: set[str],
    presence: dict[str, str],
) -> MemberStatus:
    if row.member_kind is None:
        return _UNCLASSIFIED
    return classify_member(
        target,
        kind=row.member_kind,
        known=known,
        sheets=sheets,
        unresolved=row.unresolved,
        presence=presence,
    )


def _edge_rows(edge: Edge, usage: SheetUsage | None) -> Iterable[_EdgeRow]:
    """Cell-edge rows for one formula edge. A large range is one row to its node."""
    if (
        edge.kind == "range"
        and edge.target
        and not edge.unresolved
        and "!" in edge.target
        and usage is not None
    ):
        node = range_node_for(edge.target, usage)
        if node is not None:
            yield _EdgeRow(
                edge.source,
                node.node_id,
                edge.kind,
                False,
                False,
                edge.target,
                edge.abs_col,
                edge.abs_row,
                edge.abs_col_end,
                edge.abs_row_end,
                edge.named,
                None,
            )
            return
    for target, unresolved, truncated in _expanded_targets(edge, usage):
        yield _EdgeRow(
            edge.source,
            target,
            edge.kind,
            unresolved,
            truncated,
            edge.target,
            edge.abs_col,
            edge.abs_row,
            edge.abs_col_end,
            edge.abs_row_end,
            edge.named,
            edge.kind,
        )


def _expanded_targets(edge: Edge, usage: SheetUsage | None = None) -> list[tuple[str, bool, bool]]:
    if not edge.target:
        return []
    if edge.kind == "range" and not edge.unresolved and "!" in edge.target:
        try:
            cells, trunc = expand_range(edge.target, usage=usage)
        except ValueError:
            return [(edge.target, True, False)]
        if trunc:
            edge.truncated = True
        return [(cell, False, trunc) for cell in cells]
    if edge.kind in {"ref", "cross_sheet"}:
        return [(_canonical_target(edge.target, usage), edge.unresolved, edge.truncated)]
    return [(edge.target, True, edge.truncated)]


def _canonical_target(target: str, usage: SheetUsage | None) -> str:
    if usage is None:
        return target
    try:
        sheet, body = split_sheet_ref(target)
    except ValueError:
        return target
    return f"{usage.sheet_name(sheet)}!{body}"
