from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finance_context.context.build import build_context
from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.models import Layout
from finance_context.layout.stage import layout_workbook
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.eval import (
    abstain_rate,
    content_completeness,
    disposition_metrics,
)
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
    parented = []
    unparented = []
    for item in labeled:
        want_parent = normalize_label(item.get("parent") or "")
        if want_parent:
            parented.append(item)
        else:
            unparented.append(item)
    scored: list[tuple[int, dict]] = []
    for item in parented:
        want_parent = normalize_label(item.get("parent") or "")
        if not want_parent:
            continue
        if parent == want_parent or want_parent == klass:
            scored.append((len(want_parent), item))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    if unparented:
        return unparented[0]
    return None


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
        ia = next((sheet for sheet in layout.sheets if sheet.name == "Input Assumptions"), None)
        assert ia is not None and ia.blocks
        assert all(block.kind == "params" for block in ia.blocks)
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
        assert not any(
            sheet.name == "Top Shortcuts" and sheet.blocks for sheet in layout.sheets
        )
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
    edges_path = dest / "ir" / "edges.parquet"
    edges = read_parquet(edges_path) if edges_path.is_file() else []
    ctx_doc = build_context(
        job_id="eval",
        workbook_meta={"sheets": [{"name": sheet.name} for sheet in layout.sheets]},
        cells=cells,
        layout=layout,
        mapping=doc,
        edges=edges,
    )
    layout_n = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
    assert len(ctx_doc.inventory) == layout_n
    assert content_completeness(layout_n, len(ctx_doc.inventory)) == 1.0
    assert 0.0 <= stats["concept_coverage"] <= 1.0
    series_n = sum(len(block.metrics) for block in ctx_doc.blocks) + len(ctx_doc.unmapped) + len(
        ctx_doc.excluded
    )
    assert series_n <= layout_n
    if filename == "packt-project-finance.xlsx":
        ia = [row for row in ctx_doc.inventory if row.sheet == "Input Assumptions"]
        assert ia, "Input Assumptions must appear in inventory"
        labels = {normalize_label(row.label): row for row in ia}
        assert "concession duration" in labels
        assert labels["concession duration"].kind == "fact"
        header = labels.get("traffic and revenue assumptions")
        if header is not None:
            assert header.kind == "abstract"
        costs = labels.get("costs during construction")
        if costs is not None:
            assert costs.kind == "abstract"
        assert "tax rate" in labels
        assert labels["tax rate"].unit == "rate"
        selector = labels.get("scenario chosen")
        assert selector is not None
        assert selector.kind == "flag"
        assert selector.row == 3
        assert selector.context_role == "scenario_selector"
        assert selector.disposition == "excluded"
        assert any(cell.role == "value" and str(cell.cached_value) == "1" for cell in selector.cells)
        assert labels["concession duration"].unit == "count"
        toll = labels.get("toll rate")
        if toll is not None:
            assert toll.unit == "money"
        stub = next(
            (
                cell
                for row in ctx_doc.inventory
                if row.sheet == "Construction" and row.row == 22
                for cell in row.cells
                if cell.role == "value"
            ),
            None,
        )
        assert stub is not None
        total = next(
            (
                cell
                for row in ctx_doc.inventory
                if row.sheet == "Construction" and row.row == 16
                for cell in row.cells
                if cell.role == "total"
            ),
            None,
        )
        assert total is not None
        assert ctx_doc.timeline is not None
        by_id = {item.period_id: item for item in ctx_doc.timeline.periods}
        assert by_id["Y1"].phase == "construction" and by_id["Y1"].phase_year == 1
        assert by_id["Y4"].phase == "construction" and by_id["Y4"].phase_year == 4
        assert by_id["Y5"].phase == "operation" and by_id["Y5"].phase_year == 1
        construction = next(block for block in ctx_doc.blocks if block.sheet == "Construction")
        y5 = next(item for item in construction.periods if item["period_key"] == "Y5")
        assert y5["phase"] == "operation" and y5["phase_year"] == 1
        flag_mapped = [row for row in doc.rows if row.exclusion_reason == "flag"]
        assert flag_mapped
        assert all(row.concept_id is None for row in flag_mapped)
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
        if "concept_id" in exp and row.concept_id != exp["concept_id"]:
            errors.append((row.sheet, row.label, row.concept_id, exp["concept_id"]))
        forbidden = exp.get("forbidden_concept_id")
        if forbidden and row.concept_id == forbidden:
            errors.append((row.sheet, row.label, row.concept_id, f"!={forbidden}"))
    for row in doc.rows:
        if row.disposition == "excluded":
            continue
        if "available for equity" in normalize_label(row.label) or "fcfe" in normalize_label(
            row.label
        ):
            assert row.concept_id != "bs.equity", row.label
    assert not errors, errors
