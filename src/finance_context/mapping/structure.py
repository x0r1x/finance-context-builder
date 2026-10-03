from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from finance_context.formulas.engine import FormulaEngine
from finance_context.layout.resolve import period_headers
from finance_context.mapping.book import BookView, RowPattern, _block_id, _concept_at
from finance_context.mapping.book import Signal as Signal
from finance_context.mapping.book import build_row_context as build_row_context
from finance_context.mapping.models import (
    Candidate,
    RowContext,
    RowRelation,
)
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.patterns import pattern_matches
from finance_context.mapping.priors import _graph_priors, _neighbor_priors
from finance_context.mapping.relations import (
    _diff_parent,
    _equity_cashflow_sum,
    _relation_from_pattern,
    _shared_concept,
)
from finance_context.mapping.statement import remap_alias_concept

_ENGINE = FormulaEngine(locale_hint="en")


def analyze_structure(book: BookView) -> dict[str, RowPattern]:
    patterns: dict[str, RowPattern] = {}
    relations: list[RowRelation] = []
    for sheet in book.layout.sheets:
        for block in sheet.blocks:
            headers = period_headers(sheet, block)
            period_cols = [header.col for header in headers]
            if getattr(block, "kind", "timeline") == "params":
                period_cols = [
                    header.col for header in headers if header.role in {"value", "scenario"}
                ]
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
                    remapped, extra, score = remap_alias_concept(concept_id, ctx)
                    if remapped:
                        evidence = f"alias of {key}"
                        if extra:
                            evidence = f"{evidence}; {extra}"
                        out.append(
                            Candidate(
                                concept_id=remapped,
                                score=score,
                                signal=self.name,
                                evidence=evidence,
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
                equity_net = _equity_cashflow_sum(ctx, child_ids, book)
                if equity_net:
                    out.append(
                        Candidate(
                            concept_id=equity_net,
                            score=0.95,
                            signal=self.name,
                            evidence="sum of equity irr cash lines",
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
        out.extend(_graph_priors(ctx, book))
        out.extend(_neighbor_priors(ctx, book))
        return [item for item in out if not _skipped_concept(ctx, book, item.concept_id)]


def _skipped_concept(ctx: RowContext, book: BookView, concept_id: str) -> bool:
    n = normalize_label(ctx.label)
    tokens = set(n.split())
    extra = section_tokens(ctx)
    for pattern in book.lexical_patterns:
        if pattern.skip_concept != concept_id:
            continue
        if pattern_matches(pattern.when, label=n, tokens=tokens, section=extra):
            return True
    return False


def _is_proration(ast: dict[str, Any]) -> bool:
    right = ast.get("right") or {}
    op = right.get("op")
    if op in {"name", "num"}:
        return True
    if op == "bin":
        return _is_proration({"right": right.get("right") or {}})
    return False


def section_tokens(ctx: RowContext) -> set[str]:
    blob = " ".join([ctx.label, ctx.parent_label or "", *ctx.section_path, ctx.sheet])
    tokens = set(normalize_label(blob).split())
    for part in (ctx.label, ctx.parent_label, *ctx.section_path, ctx.sheet):
        if part:
            tokens |= set(re.findall(r"[a-z0-9]+", str(part).casefold()))
    return tokens
