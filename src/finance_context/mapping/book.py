from __future__ import annotations

from typing import Protocol

from finance_context.layout.models import Block, Layout, LayoutRow
from finance_context.layout.resolve import axes_for, period_headers
from finance_context.mapping.graph import row_adjacency
from finance_context.mapping.models import (
    Calculation,
    Candidate,
    Concept,
    LexicalPattern,
    RowContext,
    RowRelation,
    ValueKind,
)
from finance_context.mapping.normalize import memory_unit, normalize_label
from finance_context.mapping.roles import article_role
from finance_context.mapping.rowfacets import infer_row_facets


class RowPattern:
    __slots__ = (
        "kind",
        "alias_sheet",
        "alias_row",
        "aggregate_rows",
        "diff_rows",
        "roll_from_row",
        "is_ratio",
        "is_total",
        "template",
    )

    def __init__(self) -> None:
        self.kind = "value"
        self.alias_sheet: str | None = None
        self.alias_row: int | None = None
        self.aggregate_rows: list[int] = []
        self.diff_rows: tuple[int, int] | None = None
        self.roll_from_row: int | None = None
        self.is_ratio = False
        self.is_total = False
        self.template: str | None = None


class BookView:
    def __init__(
        self,
        layout: Layout,
        cells: list[dict],
        taxonomy: list[Concept],
        *,
        calculations: list[Calculation] | None = None,
        patterns: list[LexicalPattern] | None = None,
        edges: list[dict] | None = None,
    ) -> None:
        self.layout = layout
        self.taxonomy = {c.id: c for c in taxonomy}
        self.calculations = list(calculations or [])
        self.lexical_patterns = list(patterns or [])
        self.cells: dict[tuple[str, int, int], dict] = {
            (str(c["sheet"]), int(c["row"]), int(c["col"])): c for c in cells
        }
        self.by_row: dict[tuple[str, int], list[dict]] = {}
        for cell in cells:
            key = (str(cell["sheet"]), int(cell["row"]))
            self.by_row.setdefault(key, []).append(cell)
        self.row_index: dict[tuple[str, int], tuple[Block, LayoutRow]] = {}
        for sheet in layout.sheets:
            for block in sheet.blocks:
                for row in block.rows:
                    self.row_index[(sheet.name, row.row)] = (block, row)
        self.patterns: dict[str, RowPattern] = {}
        self.concepts: dict[str, str] = {}
        self.relations: list[RowRelation] = []
        self.precedents, self.dependents = row_adjacency(edges or [])

    def row_key(self, sheet: str, row: int, block_id: str) -> str:
        return f"{sheet}|{row}|{block_id}"


class Signal(Protocol):
    name: str

    def propose(self, ctx: RowContext, book: BookView) -> list[Candidate]: ...


def build_row_context(
    book: BookView,
    sheet: str,
    block: Block,
    layout_row: LayoutRow,
    parent_label: str | None,
    templates: list[str | None],
) -> RowContext:
    key = book.row_key(sheet, layout_row.row, block.block_id)
    pattern = book.patterns.get(key) or RowPattern()
    block_axes = axes_for(
        next(item for item in book.layout.sheets if item.name == sheet),
        block,
    )
    anchor_col: int | None = None
    grain: str | None = None
    headers: list[str] = []
    if getattr(block, "kind", "timeline") == "params":
        value_cols = [cell.col for cell in layout_row.cells if cell.role == "value"]
        if value_cols:
            anchor_col = min(value_cols)
    elif block_axes:
        primary = max(block_axes, key=lambda axis: len(axis.periods))
        grain = primary.grain
        headers = [period.text for period in primary.periods[:12]]
        if primary.periods:
            anchor_col = min(period.col for period in primary.periods)
    value_kind = _value_kind(book, sheet, layout_row.row, block, pattern)
    unit_kind = _unit_from_row_cells(book, sheet, layout_row)
    if unit_kind and not (unit_kind == "money" and value_kind == "rate"):
        # A percent format on the values outranks a copied `EUR'000` caption.
        value_kind = unit_kind
    else:
        if _semantic_ratio(layout_row.label, value_kind):
            value_kind = "ratio"
        elif _semantic_count(layout_row.label):
            value_kind = "count"
        if _lease_rate_input(layout_row.label, book, sheet, layout_row.row, block, value_kind):
            value_kind = "rate"
    row_memory_unit = memory_unit(_unit_text(book, sheet, layout_row), value_kind)
    section = " / ".join(layout_row.section_path)
    query = " | ".join(
        part
        for part in (
            layout_row.label,
            parent_label or "",
            section,
            sheet,
            value_kind,
        )
        if part
    )
    prev_labels, next_labels = _neighbor_labels(block, layout_row.row)
    ctx = RowContext(
        row_key=key,
        sheet=sheet,
        row=layout_row.row,
        block_id=block.block_id,
        label=layout_row.label,
        parent_label=parent_label,
        section_path=list(layout_row.section_path),
        kind=layout_row.kind,
        value_kind=value_kind,
        is_total=pattern.is_total or bool(pattern.aggregate_rows),
        period_grain=grain,
        period_headers=headers,
        article_role=article_role(
            layout_row,
            templates,
            block_kind=getattr(block, "kind", "timeline"),
        ),
        query_text=query,
        label_col=layout_row.label_col or block.label_col,
        anchor_col=anchor_col,
        prev_labels=prev_labels,
        next_labels=next_labels,
        memory_unit=row_memory_unit,
    )
    ctx.inferred_facets = infer_row_facets(ctx, pattern_kind=pattern.kind)
    return ctx


def _value_kind(
    book: BookView,
    sheet: str,
    row: int,
    block: Block,
    pattern: RowPattern,
) -> ValueKind:
    if pattern.kind == "prorate":
        return "money"
    if pattern.is_ratio:
        return "ratio"
    percents = 0
    numbers = 0
    for header in period_headers(
        next(item for item in book.layout.sheets if item.name == sheet),
        block,
    ):
        cell = book.cells.get((sheet, row, header.col))
        if cell is None:
            continue
        fmt = str(cell.get("number_format") or "")
        if "%" in fmt:
            percents += 1
        text = cell.get("cached_value")
        if text not in (None, ""):
            numbers += 1
    if percents and percents >= max(1, numbers // 2):
        return "rate"
    return "money"


def _unit_text(book: BookView, sheet: str, layout_row: LayoutRow) -> str | None:
    for item in layout_row.cells:
        if item.role != "unit":
            continue
        cell = book.cells.get((sheet, layout_row.row, item.col))
        text = "" if cell is None else str(cell.get("cached_value") or "")
        if text.strip():
            return text
    return None


def _unit_from_row_cells(book: BookView, sheet: str, layout_row: LayoutRow) -> ValueKind | None:
    from finance_context.layout.params import unit_kind_from_text

    for item in layout_row.cells:
        if item.role != "unit":
            continue
        cell = book.cells.get((sheet, layout_row.row, item.col))
        text = None if cell is None else str(cell.get("cached_value") or "")
        kind = unit_kind_from_text(text)
        if kind in {"money", "rate", "ratio", "count"}:
            return kind  # type: ignore[return-value]
    return None


def _block_id(book: BookView, sheet: str, row: int) -> str | None:
    found = book.row_index.get((sheet, row))
    if found is None:
        return None
    return found[0].block_id


def _concept_at(book: BookView, sheet: str, row: int) -> str | None:
    block_id = _block_id(book, sheet, row)
    if block_id is None:
        return None
    return book.concepts.get(book.row_key(sheet, row, block_id))


def _semantic_ratio(label: str | None, value_kind: ValueKind | None = None) -> bool:
    n = normalize_label(label)
    raw = (label or "").casefold()
    tokens = set(n.split())
    if "fcfe" in tokens and "equity" in tokens and ("/" in raw or "ratio" in tokens):
        return True
    if "cost of capital" in n:
        return True
    if "availability" in tokens and "generation" not in tokens:
        return True
    if "lease" in tokens:
        return value_kind in {"rate", "ratio"}
    return bool(
        tokens
        & {
            "wacc",
            "coc",
            "dscr",
            "llcr",
            "plcr",
            "coverage",
            "conversion",
            "leverage",
            "runway",
            "ratio",
            "cpi",
            "inflation",
            "escalation",
            "payout",
            "hedge",
            "hedged",
            "uncertainty",
        }
    )


def _lease_rate_input(
    label: str | None,
    book: BookView,
    sheet: str,
    row: int,
    block: Block,
    value_kind: ValueKind,
) -> bool:
    if normalize_label(label) != "variable land lease":
        return False
    if value_kind in {"rate", "ratio"}:
        return True
    numbers = 0
    percents = 0
    for header in period_headers(
        next(item for item in book.layout.sheets if item.name == sheet),
        block,
    ):
        cell = book.cells.get((sheet, row, header.col))
        if cell is None:
            continue
        if "%" in str(cell.get("number_format") or ""):
            percents += 1
        if cell.get("cached_value") not in (None, ""):
            numbers += 1
    return percents > 0 or numbers == 0


def _neighbor_labels(block: Block, row: int, span: int = 2) -> tuple[list[str], list[str]]:
    ordered = [item for item in block.rows if item.label]
    index = next((i for i, item in enumerate(ordered) if item.row == row), None)
    if index is None:
        return [], []
    prev_labels = [item.label for item in ordered[max(0, index - span) : index]]
    next_labels = [item.label for item in ordered[index + 1 : index + 1 + span]]
    return prev_labels, next_labels


def _semantic_count(label: str | None) -> bool:
    n = normalize_label(label)
    tokens = set(n.split())
    if "week #" in n or n.endswith("week") or "trough cash week" in n:
        return True
    if any(
        part in n
        for part in ("lifetime", "turbine", "traffic", "vehicles", "generation", "mwh")
    ):
        return True
    if n in {"development and construction", "straight line depreciation"}:
        return True
    return "capacity" in n or "mw" in tokens
