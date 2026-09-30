"""Passport projection. Drops blocks and links before the documents are built."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from finance_context.context.catalog import slice_mapping_stats
from finance_context.graph.models import GraphDocument
from finance_context.models.context import ContextDocument
from finance_context.models.summary import SummaryCycle, SummaryDocument, SummaryHint


def build_summary(
    context: ContextDocument | Mapping[str, Any],
    graph: GraphDocument | Mapping[str, Any],
    *,
    meta: Mapping[str, Any] | None = None,
) -> SummaryDocument:
    """Counters from context and graph. Row values and formula links are not copied."""
    parsed_context = _context_without_blocks(context)
    parsed_graph = _graph_without_links(graph)
    identity = dict(meta) if meta is not None else parsed_context.meta.model_dump()
    workbook = parsed_context.workbook
    return SummaryDocument(
        context_schema_version=parsed_context.schema_version,
        graph_schema_version=parsed_graph.schema_version,
        source_filename=_text(identity, "source_filename"),
        content_sha256=_text(identity, "content_sha256"),
        status=_text(identity, "status"),
        stage=_text(identity, "stage"),
        mapping_stats=slice_mapping_stats(parsed_context.mapping_stats),
        sheets=list(workbook.sheets),
        formula_count=workbook.formula_count,
        missing_cached_values=workbook.missing_cached_values,
        unparsed_formulas=workbook.unparsed_formulas,
        iterate=workbook.iterate,
        defined_name_count=len(workbook.defined_names),
        nodes=parsed_graph.nodes,
        edges=parsed_graph.edges,
        kinds=dict(parsed_graph.kinds),
        unresolved=parsed_graph.unresolved,
        external=parsed_graph.external,
        dangling=parsed_graph.dangling,
        dynamic=parsed_graph.dynamic,
        truncated=parsed_graph.truncated,
        dangling_classes=dict(parsed_graph.dangling_classes),
        cycles=[
            SummaryCycle(id=cycle.id, cycle_class=cycle.class_, members=len(cycle.members))
            for cycle in parsed_graph.cycles
        ],
        circularity_hints=[
            SummaryHint(sheet=hint.sheet, row=hint.row, label=hint.label)
            for hint in parsed_graph.circularity_hints
        ],
        warnings=list(parsed_context.warnings),
    )


def _context_without_blocks(context: ContextDocument | Mapping[str, Any]) -> ContextDocument:
    if isinstance(context, ContextDocument):
        return context
    payload = dict(context)
    payload.pop("blocks", None)
    return ContextDocument.model_validate(payload)


def _graph_without_links(graph: GraphDocument | Mapping[str, Any]) -> GraphDocument:
    if isinstance(graph, GraphDocument):
        return graph
    payload = dict(graph)
    payload.pop("links", None)
    return GraphDocument.model_validate(payload)


def _text(source: Mapping[str, Any], key: str) -> str | None:
    value = source.get(key)
    if value is None:
        return None
    return str(value)
