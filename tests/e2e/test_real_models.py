from __future__ import annotations

from pathlib import Path

import pytest

from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings

_HERE = Path(__file__).resolve()
_PROJECT = _HERE.parents[2]
_AUDIT = _PROJECT.parent / "cashflow-audit"
CANDIDATES = [
    _PROJECT / "resources" / "cashflow.xlsx",
    _AUDIT / "resources" / "Примеры excel" / "sample_full_model.xlsx",
    _AUDIT / "resources" / "Примеры excel" / "sample_3stmt.xlsx",
]


@pytest.mark.e2e_models
@pytest.mark.parametrize("workbook", CANDIDATES)
def test_real_models_produce_context(tmp_path: Path, workbook: Path) -> None:
    if not workbook.is_file():
        pytest.skip(f"missing {workbook}")
    dest = tmp_path / workbook.stem
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(workbook.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None)
    doc = pipeline.run(dest, job_id=workbook.stem, source_filename=workbook.name)
    assert (dest / "raw" / "workbook.json").is_file()
    assert (dest / "context.json").is_file()
    assert (dest / "context.md").is_file()
    assert doc.workbook.cell_count > 0
    assert doc.meta.status in {"succeeded", "degraded", "needs_input"}
    traced = [
        value.source.cell_ref
        for block in doc.blocks
        for metric in block.metrics
        for value in metric.values
    ]
    traced.extend(series.source.cell_ref for series in doc.unmapped)
    assert all("!" in ref for ref in traced)
