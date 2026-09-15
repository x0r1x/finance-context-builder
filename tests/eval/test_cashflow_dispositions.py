from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.stage import layout_workbook
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.eval import disposition_metrics
from finance_context.mapping.normalize import normalize_label, section_class
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.store.fs import read_parquet

XLSX = Path("/Users/alekseykashin/projects/finance-context-builder/resources/cashflow.xlsx")
GOLD = Path("tests/fixtures/mapping/cashflow_dispositions.yaml")


def _gold_rows() -> list[dict]:
    data = yaml.safe_load(GOLD.read_text(encoding="utf-8"))
    return list(data["expectations"])


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


@pytest.mark.skipif(not XLSX.is_file(), reason="cashflow.xlsx fixture missing")
def test_cashflow_unmapped_rows_follow_gold_dispositions(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    parse_workbook(XLSX, dest)
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
    gold = _gold_rows()
    expected_labels = {normalize_label(item["label"]) for item in gold}
    relevant = [row for row in doc.rows if normalize_label(row.label) in expected_labels]
    errors = []
    for row in relevant:
        exp = _match_expectation(row, gold)
        if exp is None:
            continue
        if row.concept_id != exp["concept_id"]:
            errors.append(
                (row.sheet, row.label, row.parent_label, row.concept_id, exp["concept_id"])
            )
    assert not errors, errors
    by_label = {row.label.casefold(): row.concept_id for row in doc.rows if row.concept_id}
    assert by_label.get("dscr (op cf / debt service") != "bs.debt"
    assert not any(
        "leverage" in row.label.casefold() and row.concept_id == "pnl.revenue" for row in doc.rows
    )
