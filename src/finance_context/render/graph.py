from __future__ import annotations

from finance_context.graph.models import GraphDocument, TraceDocument

_MD_ESCAPE = str.maketrans({"|": "\\|", "\n": " "})


def render_graph_markdown(doc: GraphDocument) -> str:
    """Markdown with the same summary and links as graph.json."""
    lines = [
        "# Formula graph",
        "",
        f"- Schema: `{doc.schema_version}`",
        f"- Job: `{doc.job_id}`",
        f"- Nodes: {doc.nodes}",
        f"- Edges: {doc.edges}",
        f"- Iterate: {'true' if doc.iterate else 'false'}",
        f"- Links: {len(doc.links)}",
        f"- Unresolved: {doc.unresolved.count}",
        f"- Dangling: {doc.dangling.count}",
        "",
    ]
    if doc.kinds:
        lines.extend(["## Kinds", ""])
        for name, count in doc.kinds.items():
            lines.append(f"- {name}: {count}")
        lines.append("")
    if doc.dangling_classes:
        lines.extend(["## Empty cells", ""])
        for name, count in doc.dangling_classes.items():
            lines.append(f"- {name}: {count}")
        lines.append("")
    if doc.cycles:
        lines.extend(["## Cycles", ""])
        for cycle in doc.cycles:
            members = ", ".join(cycle.members)
            breakers = ", ".join(cycle.breakers)
            lines.append(f"- `{cycle.id}` class `{cycle.class_}` members {members}")
            if breakers:
                lines.append(f"  breakers: {breakers}")
        lines.append("")
    if doc.circularity_hints:
        lines.extend(["## Circularity", ""])
        for hint in doc.circularity_hints:
            lines.append(f"- {hint.sheet} row {hint.row}: {_cell(hint.label)}")
        lines.append("")
    lines.extend(
        [
            "## Links",
            "",
            "| Cell | Formula | Refs |",
            "| --- | --- | --- |",
        ]
    )
    for link in doc.links:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(link.cell),
                    _cell(link.formula or ""),
                    _cell(", ".join(link.refs)),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_trace_markdown(doc: TraceDocument) -> str:
    """Markdown with the same nodes and edges as a trace response."""
    lines = [
        "# Trace",
        "",
        f"- Origin: `{doc.origin}`",
        f"- Direction: `{doc.direction}`",
        f"- Depth: {doc.depth}",
        f"- Stopped: `{doc.stopped or ''}`",
        "",
        "## Nodes",
        "",
        "| Node | Row | Concept | Period | Type | Value | Formula | Depth |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for node in doc.nodes:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(node.node_id),
                    _cell(node.row_key or ""),
                    _cell(node.concept_id or ""),
                    _cell(node.period_id or ""),
                    _cell(node.node_type or ""),
                    _cell(node.cached_value or ""),
                    _cell(node.formula or ""),
                    str(node.depth),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Edges",
            "",
            "| Source | Target | Kind | Period lag |",
            "| --- | --- | --- | --- |",
        ]
    )
    for edge in doc.edges:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(edge.source),
                    _cell(edge.target),
                    _cell(edge.kind or ""),
                    _cell(edge.period_lag or ""),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cell(value: object) -> str:
    return str(value).translate(_MD_ESCAPE).strip()
