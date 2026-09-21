from __future__ import annotations

from collections import Counter
from pathlib import Path

from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.models import GRAPH_SCHEMA_VERSION, ID_CAP, GraphDocument, IdCount
from finance_context.graph.refs import parse_node_id
from finance_context.layout.models import Layout
from finance_context.mapping.models import MappingDocument
from finance_context.models.context import GraphPointer
from finance_context.store.fs import read_parquet, write_json, write_parquet

INDEX_COLUMNS = (
    ("node_id", "VARCHAR"),
    ("sheet", "VARCHAR"),
    ("addr", "VARCHAR"),
    ("row_key", "VARCHAR"),
    ("concept_id", "VARCHAR"),
    ("period_id", "VARCHAR"),
    ("node_type", "VARCHAR"),
)


def build_formula_graph(
    dest_dir: Path,
    *,
    job_id: str,
    layout: Layout,
    mapping: MappingDocument,
) -> GraphPointer:
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    edges = read_parquet(dest_dir / "ir" / "edges.parquet") if (
        dest_dir / "ir" / "edges.parquet"
    ).is_file() else []
    cell_edges_path = dest_dir / "ir" / "cell_edges.parquet"
    cell_edges = read_parquet(cell_edges_path) if cell_edges_path.is_file() else []

    row_meta = _row_meta(layout, mapping)
    period_by_cell, period_index = _period_maps(layout)
    index_rows = _index_rows(cells, row_meta, period_by_cell)
    write_parquet(dest_dir / "ir" / "graph_index.parquet", INDEX_COLUMNS, index_rows)

    period_of = {row[0]: row[5] for row in index_rows}
    enriched = [_enrich_edge(edge, period_of, period_index) for edge in cell_edges]
    write_parquet(
        dest_dir / "ir" / "cell_edges.parquet",
        IR_CELL_EDGE_COLUMNS,
        [
            (
                e["source"],
                e["target"],
                e.get("kind"),
                bool(e.get("unresolved")),
                bool(e.get("truncated")),
                bool(e.get("dangling")),
                e.get("col_offset"),
                e.get("period_lag"),
            )
            for e in enriched
        ],
    )

    cycles = classify_cycles(enriched)
    doc = _summary(job_id, cells, edges, enriched, cycles)
    write_json(dest_dir / "graph.json", doc.model_dump(mode="json", by_alias=True))
    unexpected = sum(1 for c in cycles if c.class_ == "unexpected")
    iterative = sum(1 for c in cycles if c.class_ == "iterative_ok")
    return GraphPointer(
        artifact="graph.json",
        cell_edges="ir/cell_edges.parquet",
        index="ir/graph_index.parquet",
        nodes=doc.nodes,
        edges=doc.edges,
        cycles_unexpected=unexpected,
        cycles_iterative=iterative,
        unresolved=doc.unresolved.count,
        dangling=doc.dangling.count,
    )


def _row_meta(
    layout: Layout, mapping: MappingDocument
) -> dict[tuple[str, int], tuple[str, str | None]]:
    concepts = {(row.sheet, row.row): row.concept_id for row in mapping.rows}
    out: dict[tuple[str, int], tuple[str, str | None]] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                key = (sheet.name, row.row)
                row_key = f"{sheet.name}|{row.row}|{block.block_id}"
                out[key] = (row_key, concepts.get(key) or concepts.get((sheet.name, row.row)))
    return out


def _period_maps(layout: Layout) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    period_by_cell: dict[tuple[str, int], str] = {}
    index: dict[str, int] = {}
    seq = 0
    for sheet in layout.sheets:
        for block in sheet.blocks:
            local: dict[str, int] = {}
            for pos, header in enumerate(block.axis.headers):
                period_by_cell[(sheet.name, header.col)] = header.period_key
                if header.period_key not in local:
                    local[header.period_key] = pos
                if header.period_key not in index:
                    index[header.period_key] = seq
                    seq += 1
    return period_by_cell, index


def _index_rows(
    cells: list[dict],
    row_meta: dict[tuple[str, int], tuple[str, str | None]],
    period_by_cell: dict[tuple[str, int], str],
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for cell in cells:
        sheet = str(cell["sheet"])
        addr = str(cell["addr"])
        row_n = int(cell["row"])
        col_n = int(cell["col"])
        meta = row_meta.get((sheet, row_n))
        formula = cell.get("formula_raw")
        cached = cell.get("cached_value")
        if formula:
            node_type = "calculated"
        elif cached not in (None, ""):
            node_type = "input"
        else:
            node_type = "empty"
        rows.append(
            (
                f"{sheet}!{addr}",
                sheet,
                addr,
                meta[0] if meta else None,
                meta[1] if meta else None,
                period_by_cell.get((sheet, col_n)),
                node_type,
            )
        )
    return rows


def _enrich_edge(
    edge: dict, period_of: dict[str, str | None], period_index: dict[str, int]
) -> dict:
    source = str(edge.get("source") or "")
    target = str(edge.get("target") or "")
    src = parse_node_id(source)
    tgt = parse_node_id(target)
    col_offset = None
    if src is not None and tgt is not None:
        col_offset = tgt[1] - src[1]
    src_p = period_of.get(source)
    tgt_p = period_of.get(target)
    if not src_p or not tgt_p:
        lag = "unaligned"
    elif src_p == tgt_p:
        lag = "same"
    elif src_p in period_index and tgt_p in period_index:
        lag = str(period_index[tgt_p] - period_index[src_p])
    else:
        lag = "unaligned"
    out = dict(edge)
    out["col_offset"] = col_offset
    out["period_lag"] = lag
    return out


def _summary(
    job_id: str,
    cells: list[dict],
    formula_edges: list[dict],
    cell_edges: list[dict],
    cycles,
) -> GraphDocument:
    kinds = Counter(str(e.get("kind") or "ref") for e in cell_edges)

    def bucket(pred) -> IdCount:
        ids: list[str] = []
        for edge in formula_edges:
            if pred(edge) and edge.get("source"):
                src = str(edge["source"])
                if src not in ids:
                    ids.append(src)
        return IdCount(count=len(ids), ids=ids[:ID_CAP])

    dangling_ids: list[str] = []
    for edge in cell_edges:
        if edge.get("dangling") and edge.get("target"):
            tgt = str(edge["target"])
            if tgt not in dangling_ids:
                dangling_ids.append(tgt)
    truncated_ids: list[str] = []
    for edge in cell_edges:
        if edge.get("truncated") and edge.get("source"):
            src = str(edge["source"])
            if src not in truncated_ids:
                truncated_ids.append(src)
    return GraphDocument(
        schema_version=GRAPH_SCHEMA_VERSION,
        job_id=job_id,
        nodes=len(cells),
        edges=len(cell_edges),
        kinds=dict(kinds),
        unresolved=bucket(
            lambda e: e.get("unresolved") and e.get("kind") not in {"dynamic", "external"}
        ),
        dynamic=bucket(lambda e: e.get("kind") == "dynamic"),
        external=bucket(lambda e: e.get("kind") == "external"),
        truncated=IdCount(count=len(truncated_ids), ids=truncated_ids[:ID_CAP]),
        dangling=IdCount(count=len(dangling_ids), ids=dangling_ids[:ID_CAP]),
        cycles=cycles,
    )
