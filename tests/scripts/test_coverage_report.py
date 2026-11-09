"""The coverage report counts blocks and does not print row text."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "coverage_report.py"
_SPEC = importlib.util.spec_from_file_location("coverage_report", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
coverage_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(coverage_report)


def _doc(sheet: str, kind: str, rows: int, periods: int = 0) -> dict:
    return {
        "meta": {"status": "succeeded", "source_filename": "model.xlsx"},
        "workbook": {"sheets": [sheet, "Notes"]},
        "axes": [{"id": f"{sheet}!r1", "sheet": sheet, "periods": [{"period_key": "2024"}]}],
        "warnings": [f"Sheet {sheet}: {rows} rows included without a period axis ({kind})"],
        "blocks": [
            {
                "sheet": sheet,
                "kind": kind,
                "rows": [{"label": "SECRET-LABEL"} for _ in range(rows)],
                "periods": [{"period_key": f"c{index}"} for index in range(periods)],
                "axis_ids": [],
            }
        ],
    }


def test_report_counts_kinds_and_hides_labels(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    old = _doc("CA", "timeline", 0)
    old["blocks"] = [
        {
            "sheet": "CA",
            "kind": "timeline",
            "rows": [{"label": "SECRET-LABEL"}],
            "periods": [],
            "axis_ids": ["CA!r1"],
        }
    ]
    old["warnings"] = []
    (before / "context.json").write_text(json.dumps(old), encoding="utf-8")
    new = _doc("CA", "matrix", 4, periods=6)
    (after / "context.json").write_text(json.dumps(new), encoding="utf-8")
    text = coverage_report.render(
        coverage_report._load_tree(after),
        coverage_report._load_tree(before),
    )
    assert "SECRET-LABEL" not in text
    assert "matrix_rows=4" in text
    assert "coverage_notes 1" in text
    assert "TIMELINE_LOSS" in text
    assert "empty_sheets 1 -> 1" in text


def test_a_timeline_that_keeps_its_rows_is_not_a_loss(tmp_path: Path) -> None:
    doc = _doc("Output", "timeline", 3)
    doc["blocks"][0]["axis_ids"] = ["Output!r1"]
    doc["warnings"] = []
    assert coverage_report._timeline_loss(doc, doc) is False
