from __future__ import annotations

import json
from collections import Counter
from typing import Any, Protocol

from finance_context.excel.a1 import parse_addr
from finance_context.formulas.engine import FormulaEngine
from finance_context.layout.models import Block, Layout, LayoutRow
from finance_context.layout.periods import infer_grain
from finance_context.mapping.models import (
    Calculation,
    Candidate,
    Concept,
    LexicalPattern,
    RowContext,
    RowRelation,
    ValueKind,
)
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.roles import article_role
from finance_context.mapping.rowfacets import infer_row_facets

_ENGINE = FormulaEngine(locale_hint="en")


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

    def row_key(self, sheet: str, row: int, block_id: str) -> str:
        return f"{sheet}|{row}|{block_id}"


class Signal(Protocol):
    name: str

    def propose(self, ctx: RowContext, book: BookView) -> list[Candidate]: ...


def analyze_structure(book: BookView) -> dict[str, RowPattern]:
    patterns: dict[str, RowPattern] = {}
    relations: list[RowRelation] = []
    for sheet in book.layout.sheets:
        for block in sheet.blocks:
            period_cols = [h.col for h in block.axis.headers]
            for layout_row in block.rows:
                key = book.row_key(sheet.name, layout_row.row, block.block_id)
                pattern = _pattern_for_row(book, sheet.name, layout_row.row, period_cols)
                patterns[key] = pattern
                rel = _relation_from_pattern(book, sheet.name, block, layout_row, pattern)
                if rel is not None:
                    relations.append(rel)
    book.patterns = patterns
    book.relations = relations
    return patterns


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
    grain = infer_grain([h.period_key for h in block.axis.headers])
    value_kind = _value_kind(book, sheet, layout_row.row, block, pattern)
    if _semantic_ratio(layout_row.label):
        value_kind = "ratio"
    elif _semantic_count(layout_row.label):
        value_kind = "count"
    headers = [h.text for h in block.axis.headers[:12]]
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
        article_role=article_role(layout_row, templates),
        query_text=query,
        label_col=layout_row.label_col or block.label_col,
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
    for header in block.axis.headers:
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


def _pattern_for_row(
    book: BookView,
    sheet: str,
    row: int,
    period_cols: list[int],
) -> RowPattern:
    shapes: list[dict[str, Any]] = []
    templates: list[str] = []
    for col in period_cols:
        cell = book.cells.get((sheet, row, col))
        if cell is None:
            continue
        template = cell.get("formula_template")
        if template:
            templates.append(str(template))
        ast = _ast_for(cell, sheet)
        if ast is None:
            continue
        shape = _shape(ast, sheet, col, row)
        if shape is not None:
            shapes.append(shape)
    pattern = RowPattern()
    if templates:
        pattern.template = Counter(templates).most_common(1)[0][0]
    if not shapes:
        return pattern
    kinds = Counter(item["type"] for item in shapes)
    top, count = kinds.most_common(1)[0]
    if count < max(1, int(0.6 * len(shapes))):
        pattern.kind = "mixed"
        pattern.is_ratio = "ratio" in kinds
        return pattern
    pattern.kind = top
    sample = next(item for item in shapes if item["type"] == top)
    if top == "alias":
        pattern.alias_sheet = sample.get("sheet")
        pattern.alias_row = sample.get("row")
    elif top == "aggregate":
        pattern.aggregate_rows = list(sample.get("rows") or [])
        pattern.is_total = True
    elif top == "diff":
        rows = sample.get("rows") or []
        if len(rows) == 2:
            pattern.diff_rows = (int(rows[0]), int(rows[1]))
    elif top == "roll":
        pattern.roll_from_row = sample.get("row")
    elif top == "ratio":
        pattern.is_ratio = True
    return pattern


def _ast_for(cell: dict, sheet: str) -> dict[str, Any] | None:
    raw_json = cell.get("ast_json")
    if raw_json:
        try:
            data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except (TypeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and "op" in data:
            return data
    formula = cell.get("formula_raw")
    if not formula:
        return None
    parsed = _ENGINE.parse(str(formula), sheet=sheet, addr=str(cell.get("addr") or "A1"))
    return parsed.ast


def _shape(ast: dict[str, Any], sheet: str, col: int, row: int) -> dict[str, Any] | None:
    op = ast.get("op")
    if op == "ref":
        target_sheet = ast.get("sheet") or sheet
        target_row = int(ast["row"])
        target_col = int(ast["col"])
        if target_col == col - 1 and target_row != row:
            return {"type": "roll", "sheet": target_sheet, "row": target_row}
        if target_row != row:
            return {"type": "alias", "sheet": target_sheet, "row": target_row}
        return None
    if op == "func" and str(ast.get("name", "")).upper() in {"SUM", "СУММ"}:
        args = ast.get("args") or []
        if len(args) == 1 and args[0].get("op") == "range":
            rows = _range_rows(args[0], col)
            if rows:
                return {"type": "aggregate", "rows": rows}
    if op == "bin" and ast.get("kind") == "-":
        left = _ref_row(ast.get("left") or {}, sheet, col)
        right = _ref_row(ast.get("right") or {}, sheet, col)
        if left and right:
            return {"type": "diff", "rows": [left[1], right[1]]}
    if op == "bin" and ast.get("kind") == "/":
        if _is_proration(ast):
            return {"type": "prorate"}
        return {"type": "ratio"}
    return None


def _ref_row(node: dict[str, Any], sheet: str, col: int) -> tuple[str, int] | None:
    if node.get("op") != "ref":
        return None
    if int(node["col"]) != col:
        return None
    return (str(node.get("sheet") or sheet), int(node["row"]))


def _range_rows(node: dict[str, Any], col: int) -> list[int]:
    start = node.get("start") or {}
    end = node.get("end") or {}
    if start.get("col") != col or end.get("col") != col:
        return []
    lo = int(start["row"])
    hi = int(end["row"])
    if hi < lo:
        lo, hi = hi, lo
    return list(range(lo, hi + 1))


def _relation_from_pattern(
    book: BookView,
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


def _block_id(book: BookView, sheet: str, row: int) -> str | None:
    found = book.row_index.get((sheet, row))
    if found is None:
        return None
    return found[0].block_id


class StructureSignal:
    name = "structure"

    def propose(self, ctx: RowContext, book: BookView) -> list[Candidate]:
        pattern = book.patterns.get(ctx.row_key)
        if pattern is None:
            return []
        out: list[Candidate] = []
        if pattern.kind == "alias" and pattern.alias_row is not None:
            block_id = _block_id(book, pattern.alias_sheet or ctx.sheet, pattern.alias_row)
            if block_id:
                key = book.row_key(
                    pattern.alias_sheet or ctx.sheet, pattern.alias_row, block_id
                )
                concept_id = book.concepts.get(key)
                if concept_id:
                    out.append(
                        Candidate(
                            concept_id=concept_id,
                            score=0.96,
                            signal=self.name,
                            evidence=f"alias of {key}",
                        )
                    )
        if pattern.kind == "aggregate" and pattern.aggregate_rows:
            members: list[str] = []
            child_ids: list[str] = []
            for row_n in pattern.aggregate_rows:
                block_id = _block_id(book, ctx.sheet, row_n)
                if not block_id:
                    continue
                key = book.row_key(ctx.sheet, row_n, block_id)
                members.append(key)
                concept_id = book.concepts.get(key)
                if concept_id:
                    child_ids.append(concept_id)
            if child_ids:
                shared = _shared_concept(
                    child_ids,
                    book,
                    mapped=len(child_ids),
                    members=len(members),
                )
                if shared:
                    out.append(
                        Candidate(
                            concept_id=shared,
                            score=0.93,
                            signal=self.name,
                            evidence=f"sum of {len(child_ids)} child rows",
                        )
                    )
        if pattern.kind == "diff" and pattern.diff_rows:
            left_id = _concept_at(book, ctx.sheet, pattern.diff_rows[0])
            right_id = _concept_at(book, ctx.sheet, pattern.diff_rows[1])
            parent = _diff_parent(left_id, right_id, book)
            if parent:
                out.append(
                    Candidate(
                        concept_id=parent,
                        score=0.94,
                        signal=self.name,
                        evidence="declared difference of mapped operands",
                    )
                )
        if pattern.kind == "roll":
            source_id = _concept_at(book, ctx.sheet, pattern.roll_from_row or -1)
            if source_id:
                out.append(
                    Candidate(
                        concept_id=source_id,
                        score=0.9,
                        signal=self.name,
                        evidence="roll-forward from prior period",
                    )
                )
        return out


def _concept_at(book: BookView, sheet: str, row: int) -> str | None:
    block_id = _block_id(book, sheet, row)
    if block_id is None:
        return None
    return book.concepts.get(book.row_key(sheet, row, block_id))


def _shared_concept(
    child_ids: list[str],
    book: BookView,
    *,
    mapped: int,
    members: int,
) -> str | None:
    if members <= 0 or mapped < members:
        return None
    unique = list(dict.fromkeys(child_ids))
    if len(unique) == 1:
        return unique[0]
    calc_parent = _aggregate_parent(unique, book)
    if calc_parent:
        return calc_parent
    broaders: list[str] = []
    prefixes: list[str] = []
    for cid in unique:
        concept = book.taxonomy.get(cid)
        if concept and concept.broader:
            broaders.append(concept.broader)
        parts = cid.split(".")
        if len(parts) >= 2:
            prefixes.append(".".join(parts[:2]))
    if broaders and len(set(broaders)) == 1:
        shared = broaders[0]
        if shared in book.taxonomy:
            return shared
    if prefixes and len(set(prefixes)) == 1:
        shared = prefixes[0]
        if shared in book.taxonomy:
            return shared
    return None


def _diff_parent(left_id: str | None, right_id: str | None, book: BookView) -> str | None:
    if not left_id or not right_id or left_id == right_id:
        return None
    observed = {left_id, right_id}
    for calc in book.calculations:
        if len(calc.terms) != 2:
            continue
        weights = {term.concept: term.weight for term in calc.terms}
        if set(weights) != observed:
            continue
        if weights[left_id] * weights[right_id] < 0 and calc.parent in book.taxonomy:
            return calc.parent
    return None


def _aggregate_parent(child_ids: list[str], book: BookView) -> str | None:
    observed = set(child_ids)
    for calc in book.calculations:
        if any(term.weight < 0 for term in calc.terms):
            continue
        terms = {term.concept for term in calc.terms}
        if not terms:
            continue
        if observed == terms and calc.parent in book.taxonomy:
            return calc.parent
    return None


def _is_proration(ast: dict[str, Any]) -> bool:
    right = ast.get("right") or {}
    op = right.get("op")
    if op in {"name", "num"}:
        return True
    if op == "bin":
        return _is_proration({"right": right.get("right") or {}})
    return False


def _semantic_ratio(label: str | None) -> bool:
    n = normalize_label(label)
    raw = (label or "").casefold()
    return any(
        token in n or token in raw
        for token in (
            "dscr",
            "llcr",
            "plcr",
            "coverage",
            "conversion",
            "leverage",
            "runway",
            "ratio",
        )
    )


def _semantic_count(label: str | None) -> bool:
    n = normalize_label(label)
    return "week #" in n or n.endswith("week") or "trough cash week" in n


def parse_cell_addr(addr: str) -> tuple[int, int]:
    return parse_addr(addr)


def section_tokens(ctx: RowContext) -> set[str]:
    blob = " ".join([ctx.label, ctx.parent_label or "", *ctx.section_path, ctx.sheet])
    return set(normalize_label(blob).split())
