from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, Field

from finance_context.layout.formulas_shape import RowPattern, _pattern_for_row
from finance_context.layout.models import Block, Layout, LayoutRow, SheetLayout, is_value_block
from finance_context.layout.resolve import period_headers


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
        for sheet in layout.sheets:
            for block in sheet.blocks:
                for row in block.rows:
                    # A transposed line shares the label row with its siblings.
                    # Indexing it here would hide them, and a horizontal row.
                    if row.value_col:
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
        return f"{sheet}|{row}|{block_id}"


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
                periods.setdefault((sheet.name, layout_row.row), []).append(cols)
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
            keyed = tuple(relation_period_cols(sheet, block))
            for layout_row in block.rows:
                if layout_row.value_col:
                    continue
                pattern = patterns.get((sheet.name, layout_row.row, keyed))
                if pattern is None:
                    continue
                relation = _relation_from_pattern(book, sheet.name, block, layout_row, pattern)
                if relation is not None:
                    relations.append(relation)
    return relations


def _member_keys_in_span(book: _RelationBook, sheet: str, lo: int, hi: int) -> list[str]:
    rows = book.sheet_rows.get(sheet)
    if not rows:
        return []
    start = bisect_left(rows, (lo,))
    stop = bisect_left(rows, (hi + 1,))
    return [book.row_key(sheet, row_n, block_id) for row_n, block_id in rows[start:stop]]


def _block_id(book: _RelationBook, sheet: str, row: int) -> str | None:
    found = book.row_index.get((sheet, row))
    if found is None:
        return None
    block = found[0]
    return getattr(block, "block_id", None)


def _relation_from_pattern(
    book: _RelationBook,
    sheet: str,
    block: Block,
    layout_row: LayoutRow,
    pattern: RowPattern,
) -> RowRelation | None:
    source = book.row_key(sheet, layout_row.row, block.block_id)
    if pattern.kind == "alias" and pattern.alias_row is not None:
        target_block = _block_id(book, pattern.alias_sheet or sheet, pattern.alias_row)
        if target_block is None:
            return None
        return RowRelation(
            kind="alias",
            source_row_key=source,
            target_row_key=book.row_key(
                pattern.alias_sheet or sheet, pattern.alias_row, target_block
            ),
            evidence=pattern.template,
        )
    if pattern.kind == "aggregate" and pattern.aggregate_span is not None:
        lo, hi = pattern.aggregate_span
        members = _member_keys_in_span(book, sheet, lo, hi)
        return RowRelation(
            kind="aggregate",
            source_row_key=source,
            member_row_keys=members,
            evidence=pattern.template,
        )
    if pattern.kind == "diff" and pattern.diff_rows:
        members = []
        for row_n in pattern.diff_rows:
            block_id = _block_id(book, sheet, row_n)
            if block_id:
                members.append(book.row_key(sheet, row_n, block_id))
        return RowRelation(
            kind="difference",
            source_row_key=source,
            member_row_keys=members,
            evidence=pattern.template,
        )
    if pattern.kind == "roll" and pattern.roll_from_row is not None:
        block_id = _block_id(book, sheet, pattern.roll_from_row)
        if block_id is None:
            return None
        return RowRelation(
            kind="roll_forward",
            source_row_key=source,
            target_row_key=book.row_key(sheet, pattern.roll_from_row, block_id),
            evidence=pattern.template,
        )
    return None
