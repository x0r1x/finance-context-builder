from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection

from finance_context.excel.a1 import index_to_col, parse_addr
from finance_context.formulas.csr import (
    RangeNode,
    SheetUsage,
    expand_range,
    present_count,
    range_node_for,
)
from finance_context.formulas.models import Edge, as_edge


def cell_ref_row(ref: str | None) -> tuple[str, int] | None:
    if not ref or "!" not in ref:
        return None
    sheet, addr = ref.rsplit("!", 1)
    sheet = sheet.strip().strip("'").replace("''", "'")
    addr = addr.split(":")[0].replace("$", "")
    try:
        _col, row = parse_addr(addr)
    except ValueError:
        return None
    if not sheet:
        return None
    return sheet, row


def row_key_ref(sheet: str, row: int) -> str:
    return f"{sheet}!{row}"


def _targets(edge: Edge, usage: SheetUsage | None = None) -> list[str]:
    target = str(edge.target or "")
    if not target:
        return []
    # Cell edges already name one target. Only a formula-level range still expands.
    body = target.rsplit("!", 1)[-1]
    if edge.kind == "range" and not edge.unresolved and ":" in body:
        try:
            cells, _trunc = expand_range(target, usage=usage)
        except ValueError:
            return [target]
        return cells
    return [target]


class RangeReads:
    """Large ranges read by formula rows, once per range.

    A range that is a range node in the graph (a whole column or row, or a
    rectangle over RANGE_NODE_MIN_CELLS) is not expanded into member rows. The
    reading row names the range, and the range lists its bounds and its reading
    rows. Rows inside the range get no back-reference: one SUMIF(A:A) copied
    down N rows, or a running total SUM($B$2:B{n}), stays linear in N.
    """

    def __init__(self) -> None:
        self.by_row: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
        self.nodes: dict[str, RangeNode] = {}
        self.readers: dict[str, dict[str, None]] = defaultdict(dict)

    def add(self, source: tuple[str, int], node: RangeNode) -> None:
        self.nodes.setdefault(node.node_id, node)
        self.by_row[source].setdefault(node.node_id, None)
        self.readers[node.node_id].setdefault(row_key_ref(*source), None)

    def for_row(self, sheet: str, row: int) -> list[str]:
        return list(self.by_row.get((sheet, row), {}))

    def records(self, usage: SheetUsage | None) -> list[dict]:
        out: list[dict] = []
        for node_id, node in self.nodes.items():
            whole_rows = node.c1 == 1 and node.c2 == 16_384
            whole_cols = node.r1 == 1 and node.r2 == 1_048_576
            out.append(
                {
                    "id": node_id,
                    "sheet": node.sheet,
                    "col_start": None if whole_rows else index_to_col(node.c1),
                    "col_end": None if whole_rows else index_to_col(node.c2),
                    "row_start": None if whole_cols else node.r1,
                    "row_end": None if whole_cols else node.r2,
                    "cells": present_count(node, usage) if usage is not None else 0,
                    "readers": list(self.readers.get(node_id, {})),
                }
            )
        return out


def row_adjacency(
    edges: list,
    *,
    limit: int = 8,
    usage: SheetUsage | None = None,
    ranges: RangeReads | None = None,
    rows: Collection[tuple[str, int]] | None = None,
) -> tuple[dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]]]:
    """Row precedents and dependents. With ``ranges``, large ranges go there instead.

    ``rows`` is the set of rows whose neighbor lists are read. Other rows are
    not stored. Range nodes are still recorded for every edge. ``None`` keeps
    every row.
    """
    keep = None if rows is None else rows if isinstance(rows, set) else set(rows)
    neighbors = keep is None or bool(keep)
    precedents: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
    dependents: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
    for raw in edges:
        edge = as_edge(raw)
        if edge.named:
            continue
        source = cell_ref_row(str(edge.source or ""))
        if source is None:
            continue
        if ranges is not None and edge.kind == "range" and not edge.unresolved:
            node = range_node_for(str(edge.target or ""), usage)
            if node is not None:
                ranges.add(source, node)
                continue
        if not neighbors:
            continue
        # A source outside ``rows`` is still walked: it may depend on a row inside.
        source_kept = keep is None or source in keep
        for target_ref in _targets(edge, usage):
            target = cell_ref_row(target_ref)
            if target is None or source == target:
                continue
            if source_kept:
                precedents[source].setdefault(row_key_ref(*target), None)
            if keep is None or target in keep:
                dependents[target].setdefault(row_key_ref(*source), None)
    cut = limit if limit > 0 else None
    return _neighbor_lists(precedents, cut), _neighbor_lists(dependents, cut)


def _neighbor_lists(
    found: dict[tuple[str, int], dict[str, None]],
    cut: int | None,
) -> dict[tuple[str, int], list[str]]:
    """One list per row. The dict of dicts is dropped as the lists are built."""
    out: dict[tuple[str, int], list[str]] = {}
    while found:
        key, values = found.popitem()
        out[key] = list(values)[:cut]
    return out
