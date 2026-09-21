from __future__ import annotations

from finance_context.graph.cycles import classify_cycles


def test_unexpected_two_cell_cycle() -> None:
    edges = [
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
    ]
    cycles = classify_cycles(edges)
    assert len(cycles) == 1
    assert cycles[0].class_ == "unexpected"
    assert set(cycles[0].members) == {"Sheet!A1", "Sheet!B1"}


def test_period_roll_forward_is_iterative() -> None:
    edges = [
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
    ]
    cycles = classify_cycles(edges)
    assert len(cycles) == 1
    assert cycles[0].class_ == "iterative_ok"
