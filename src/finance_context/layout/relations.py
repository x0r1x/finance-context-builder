from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from finance_context.layout.formulas_shape import RowPattern, _pattern_for_row
from finance_context.layout.models import Block, Layout, LayoutRow
from finance_context.layout.resolve import period_headers


class RowRelation(BaseModel):
    kind: str
    source_row_key: str
    target_row_key: str | None = None
    member_row_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None


class _RelationBook(Protocol):
    row_index: dict[tuple[str, int], tuple[object, object]]

    def row_key(self, sheet: str, row: int, block_id: str) -> str: ...


class _CellIndex:
    def __init__(self, layout: Layout, cells: list[dict]) -> None:
        self.cells = {
            (str(cell["sheet"]), int(cell["row"]), int(cell["col"])): cell for cell in cells
        }
        self.row_index: dict[tuple[str, int], tuple[Block, LayoutRow]] = {}
        for sheet in layout.sheets:
            for block in sheet.blocks:
                for row in block.rows:
                    self.row_index[(sheet.name, row.row)] = (block, row)

    def row_key(self, sheet: str, row: int, block_id: str) -> str:
        return f"{sheet}|{row}|{block_id}"


def formula_relations(layout: Layout, cells: list[dict]) -> list[RowRelation]:
    """Formula-shape relations for alias, aggregate, difference, and roll-forward."""
    book = _CellIndex(layout, cells)
    relations: list[RowRelation] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            headers = period_headers(sheet, block)
            period_cols = [header.col for header in headers]
            if getattr(block, "kind", "timeline") == "params":
                period_cols = [
                    header.col
                    for header in headers
                    if header.role in {"value", "scenario", "unknown"}
                ]
            for layout_row in block.rows:
                pattern = _pattern_for_row(book, sheet.name, layout_row.row, period_cols)
                relation = _relation_from_pattern(book, sheet.name, block, layout_row, pattern)
                if relation is not None:
                    relations.append(relation)
    return relations


def _member_keys_in_span(book: _RelationBook, sheet: str, lo: int, hi: int) -> list[str]:
    found: list[tuple[int, str]] = []
    for (name, row_n), (block, _layout_row) in book.row_index.items():
        if name != sheet or row_n < lo or row_n > hi:
            continue
        block_id = getattr(block, "block_id", None)
        if block_id:
            found.append((row_n, block_id))
    found.sort()
    return [book.row_key(sheet, row_n, block_id) for row_n, block_id in found]


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
