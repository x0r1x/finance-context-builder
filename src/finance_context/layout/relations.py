from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from finance_context.layout.formulas_shape import RowPattern
from finance_context.layout.models import Block, LayoutRow


class RowRelation(BaseModel):
    kind: str
    source_row_key: str
    target_row_key: str | None = None
    member_row_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None


class _RelationBook(Protocol):
    row_index: dict[tuple[str, int], tuple[object, object]]

    def row_key(self, sheet: str, row: int, block_id: str) -> str: ...


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
    if pattern.kind == "aggregate" and pattern.aggregate_rows:
        members = []
        for row_n in pattern.aggregate_rows:
            block_id = _block_id(book, sheet, row_n)
            if block_id:
                members.append(book.row_key(sheet, row_n, block_id))
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
