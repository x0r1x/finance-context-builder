from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, NamedTuple

from finance_context.excel.a1 import col_to_index, format_addr, index_to_col
from finance_context.formulas.models import Edge
from finance_context.settings import DEFAULT_RANGE_EXPAND_CAP

_RANGE_CAP: ContextVar[int | None] = ContextVar("finance_range_expand_cap", default=None)
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
    """Cells that exist on each sheet, as sorted row lists per column and the reverse.

    Sheet names compare case-insensitively. ``canonical`` maps the folded name to
    the spelling stored on the cell, which is the id ``known_nodes`` uses.
    """

    rows_by_col: dict[str, dict[int, tuple[int, ...]]]
    cols_by_row: dict[str, dict[int, tuple[int, ...]]]
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


def present_cell_count(usage: SheetUsage) -> int:
    """Present cells already indexed on ``usage``. No parquet read."""
    return sum(len(rows) for columns in usage.rows_by_col.values() for rows in columns.values())


def sheet_usage(nodes: Iterable[str]) -> SheetUsage:
    """Index present cells. Whole-column expansion intersects this instead of empty rows."""
    by_col: dict[str, dict[int, set[int]]] = {}
    by_row: dict[str, dict[int, set[int]]] = {}
    canonical: dict[str, str] = {}
    for node in nodes:
        parsed = _node_coord(node)
        if parsed is None:
            continue
        sheet, col, row = parsed
        canonical.setdefault(sheet.casefold(), sheet)
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
        canonical=canonical,
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


def _whole_axis(c1: int, c2: int, r1: int, r2: int) -> bool:
    """A whole column or whole row. A bounded rectangle keeps the safety cap."""
    whole_column = r1 == 1 and r2 == _EXCEL_MAX_ROW
    whole_row = c1 == 1 and c2 == _EXCEL_MAX_COL
    return whole_column or whole_row


def _slice_sorted(values: Sequence[int], lo: int, hi: int) -> Sequence[int]:
    if not values:
        return ()
    start = bisect.bisect_left(values, lo)
    end = bisect.bisect_right(values, hi)
    return values[start:end]


def _count_span(values: Sequence[int], lo: int, hi: int) -> int:
    if not values:
        return 0
    return bisect.bisect_right(values, hi) - bisect.bisect_left(values, lo)


def _present_count(
    sheet: str, c1: int, c2: int, r1: int, r2: int, usage: SheetUsage
) -> int:
    """How many present cells sit in the rectangle. No pair list is built."""
    sheet = usage.sheet_name(sheet)
    width = c2 - c1 + 1
    height = r2 - r1 + 1
    if width <= height:
        columns = usage.rows_by_col.get(sheet, {})
        return sum(_count_span(columns.get(col, ()), r1, r2) for col in range(c1, c2 + 1))
    rows = usage.cols_by_row.get(sheet, {})
    return sum(
        _count_span(cols, c1, c2) for row, cols in rows.items() if r1 <= row <= r2
    )


def _present_pairs(
    sheet: str, c1: int, c2: int, r1: int, r2: int, usage: SheetUsage
) -> list[tuple[int, int]]:
    """Present cells inside the rectangle, row then column."""
    sheet = usage.sheet_name(sheet)
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
    target: str, cap: int | None = None, usage: SheetUsage | None = None
) -> int:
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


ID_POOL: dict[str, str] | None = None  # PROTOTYPE


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
    pool = ID_POOL
    for edge in edges:
        for target, unresolved, truncated in _expanded_targets(edge, usage):
            if pool is not None:
                target = pool.setdefault(target, target)
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


def expand_cell_edges_batched(
    edges: list[Edge],
    known_nodes: set[str] | None = None,
    *,
    known_sheets: set[str] | None = None,
    presence: dict[str, str] | None = None,
    usage: SheetUsage | None = None,
    consume,
    batch_size: int = 65536,
) -> None:
    """PROTOTYPE: same rows as expand_cell_edges, handed off in batches.

    The dedup set stores packed integer ids so it does not keep a 3-tuple per edge.
    ``consume`` must finish with the batch before it returns; the batch is then cleared.
    """
    known = known_nodes or set()
    sheets = known_sheets or set()
    seen_cells = presence or {}
    seen: set[int] = set()
    ids: dict[str, int] = {}
    id_box: list[int] = []
    kinds: dict[str, int] = {}
    pool = ID_POOL
    batch: list[CellEdge] = []

    def nid(text: str) -> int:
        slot = ids.get(text)
        if slot is None:
            slot = len(id_box)
            ids[text] = slot
            id_box.append(slot)
        return id_box[slot]

    def kid(kind: str) -> int:
        slot = kinds.get(kind)
        if slot is None:
            slot = len(kinds) + 1
            kinds[kind] = slot
        return slot

    for edge in edges:
        for target, unresolved, truncated in _expanded_targets(edge, usage):
            if pool is not None:
                target = pool.setdefault(target, target)
            classified = classify_member(
                target,
                kind=edge.kind,
                known=known,
                sheets=sheets,
                unresolved=unresolved,
                presence=seen_cells,
            )
            key = (nid(edge.source) << 40) | (nid(target) << 8) | kid(edge.kind)
            if key in seen:
                continue
            seen.add(key)
            batch.append(
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
            if len(batch) >= batch_size:
                consume(batch)
                batch.clear()
    if batch:
        consume(batch)
        batch.clear()
    seen.clear()
    ids.clear()
    id_box.clear()


_CE_COLUMNS = (
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
    known_nodes: set[str] | None = None,
    *,
    known_sheets: set[str] | None = None,
    presence: dict[str, str] | None = None,
    usage: SheetUsage | None = None,
    consume,
    batch_size: int = 65536,
) -> int:
    """PROTOTYPE: same rows as expand_cell_edges, flushed as column lists. No CellEdge."""
    known = known_nodes or set()
    sheets = known_sheets or set()
    seen_cells = presence or {}
    seen: set[int] = set()
    ids: dict[str, int] = {}
    id_box: list[int] = []
    kinds: dict[str, int] = {}
    pool = ID_POOL
    cols: dict[str, list] = {name: [] for name in _CE_COLUMNS}
    count = 0

    def nid(text: str) -> int:
        slot = ids.get(text)
        if slot is None:
            slot = len(id_box)
            ids[text] = slot
            id_box.append(slot)
        return id_box[slot]

    def kid(kind: str) -> int:
        slot = kinds.get(kind)
        if slot is None:
            slot = len(kinds) + 1
            kinds[kind] = slot
        return slot

    def flush() -> None:
        nonlocal count
        if not cols["source"]:
            return
        consume(cols)
        count += len(cols["source"])
        for values in cols.values():
            values.clear()

    for edge in edges:
        for target, unresolved, truncated in _expanded_targets(edge, usage):
            if pool is not None:
                target = pool.setdefault(target, target)
            classified = classify_member(
                target,
                kind=edge.kind,
                known=known,
                sheets=sheets,
                unresolved=unresolved,
                presence=seen_cells,
            )
            key = (nid(edge.source) << 40) | (nid(target) << 8) | kid(edge.kind)
            if key in seen:
                continue
            seen.add(key)
            cols["source"].append(edge.source)
            cols["target"].append(target)
            cols["kind"].append(edge.kind)
            cols["unresolved"].append(unresolved)
            cols["truncated"].append(truncated)
            cols["dangling"].append(classified.dangling)
            cols["col_offset"].append(None)
            cols["period_lag"].append(None)
            cols["dangling_reason"].append(classified.dangling_reason)
            cols["status"].append(classified.status)
            cols["reason"].append(classified.reason)
            cols["evidence"].append(classified.evidence)
            cols["range_ref"].append(edge.target)
            cols["abs_col"].append(edge.abs_col)
            cols["abs_row"].append(edge.abs_row)
            cols["abs_col_end"].append(edge.abs_col_end)
            cols["abs_row_end"].append(edge.abs_row_end)
            cols["named"].append(edge.named)
            if len(cols["source"]) >= batch_size:
                flush()
    flush()
    seen.clear()
    ids.clear()
    id_box.clear()
    return count


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
