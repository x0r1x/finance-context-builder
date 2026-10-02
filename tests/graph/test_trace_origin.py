"""Graph walk starts from a cell, a row_key, or the row's own label."""

from __future__ import annotations

import json
from pathlib import Path

from finance_context.graph.stage import INDEX_COLUMNS
from finance_context.graph.trace import trace_graph
from finance_context.store.fs import write_cells_parquet, write_parquet

_PROJECT = "Ratios|6"
_EQUITY = "Ratios|19"
_CAPEX = "PF Model|10"
_EBITDA = "PF Model|30"
_OTHER = "PF Model|1"


def _index_row(
    node_id: str,
    sheet: str,
    addr: str,
    row_key: str,
    concept_id: str | None,
) -> tuple:
    return (node_id, sheet, addr, row_key, concept_id, "Y1", "formula")


def _write_index(dest: Path) -> None:
    write_cells_parquet(dest / "ir" / "cells.parquet", [])
    write_parquet(
        dest / "ir" / "graph_index.parquet",
        INDEX_COLUMNS,
        [
            _index_row("Ratios!F6", "Ratios", "F6", _PROJECT, "val.irr"),
            _index_row("Ratios!G6", "Ratios", "G6", _PROJECT, "val.irr"),
            _index_row("Ratios!F19", "Ratios", "F19", _EQUITY, "val.irr"),
            _index_row("PF Model!F10", "PF Model", "F10", _CAPEX, "val.irr"),
            _index_row("PF Model!F30", "PF Model", "F30", _EBITDA, None),
            _index_row("PF Model!Z1", "PF Model", "Z1", _OTHER, "pnl.other"),
        ],
    )


def _write_context(dest: Path, payload: object | None = None) -> None:
    if payload is None:
        payload = {
            "blocks": [
                {
                    "rows": [
                        {"row_key": _PROJECT, "label": "Project IRR"},
                        {"row_key": _EQUITY, "label": "Equity IRR"},
                        {
                            "row_key": _CAPEX,
                            "label": "CAPEX",
                            "label_path": ["Project IRR"],
                        },
                        {"row_key": _EBITDA, "label": "EBITDA"},
                        {"row_key": _OTHER, "label": "Other income"},
                    ]
                }
            ]
        }
    (dest / "context.json").write_text(json.dumps(payload), encoding="utf-8")


def _ids(dest: Path, origin: str) -> set[str]:
    traced = trace_graph(dest, origin=origin, direction="precedents", depth=1)
    return {node.node_id for node in traced.nodes}


def test_address_and_row_key_resolve_without_context(tmp_path: Path) -> None:
    _write_index(tmp_path)
    assert _ids(tmp_path, "PF Model!Z1") == {"PF Model!Z1"}
    assert _ids(tmp_path, _PROJECT) == {"Ratios!F6", "Ratios!G6"}


def test_label_starts_project_and_equity_irr_and_skips_capex(tmp_path: Path) -> None:
    _write_index(tmp_path)
    _write_context(tmp_path)
    traced = trace_graph(tmp_path, origin="IRR", direction="precedents", depth=1)
    assert {node.node_id for node in traced.nodes} == {"Ratios!F6", "Ratios!G6", "Ratios!F19"}
    assert {node.concept_id for node in traced.nodes} == {"val.irr"}
    assert "PF Model!F10" not in {node.node_id for node in traced.nodes}


def test_concept_id_does_not_start_the_walk(tmp_path: Path) -> None:
    _write_index(tmp_path)
    _write_context(tmp_path)
    traced = trace_graph(tmp_path, origin="val.irr", direction="precedents", depth=1)
    assert traced.nodes == []
    assert traced.stopped == "empty"


def test_empty_concept_starts_when_the_label_matches(tmp_path: Path) -> None:
    _write_index(tmp_path)
    _write_context(tmp_path)
    traced = trace_graph(tmp_path, origin="EBITDA", direction="precedents", depth=1)
    assert [node.node_id for node in traced.nodes] == ["PF Model!F30"]
    assert traced.nodes[0].concept_id is None
    assert traced.nodes[0].row_key == _EBITDA


def test_bad_context_keeps_address_and_row_key(tmp_path: Path) -> None:
    _write_index(tmp_path)
    assert _ids(tmp_path, "IRR") == set()
    for payload in ("not json", "[]", "{}", '{"blocks": "nope"}'):
        (tmp_path / "context.json").write_text(payload, encoding="utf-8")
        assert _ids(tmp_path, "IRR") == set()
        assert _ids(tmp_path, "PF Model!Z1") == {"PF Model!Z1"}
        assert _ids(tmp_path, _PROJECT) == {"Ratios!F6", "Ratios!G6"}
