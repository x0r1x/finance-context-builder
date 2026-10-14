from __future__ import annotations

import re

from finance_context.layout.formulas_shape import _pattern_for_row
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


def section_tokens(ctx: RowContext) -> set[str]:
    blob = " ".join([ctx.label, ctx.parent_label or "", *ctx.section_path, ctx.sheet])
    tokens = set(normalize_label(blob).split())
    for part in (ctx.label, ctx.parent_label, *ctx.section_path, ctx.sheet):
        if part:
            tokens |= set(re.findall(r"[a-z0-9]+", str(part).casefold()))
    return tokens
