from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, Field

from finance_context.layout.formulas_shape import RowPattern, _pattern_for_row
from finance_context.layout.models import (
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
    is_value_block,
    published_row_key,
    shared_horizontal_rows,
)
from finance_context.layout.resolve import horizontal_columns, period_headers


class RowRelation(BaseModel):
    kind: str
    source_row_key: str
    target_row_key: str | None = None
    member_row_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None


class _RelationBook(Protocol):
    row_index: dict[tuple[str, int], tuple[object, object]]
    sheet_rows: dict[str, list[tuple[int, str]]]

    def row_key(self, sheet: str, row: int, block_id: str) -> str: ...


class _CellIndex:
    def __init__(self, layout: Layout) -> None:
        self.row_index: dict[tuple[str, int], tuple[Block, LayoutRow]] = {}
        self.owners: dict[tuple[str, int], list[Block]] = {}
        self.sheet_of: dict[str, SheetLayout] = {}
        self.shared: dict[str, set[int]] = {}
        self.label_of: dict[tuple[str, str], int] = {}
        for sheet in layout.sheets:
            shared_rows = shared_horizontal_rows(sheet.blocks)
            self.shared[sheet.name] = shared_rows
            self.sheet_of[sheet.name] = sheet
            for block in sheet.blocks:
                self.label_of[(sheet.name, block.block_id)] = block.label_col
                for row in block.rows:
                    # A transposed line shares the label row with its siblings.
                    # Indexing it here would hide them, and a horizontal row.
                    if row.value_col:
                        continue
                    owners = self.owners.setdefault((sheet.name, row.row), [])
                    if not any(existing is block for existing in owners):
                        owners.append(block)
                    # A row in two blocks has no single target.
                    if row.row in shared_rows:
                        continue
                    self.row_index[(sheet.name, row.row)] = (block, row)
        buckets: dict[str, list[tuple[int, str]]] = {}
        for (name, row_n), (block, _layout_row) in self.row_index.items():
            block_id = getattr(block, "block_id", None)
            if block_id:
                buckets.setdefault(name, []).append((row_n, block_id))
        for rows in buckets.values():
            rows.sort()
        self.sheet_rows = buckets

    def row_key(self, sheet: str, row: int, block_id: str) -> str:
        label_col = None
        if row in self.shared.get(sheet, ()):
            label_col = self.label_of.get((sheet, block_id))
        return published_row_key(sheet, row, block_id, label_col=label_col)


class _RowCells:
    """Cells of one sheet row. ``_pattern_for_row`` reads ``.cells`` and nothing else."""

    def __init__(self, cells: dict[tuple[str, int, int], dict]) -> None:
        self.cells = cells


def relation_period_cols(sheet: SheetLayout, block: Block) -> list[int]:
    """Columns ``formula_relations`` reads on this block."""
    headers = period_headers(sheet, block)
    if is_value_block(getattr(block, "kind", "timeline")):
        return [header.col for header in headers if header.role in {"value", "unknown"}]
    return [header.col for header in headers]


def formula_relations(layout: Layout, cells: Iterable[dict]) -> list[RowRelation]:
    """Formula-shape relations for alias, aggregate, difference, and roll-forward.

    Cells of one sheet row are contiguous, which is how ``ir/cells.parquet`` is
    stored and how ``load_relation_cells`` yields them. The row is reduced to a
    pattern and its AST strings are dropped before the next row.

    A transposed line is a column, so it gets none of these relations. The graph
    still links each of its formula cells once ``row_key`` and ``period_id`` are set.
    """
    book = _CellIndex(layout)
    periods: dict[tuple[str, int], list[list[int]]] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            cols = relation_period_cols(sheet, block)
            for layout_row in block.rows:
                # The scanner reads one sheet row across period columns. A
                # transposed line is a column, so that read would be the label.
                if layout_row.value_col:
                    continue
                owned = cols
                if layout_row.excluded_cols:
                    owned = [col for col in cols if col not in layout_row.excluded_cols]
                periods.setdefault((sheet.name, layout_row.row), []).append(owned)
    patterns: dict[tuple[str, int, tuple[int, ...]], RowPattern] = {}
    current: tuple[str, int] | None = None
    bucket: list[dict] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        colmap = {(current[0], current[1], int(cell["col"])): cell for cell in bucket}
        view = _RowCells(colmap)
        for cols in periods.get(current, ()):
            patterns[(current[0], current[1], tuple(cols))] = _pattern_for_row(
                view, current[0], current[1], cols
            )
        bucket.clear()
        current = None

    for cell in cells:
        key = (str(cell["sheet"]), int(cell["row"]))
        if key != current:
            flush()
            current = key
        bucket.append(cell)
    flush()

    relations: list[RowRelation] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            cols = relation_period_cols(sheet, block)
            for layout_row in block.rows:
                if layout_row.value_col:
                    continue
                keyed = tuple(col for col in cols if col not in layout_row.excluded_cols)
                pattern = patterns.get((sheet.name, layout_row.row, keyed))
                if pattern is None:
                    continue
                relation = _relation_from_pattern(book, sheet.name, block, layout_row, pattern)
                if relation is not None:
                    relations.append(relation)
    return relations


def _member_keys_in_span(
    book: _RelationBook, sheet: str, lo: int, hi: int, block: Block | None = None
) -> list[str]:
    """Rows in the span. The source block keeps its own rows when they are shared."""
    if hi < lo:
        return []
    found: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()

    def add(row_n: int, block_id: str) -> None:
        if row_n < lo or row_n > hi:
            return
        item = (row_n, block_id)
        if item in seen:
            return
        seen.add(item)
        found.append(item)

    if block is not None:
        for layout_row in block.rows:
            if layout_row.value_col:
                continue
            add(int(layout_row.row), block.block_id)
    rows = book.sheet_rows.get(sheet)
    if rows:
        start = bisect_left(rows, (lo,))
        stop = bisect_left(rows, (hi + 1,))
        for row_n, block_id in rows[start:stop]:
            if block is not None and block_id == block.block_id:
                continue
            add(row_n, block_id)
    found.sort()
    return [book.row_key(sheet, row_n, block_id) for row_n, block_id in found]


def _owner_id(
    book: _CellIndex,
    sheet: str,
    row: int,
    col: int | None,
    source: Block | None,
) -> str | None:
    """Block that owns this cell. A shared row is resolved by column."""
    owners = book.owners.get((sheet, int(row)), [])
    layout_sheet = book.sheet_of.get(sheet)
    if col is not None and layout_sheet is not None and owners:
        claimed = [
            block
            for block in owners
            if int(col) in horizontal_columns(layout_sheet, block, int(row))
        ]
        if len(claimed) == 1:
            return claimed[0].block_id
        if len(claimed) > 1:
            return None
    if len(owners) == 1:
        return owners[0].block_id
    if source is not None and any(block.block_id == source.block_id for block in owners):
        return source.block_id
    return None


def _relation_from_pattern(
    book: _CellIndex,
    sheet: str,
    block: Block,
    layout_row: LayoutRow,
    pattern: RowPattern,
) -> RowRelation | None:
    source = book.row_key(sheet, layout_row.row, block.block_id)
    if pattern.kind == "alias" and pattern.alias_row is not None:
        target_sheet = pattern.alias_sheet or sheet
        target_block = _owner_id(
            book,
            target_sheet,
            pattern.alias_row,
            pattern.alias_col,
            block if target_sheet == sheet else None,
        )
        if target_block is None:
            return None
        return RowRelation(
            kind="alias",
            source_row_key=source,
            target_row_key=book.row_key(target_sheet, pattern.alias_row, target_block),
            evidence=pattern.template,
        )
    if pattern.kind == "aggregate" and pattern.aggregate_span is not None:
        lo, hi = pattern.aggregate_span
        members = _member_keys_in_span(book, sheet, lo, hi, block)
        return RowRelation(
            kind="aggregate",
            source_row_key=source,
            member_row_keys=members,
            evidence=pattern.template,
        )
    if pattern.kind == "diff" and pattern.diff_rows:
        members = []
        for row_n in pattern.diff_rows:
            block_id = _owner_id(book, sheet, row_n, None, block)
            if block_id:
                members.append(book.row_key(sheet, row_n, block_id))
        return RowRelation(
            kind="difference",
            source_row_key=source,
            member_row_keys=members,
            evidence=pattern.template,
        )
    if pattern.kind == "roll" and pattern.roll_from_row is not None:
        block_id = _owner_id(book, sheet, pattern.roll_from_row, None, block)
        if block_id is None:
            return None
        return RowRelation(
            kind="roll_forward",
            source_row_key=source,
            target_row_key=book.row_key(sheet, pattern.roll_from_row, block_id),
            evidence=pattern.template,
        )
    return None
