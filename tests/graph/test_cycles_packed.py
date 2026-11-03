from __future__ import annotations

import array

from finance_context.graph.cycles import classify_cycles
from finance_context.graph.stage import _STRUCTURAL, _cycles_packed, _NodeIds, _period_chain


def _packed(edges: list[dict], structural: set[int] | None = None):
    ids = _NodeIds()
    src = array.array("i")
    tgt = array.array("i")
    flags = array.array("B")
    structural = structural or set()
    for index, edge in enumerate(edges):
        source = str(edge["source"])
        target = str(edge["target"])
        src.append(ids.nid(source))
        tgt.append(ids.nid(target))
        if index in structural:
            flags.append(_STRUCTURAL)
            continue
        bit = _period_chain(source, target, edge.get("period_lag"), edge.get("col_offset"))
        flags.append(1 if bit else 0)
    return _cycles_packed(ids.map, src, tgt, flags)


def _as_dicts(cycles) -> list[dict]:
    return [
        {"id": cycle.id, "members": list(cycle.members), "class": cycle.class_} for cycle in cycles
    ]


def test_packed_cycles_match_classify() -> None:
    cases = [
        [
            {
                "source": "S!A1",
                "target": "S!A1",
                "kind": "ref",
                "col_offset": 0,
                "period_lag": "same",
            }
        ],
        [
            {
                "source": "Sheet!A1",
                "target": "Sheet!B1",
                "kind": "ref",
                "col_offset": 1,
                "period_lag": "same",
            },
            {
                "source": "Sheet!B1",
                "target": "Sheet!A1",
                "kind": "ref",
                "col_offset": -1,
                "period_lag": "same",
            },
        ],
        [
            {
                "source": "CF!C2",
                "target": "CF!B2",
                "kind": "ref",
                "col_offset": -1,
                "period_lag": "-1",
            },
            {
                "source": "CF!B2",
                "target": "CF!C2",
                "kind": "ref",
                "col_offset": 1,
                "period_lag": "1",
            },
        ],
        [
            {"source": "S!A2", "target": "S!A3", "kind": "ref", "col_offset": 1, "period_lag": "1"},
            {"source": "S!A3", "target": "S!A4", "kind": "ref", "col_offset": 1, "period_lag": "1"},
        ],
    ]
    for edges in cases:
        assert _as_dicts(_packed(edges)) == _as_dicts(classify_cycles(edges))


def test_structural_edges_join_but_do_not_vote() -> None:
    edges = [
        {"source": "S!A1", "target": "S!B1", "kind": "ref", "period_lag": "-1", "col_offset": -1},
        {"source": "S!B1", "target": "S!A1", "kind": "ref", "period_lag": "1", "col_offset": 1},
    ]
    cycles = _packed(edges, structural={0, 1})
    assert len(cycles) == 1
    assert cycles[0].class_ == "unexpected"
    assert cycles[0].members == ["S!A1", "S!B1"]


def test_no_edges_and_empty_graph_have_no_cycles() -> None:
    assert _cycles_packed({}, array.array("i"), array.array("i"), array.array("B")) == []
    ids = _NodeIds()
    ids.nid("S!A1")
    ids.nid("S!B1")
    assert _cycles_packed(ids.map, array.array("i"), array.array("i"), array.array("B")) == []
