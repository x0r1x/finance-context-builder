from finance_context.formulas.csr import CellEdge
from finance_context.formulas.models import Edge
from finance_context.graph.stage import (
    _bind_refs,
    _formulas_from_cells,
    _NodeIds,
    _share_ends,
    _StrPool,
)


def test_node_ids_share_one_object() -> None:
    pool = _StrPool()
    left = "".join(["S!A", "1"])
    right = "".join(["S!", "A1"])
    assert left is not right
    assert pool.share(left) is pool.share(right) is left

    cells = [{"sheet": "S", "row": 1, "col": 1, "addr": "A1", "formula_raw": "=B1"}]
    formulas = _formulas_from_cells(cells, {}, {}, pool.share)
    edge = Edge(kind="ref", source="".join(["S!", "A1"]), target="".join(["S!", "B1"]))
    assert edge.source is not next(iter(formulas))
    _share_ends([edge], pool.share)
    _bind_refs(formulas, [edge], pool.share)
    assert edge.source is next(iter(formulas))
    assert formulas["S!A1"].refs[0] is edge.target

    ids = _NodeIds(pool.share)
    ids.nid("".join(["S!", "A1"]))
    ids.nid("".join(["S!", "B1"]))
    assert next(iter(ids.map)) is edge.source
    assert list(ids.map)[1] is edge.target

    cell = CellEdge(
        source="".join(["S!", "A1"]),
        target="".join(["S!", "B1"]),
        kind="ref",
        unresolved=False,
        truncated=False,
        dangling=False,
        dangling_reason=None,
        status=None,
        reason=None,
        evidence=None,
        range_ref=None,
    )
    cell_edges = [cell]
    _share_ends(cell_edges, pool.share)
    assert cell_edges[0] is not cell
    assert cell_edges[0].source is edge.source
    assert cell_edges[0].target is edge.target
