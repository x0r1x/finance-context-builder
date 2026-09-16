from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.stage import layout_workbook
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.eval import abstain_rate, disposition_metrics
from finance_context.mapping.normalize import normalize_label, section_class
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.store.fs import read_parquet

CORPUS = Path("resources/corpus")
CASES = [
    (
        "packt-project-finance.xlsx",
        Path("tests/fixtures/mapping/packt_project_finance_dispositions.yaml"),
    ),
    (
        "three-statement.xlsx",
        Path("tests/fixtures/mapping/three_statement_dispositions.yaml"),
    ),
    (
        "rvi-project-finance.xlsx",
        Path("tests/fixtures/mapping/rvi_project_finance_dispositions.yaml"),
    ),
]


def _match_expectation(row, expectations: list[dict]) -> dict | None:
    label = normalize_label(row.label)
    parent = normalize_label(row.parent_label)
    klass = section_class(row.parent_label, sheet=row.sheet)
    labeled = [item for item in expectations if normalize_label(item["label"]) == label]
    if not labeled:
        return None
    if len(labeled) == 1:
        return labeled[0]
    for item in labeled:
        want_parent = normalize_label(item.get("parent") or "")
        if want_parent and (want_parent in parent or want_parent in klass):
            return item
    return labeled[0]


@pytest.mark.parametrize(("filename", "gold_path"), CASES)
def test_corpus_workbook_dispositions(tmp_path: Path, filename: str, gold_path: Path) -> None:
    xlsx = CORPUS / filename
    if not xlsx.is_file():
        pytest.skip(f"corpus workbook missing: {xlsx} (run scripts/fetch-corpus.py)")
    gold = yaml.safe_load(gold_path.read_text(encoding="utf-8"))
    expectations = list(gold.get("expectations") or [])
    dest = tmp_path / "job"
    dest.mkdir()
    parse_workbook(xlsx, dest)
    compile_workbook(dest)
    layout = layout_workbook(dest)
    cells = read_parquet(dest / "ir" / "cells.parquet")
    doc = map_layout(
        layout,
        taxonomy=load_taxonomy(),
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
    )
    stats = disposition_metrics(doc.rows)
    assert stats["processed_rate"] == 1.0
    assert 0.0 <= abstain_rate(doc.rows) <= 1.0
    if not expectations:
        pytest.skip("gold expectations not filled yet")
    errors = []
    expected_labels = {normalize_label(item["label"]) for item in expectations}
    for row in doc.rows:
        if normalize_label(row.label) not in expected_labels:
            continue
        exp = _match_expectation(row, expectations)
        if exp is None:
            continue
        if row.concept_id != exp["concept_id"]:
            errors.append((row.sheet, row.label, row.concept_id, exp["concept_id"]))
    assert not errors, errors
