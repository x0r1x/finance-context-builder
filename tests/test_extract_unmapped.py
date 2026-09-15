from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract-unmapped.py"


def _run(input_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(input_path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_extracts_only_rows_without_concept_id(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mapping.json"
    rows = [
        {"row_key": "mapped", "label": "Cash", "concept_id": "bs.cash"},
        {"row_key": "unmapped", "label": "Прочее", "concept_id": None},
    ]
    mapping_path.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    result = _run(mapping_path)

    assert result.returncode == 0
    output = json.loads((tmp_path / "unmapped.json").read_text(encoding="utf-8"))
    assert output == {"count": 1, "rows": [rows[1]]}


def test_extracts_existing_unmapped_rows_from_context(tmp_path: Path) -> None:
    context_path = tmp_path / "context.json"
    rows = [
        {
            "row_key": "unmapped",
            "label": "Прочее",
            "concept_id": None,
            "disposition": "abstained",
            "source": {"sheet": "P&L", "addr": "A2"},
            "values": [{"cached_value": "1"}],
        }
    ]
    context_path.write_text(json.dumps({"unmapped": rows}), encoding="utf-8")

    result = _run(context_path)

    assert result.returncode == 0
    output = json.loads((tmp_path / "unmapped.json").read_text(encoding="utf-8"))
    assert output == {
        "count": 1,
        "rows": [
            {
                "row_key": "unmapped",
                "label": "Прочее",
                "concept_id": None,
                "disposition": "abstained",
                "source": {"sheet": "P&L", "addr": "A2"},
                "ref": "P&L!A2",
            }
        ],
    }


def test_skips_excluded_rows_in_context_unmapped(tmp_path: Path) -> None:
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "unmapped": [
                    {"label": "Tie-out", "concept_id": None, "disposition": "excluded"},
                    {"label": "Mystery", "concept_id": None, "disposition": "abstained"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run(context_path)

    assert result.returncode == 0
    output = json.loads((tmp_path / "unmapped.json").read_text(encoding="utf-8"))
    assert output["count"] == 1
    assert output["rows"][0]["label"] == "Mystery"


def test_writes_empty_result_to_requested_path(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mapping.json"
    output_path = tmp_path / "reports" / "review.json"
    mapping_path.write_text(
        json.dumps({"rows": [{"concept_id": "pnl.revenue"}]}),
        encoding="utf-8",
    )

    result = _run(mapping_path, "--output", str(output_path))

    assert result.returncode == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "count": 0,
        "rows": [],
    }


def test_rejects_invalid_json(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text("{not valid json", encoding="utf-8")

    result = _run(mapping_path)

    assert result.returncode == 2
    assert "error:" in result.stderr
    assert not (tmp_path / "unmapped.json").exists()
