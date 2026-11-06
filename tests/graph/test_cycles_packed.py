from __future__ import annotations

import array

from finance_context.formulas.csr import RangeNode
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.ranges import RangeHops, SegmentIds, hop_flags
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


def test_wire_matches_string_edges() -> None:
    cells = [f"S!A{row}" for row in range(1, 21)] + [f"S!B{row}" for row in range(1, 21)]
    hops = RangeHops(cells)
    nodes = [
        RangeNode("S!a", "S", 1, 2, 1, 10),
        RangeNode("S!b", "S", 2, 2, 5, 20),
    ]
    named = [edge for node in nodes for edge in hops.edges(node)]
    names: dict[str, int] = {}

    def nid(text: str) -> int:
        slot = names.get(text)
        if slot is None:
            slot = len(names)
            names[text] = slot
        return slot

    packed: list[tuple[int, int]] = []
    segments = SegmentIds(100_000)

    def add(parent: int, child: int) -> None:
        packed.append((parent, child))

    for node in nodes:
        hops.wire(node, nid, add, segments)
    reverse = {slot: name for name, slot in names.items()}
    segments.offer(lambda slot, name: reverse.__setitem__(slot, name))
    got = [(reverse[parent], reverse[child]) for parent, child in packed]
    assert sorted(got) == sorted(named)
    segments_named = {parent for parent, _child in named if str(parent).startswith("#seg|")}
    assert len(segments._slot) == len(segments_named)


def test_anonymous_hops_match_named_cycles() -> None:
    # ``#seg|Z|...`` sorts before ``M!A1``, while ``Z!A1`` sorts after it.
    # Cycle ids follow the segment name, so dropping those names would swap c1 and c2.
    cells = [f"Z!A{row}" for row in range(1, 31)] + ["M!A1", "M!B1"]
    node = RangeNode("Z!A1:A30", "Z", 1, 1, 1, 30)
    edges = [
        {
            "source": "Z!A1",
            "target": "Z!A1:A30",
            "kind": "range",
            "period_lag": "1",
            "col_offset": 0,
        },
        {"source": "Z!A10", "target": "Z!A1", "kind": "ref", "period_lag": "1", "col_offset": 0},
        {"source": "M!A1", "target": "M!B1", "kind": "ref", "period_lag": "1", "col_offset": 1},
        {"source": "M!B1", "target": "M!A1", "kind": "ref", "period_lag": "1", "col_offset": -1},
    ]
    users = [("Z!A1", node)]

    def chain(source: str, member: str) -> bool:
        return _period_chain(source, member, "1", 0)

    expected = classify_cycles(
        edges, hops=RangeHops(cells), range_edges=users, period_chain=chain
    )
    got = _wired(edges, cells, users, chain)
    assert _as_dicts(got) == _as_dicts(expected)
    assert got[0].members[0].startswith("Z!")
    assert [cycle.members for cycle in got][1] == ["M!A1", "M!B1"]


def _wired(edges, cells, users, period_chain):
    hops = RangeHops(cells)
    ids = _NodeIds()
    src = array.array("i")
    tgt = array.array("i")
    flags = array.array("B")
    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        src.append(ids.nid(source))
        tgt.append(ids.nid(target))
        if ":" in target:
            flags.append(_STRUCTURAL)
        else:
            bit = _period_chain(source, target, edge.get("period_lag"), edge.get("col_offset"))
            flags.append(1 if bit else 0)
    unique = {node.node_id: node for _source, node in users}
    segments = SegmentIds(len(ids.map) + len(cells) + len(unique) + 1)

    def add(parent: int, child: int) -> None:
        src.append(parent)
        tgt.append(child)
        flags.append(_STRUCTURAL)

    for node in unique.values():
        hops.wire(node, ids.nid, add, segments)

    def extra(label_of):
        return hop_flags(label_of, users, hops, period_chain)

    n_nodes = segments.n_nodes if segments.span else len(ids.map)
    return _cycles_packed(
        ids.map, src, tgt, flags, extra, n_nodes=n_nodes, order_names=segments.offer
    )
