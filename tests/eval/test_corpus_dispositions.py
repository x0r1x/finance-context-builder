from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.models import Layout
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


def _period_count(block) -> int:
    return sum(
        1
        for header in block.axis.headers
        if header.role in {"historical", "forecast", "stub", "relative"}
        and header.period_key not in {"actual", "plan", "total", "stub"}
    )


def _assert_layout_geometry(filename: str, layout: Layout) -> None:
    blocks = [block for sheet in layout.sheets for block in sheet.blocks]
    facts = [
        row
        for sheet in layout.sheets
        for block in sheet.blocks
        for row in block.rows
        if row.kind == "fact"
    ]
    flags = [
        row
        for sheet in layout.sheets
        for block in sheet.blocks
        for row in block.rows
        if row.kind == "flag"
    ]
    if filename == "packt-project-finance.xlsx":
        assert any(_period_count(block) >= 3 for block in blocks), (
            f"{filename}: expected a block with >=3 periods, got "
            f"{[_period_count(b) for b in blocks]}"
        )
        assert facts, f"{filename}: layout produced no fact rows"
        flag_labels = {normalize_label(row.label) for row in flags}
        assert any("construction" == label or "construction" in label for label in flag_labels) or any(
            "beginning of construction" in label for label in flag_labels
        )
        return
    if filename == "rvi-project-finance.xlsx":
        short = [block.block_id for block in blocks if _period_count(block) == 2]
        assert len(short) <= 2, (
            f"{filename}: leftover 2-period start/end blocks: {short[:12]}"
        )
        labels = {normalize_label(row.label) for row in facts}
        for needle in ("cfads", "total revenue"):
            assert any(needle in label for label in labels), (
                f"{filename}: no fact label containing {needle!r}"
            )


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
    _assert_layout_geometry(filename, layout)
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
    mapped_labels = {normalize_label(row.label) for row in doc.rows}
    missing = sorted(expected_labels - mapped_labels)
    assert not missing, f"{filename}: gold labels missing from mapped fact rows: {missing}"
    for row in doc.rows:
        if normalize_label(row.label) not in expected_labels:
            continue
        exp = _match_expectation(row, expectations)
        if exp is None:
            continue
        if row.concept_id != exp["concept_id"]:
            errors.append((row.sheet, row.label, row.concept_id, exp["concept_id"]))
    for row in doc.rows:
        if row.disposition == "excluded":
            continue
        if "available for equity" in normalize_label(row.label) or "fcfe" in normalize_label(
            row.label
        ):
            assert row.concept_id != "bs.equity", row.label
