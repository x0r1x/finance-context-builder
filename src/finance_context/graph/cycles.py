from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components

from finance_context.graph.models import CycleRecord
from finance_context.graph.refs import parse_node_id


def classify_cycles(edges: list[dict]) -> list[CycleRecord]:
    usable = [
        edge
        for edge in edges
        if edge.get("source")
        and edge.get("target")
        and not edge.get("unresolved")
        and not edge.get("dangling")
        and edge.get("kind") in {"ref", "cross_sheet", "range"}
    ]
    nodes = sorted({str(e["source"]) for e in usable} | {str(e["target"]) for e in usable})
    if not nodes:
        return []
    index = {name: i for i, name in enumerate(nodes)}
    rows = np.fromiter((index[str(e["source"])] for e in usable), dtype=np.int32, count=len(usable))
    cols = np.fromiter((index[str(e["target"])] for e in usable), dtype=np.int32, count=len(usable))
    data = np.ones(len(usable), dtype=np.uint8)
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(len(nodes), len(nodes)), dtype=np.uint8)
    _n_comp, labels = connected_components(matrix, directed=True, connection="strong")
    groups: dict[int, list[str]] = defaultdict(list)
    for name, label in zip(nodes, labels, strict=True):
        groups[int(label)].append(name)

    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for edge in usable:
        by_pair[(str(edge["source"]), str(edge["target"]))].append(edge)

    cycles: list[CycleRecord] = []
    seq = 1
    for members in groups.values():
        member_set = set(members)
        intra = [
            edge
            for src, tgt in by_pair
            if src in member_set and tgt in member_set
            for edge in by_pair[(src, tgt)]
        ]
        self_loop = any(e["source"] == e["target"] for e in intra)
        if len(members) < 2 and not self_loop:
            continue
        cls = _classify(members, intra)
        cycles.append(
            CycleRecord.model_validate(
                {"id": f"c{seq}", "members": sorted(members), "class": cls}
            )
        )
        seq += 1
    return cycles


def _classify(members: list[str], intra: list[dict]) -> str:
    if not intra:
        return "unexpected"
    if all(_is_period_chain(edge) for edge in intra):
        return "iterative_ok"
    return "unexpected"


def _is_period_chain(edge: dict) -> bool:
    lag = edge.get("period_lag")
    if lag in {"same", "0", "unaligned"}:
        return False
    if lag not in (None, ""):
        try:
            return int(lag) != 0
        except (TypeError, ValueError):
            return False
    offset = edge.get("col_offset")
    if offset in (None, 0):
        return False
    src = parse_node_id(str(edge.get("source") or ""))
    tgt = parse_node_id(str(edge.get("target") or ""))
    return src is not None and tgt is not None and src[0] == tgt[0] and src[2] == tgt[2]
