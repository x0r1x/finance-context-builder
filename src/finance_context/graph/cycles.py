from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components

from finance_context.formulas.csr import CellEdge, RangeNode, as_cell_edge, is_range_node
from finance_context.graph.models import CycleRecord
from finance_context.graph.ranges import RangeHops, hop_flags, is_cell_id
from finance_context.graph.refs import parse_node_id


def classify_cycles(
    edges: list,
    *,
    hops: RangeHops | None = None,
    range_edges: Sequence[tuple[str, RangeNode]] = (),
    period_chain: Callable[[str, str], bool] | None = None,
) -> list[CycleRecord]:
    """Strongly connected formula cells and their class.

    An edge into a range node is followed through ``hops`` to the formula cells
    inside the range. Those links join components but do not vote on the class;
    ``period_chain`` rates the formula -> member edges a per-formula expansion
    would have had instead.
    """
    usable = [
        edge
        for edge in (as_cell_edge(item) for item in edges)
        if edge.source
        and edge.target
        and not edge.unresolved
        and not edge.dangling
        and edge.kind in {"ref", "cross_sheet", "range"}
    ]
    structural: list[tuple[str, str]] = []
    if hops is not None:
        for node in {node.node_id: node for _source, node in range_edges}.values():
            structural.extend(hops.edges(node))
    names = {str(edge.source) for edge in usable} | {str(edge.target) for edge in usable}
    for parent, child in structural:
        names.add(parent)
        names.add(child)
    nodes = sorted(names)
    if not nodes:
        return []
    index = {name: i for i, name in enumerate(nodes)}
    pairs = [(str(edge.source), str(edge.target)) for edge in usable] + structural
    rows = np.fromiter((index[src] for src, _tgt in pairs), dtype=np.int32, count=len(pairs))
    cols = np.fromiter((index[tgt] for _src, tgt in pairs), dtype=np.int32, count=len(pairs))
    data = np.ones(len(pairs), dtype=np.uint8)
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(len(nodes), len(nodes)), dtype=np.uint8)
    _n_comp, labels = connected_components(matrix, directed=True, connection="strong")
    order: list[int] = []
    members: dict[int, list[str]] = {}
    for name, label_raw in zip(nodes, labels, strict=True):
        label = int(label_raw)
        bucket = members.get(label)
        if bucket is None:
            members[label] = [name]
            order.append(label)
        else:
            bucket.append(name)
    sizes = np.bincount(labels)
    intra_by: dict[int, list[CellEdge]] = defaultdict(list)
    same = labels[rows] == labels[cols]
    for pos in np.flatnonzero(same):
        slot = int(pos)
        # Links into and inside a range node only join components.
        if slot >= len(usable) or is_range_node(str(usable[slot].target)):
            continue
        label = int(labels[rows[slot]])
        edge = usable[slot]
        if sizes[label] < 2 and edge.source != edge.target:
            continue
        intra_by[label].append(edge)
    hop_bits: dict[int, list[bool]] = defaultdict(list)
    hop_self: set[int] = set()
    if hops is not None and period_chain is not None:

        def label_of(name: str) -> int | None:
            slot = index.get(name)
            return None if slot is None else int(labels[slot])

        for label, bit, is_self in hop_flags(label_of, range_edges, hops, period_chain):
            hop_bits[label].append(bit)
            if is_self:
                hop_self.add(label)

    cycles: list[CycleRecord] = []
    seq = 1
    for label in order:
        # A range node and its tree segments are hops, not cells:
        # A100 = SUM(A:A) is the cycle [A100].
        group = [name for name in members[label] if is_cell_id(name)]
        intra = intra_by.get(label, [])
        self_loop = any(edge.source == edge.target for edge in intra) or label in hop_self
        if not group or (len(group) < 2 and not self_loop):
            continue
        cls = _classify(intra, hop_bits.get(label, []))
        cycles.append(
            CycleRecord.model_validate(
                {"id": f"c{seq}", "members": sorted(group), "class": cls}
            )
        )
        seq += 1
    return cycles


def _classify(intra: list[CellEdge], hop_bits: list[bool] | None = None) -> str:
    bits = list(hop_bits or [])
    if not intra and not bits:
        return "unexpected"
    if all(_is_period_chain(edge) for edge in intra) and all(bits):
        return "iterative_ok"
    return "unexpected"


def _is_period_chain(edge: CellEdge) -> bool:
    """A non-zero period distance. Variants of one key and ``unaligned`` are not a shift."""
    lag = edge.period_lag
    if lag in {"same", "0", "unaligned"}:
        return False
    if lag not in (None, ""):
        try:
            return int(lag) != 0
        except (TypeError, ValueError):
            return False
    offset = edge.col_offset
    if offset in (None, 0):
        return False
    src = parse_node_id(str(edge.source or ""))
    tgt = parse_node_id(str(edge.target or ""))
    return src is not None and tgt is not None and src[0] == tgt[0] and src[2] == tgt[2]
