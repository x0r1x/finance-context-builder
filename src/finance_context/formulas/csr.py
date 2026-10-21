from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from finance_context.excel.a1 import col_to_index, format_addr, index_to_col
from finance_context.formulas.models import Edge

RANGE_EXPAND_CAP = 2000
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
    cells_range = re.fullmatch(
        r"\$?([A-Za-z]+)\$?(\d+):\$?([A-Za-z]+)\$?(\d+)", body, re.I
    )
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
    """Cells that exist on each sheet, as sorted row lists per column and the reverse."""

    rows_by_col: dict[str, dict[int, tuple[int, ...]]]
    cols_by_row: dict[str, dict[int, tuple[int, ...]]]


def sheet_usage(nodes: Iterable[str]) -> SheetUsage:
    """Index present cells. Whole-column expansion intersects this instead of empty rows."""
    by_col: dict[str, dict[int, set[int]]] = {}
    by_row: dict[str, dict[int, set[int]]] = {}
    for node in nodes:
        parsed = _node_coord(node)
        if parsed is None:
            continue
        sheet, col, row = parsed
        by_col.setdefault(sheet, {}).setdefault(col, set()).add(row)
        by_row.setdefault(sheet, {}).setdefault(row, set()).add(col)
    return SheetUsage(
        rows_by_col={
            sheet: {col: tuple(sorted(rows)) for col, rows in cols.items()}
            for sheet, cols in by_col.items()
        },
        cols_by_row={
            sheet: {row: tuple(sorted(cols)) for row, cols in rows.items()}
            for sheet, rows in by_row.items()
        },
    )


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


def _clips(c1: int, c2: int, r1: int, r2: int, cap: int) -> bool:
    """Whole column/row, or a rectangle bigger than the safety cap."""
    whole_column = r1 == 1 and r2 == _EXCEL_MAX_ROW
    whole_row = c1 == 1 and c2 == _EXCEL_MAX_COL
    return whole_column or whole_row or _area(c1, c2, r1, r2) > cap


def _slice_sorted(values: Sequence[int], lo: int, hi: int) -> Sequence[int]:
    if not values:
        return ()
    start = bisect.bisect_left(values, lo)
    end = bisect.bisect_right(values, hi)
    return values[start:end]


def _present_pairs(
    sheet: str, c1: int, c2: int, r1: int, r2: int, usage: SheetUsage
) -> list[tuple[int, int]]:
    """Present cells inside the rectangle, row then column."""
    found: list[tuple[int, int]] = []
    width = c2 - c1 + 1
    height = r2 - r1 + 1
    if width <= height:
        columns = usage.rows_by_col.get(sheet, {})
        for col in range(c1, c2 + 1):
            for row in _slice_sorted(columns.get(col, ()), r1, r2):
                found.append((row, col))
    else:
        rows = usage.cols_by_row.get(sheet, {})
        chosen = sorted(row for row in rows if r1 <= row <= r2)
        for row in chosen:
            for col in _slice_sorted(rows[row], c1, c2):
                found.append((row, col))
    found.sort()
    return found


def expansion_count(
    target: str, cap: int = RANGE_EXPAND_CAP, usage: SheetUsage | None = None
) -> int:
    """How many cells expand_range would emit. A whole column is not materialised."""
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
    if usage is not None and _clips(c1, c2, r1, r2, cap):
        return len(_present_pairs(sheet, c1, c2, r1, r2, usage))
    return area if area <= cap else cap


def expanded_edge_count(edges: list[Edge], usage: SheetUsage | None = None) -> int:
    """Cell targets expand_cell_edges would walk, before de-duplication."""
    total = 0
    for edge in edges:
        if not edge.target:
            continue
        if edge.kind == "range" and not edge.unresolved and "!" in edge.target:
            try:
                total += expansion_count(edge.target, usage=usage)
            except ValueError:
                total += 1
            continue
        total += 1
    return total


def expand_range(
    target: str, cap: int = RANGE_EXPAND_CAP, usage: SheetUsage | None = None
) -> tuple[list[str], bool]:
    """Expand a range.

    With `usage`, a whole column, whole row, or rectangle over the cap keeps only
    cells that are present. Every present cell is emitted, so the range is not
    truncated. Without `usage`, the cap is still the safety bound.
    """
    sheet, body = split_sheet_ref(target)
    cell = _CELL.fullmatch(body)
    if cell:
        return [f"{sheet}!{format_addr(col_to_index(cell.group(1)), int(cell.group(2)))}"], False
    rect = _rectangle(body)
    if rect is None:
        return [target], False
    c1, c2, r1, r2 = rect
    if usage is not None and _clips(c1, c2, r1, r2, cap):
        cells = [
            f"{sheet}!{index_to_col(col)}{row}"
            for row, col in _present_pairs(sheet, c1, c2, r1, r2, usage)
        ]
        return cells, False
    return _take(sheet, c1, c2, r1, r2, cap)


def _take(
    sheet: str, c1: int, c2: int, r1: int, r2: int, cap: int
) -> tuple[list[str], bool]:
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


class MemberStatus(NamedTuple):
    dangling_reason: str | None
    dangling: bool
    status: str | None
    reason: str | None
    evidence: str | None


def expand_cell_edges(
    edges: list[Edge],
    known_nodes: set[str] | None = None,
    *,
    known_sheets: set[str] | None = None,
    presence: dict[str, str] | None = None,
    usage: SheetUsage | None = None,
) -> list[CellEdge]:
    """Expand formula edges to cell-to-cell rows with blank vs unresolved status."""
    known = known_nodes or set()
    sheets = known_sheets or set()
    seen_cells = presence or {}
    rows: list[CellEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        for target, unresolved, truncated in _expanded_targets(edge, usage):
            classified = classify_member(
                target,
                kind=edge.kind,
                known=known,
                sheets=sheets,
                unresolved=unresolved,
                presence=seen_cells,
            )
            key = (edge.source, target, edge.kind)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                CellEdge(
                    edge.source,
                    target,
                    edge.kind,
                    unresolved,
                    truncated,
                    classified.dangling,
                    classified.dangling_reason,
                    classified.status,
                    classified.reason,
                    classified.evidence,
                    edge.target,
                    edge.abs_col,
                    edge.abs_row,
                    edge.abs_col_end,
                    edge.abs_row_end,
                    edge.named,
                )
            )
    return rows


def classify_member(
    target: str,
    *,
    kind: str,
    known: set[str],
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


def _expanded_targets(
    edge: Edge, usage: SheetUsage | None = None
) -> list[tuple[str, bool, bool]]:
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
        return [(edge.target, edge.unresolved, edge.truncated)]
    return [(edge.target, True, edge.truncated)]
