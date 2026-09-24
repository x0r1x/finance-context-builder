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
    headers = block.axis.headers if block.axis is not None else []
    return sum(
        1
        for header in headers
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
        assert any(
            "construction" == label or "construction" in label for label in flag_labels
        ) or any("beginning of construction" in label for label in flag_labels)
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
        model = next(sheet for sheet in layout.sheets if sheet.name == "PF Model")
        assert [axis.id for axis in model.axes] == ["PF Model!r7"]
        keys = [period.period_key for period in model.axes[0].periods]
        assert keys[0] == "2024" and keys[-1] == "2062" and len(keys) == 39
        timelines = [block for block in model.blocks if block.kind == "timeline"]
        assert all(block.axis_ids == ["PF Model!r7"] for block in timelines)
        scenario = next(block for block in model.blocks if block.block_id == "PF Model!r49")
        assert scenario.kind == "params"
        cases = [header.text for header in scenario.axis.headers if header.role == "scenario"]
        assert cases == [f"Case {n}" for n in range(1, 11)]


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
    context_rows = [row for block in ctx_doc.blocks for row in block.rows]
    assert len(context_rows) == layout_n
    assert content_completeness(layout_n, len(context_rows)) == 1.0
    assert 0.0 <= stats["concept_coverage"] <= 1.0
    if filename == "packt-project-finance.xlsx":
        ia = [row for row in context_rows if row.sheet == "Input Assumptions"]
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
        assert any(
            cell.role == "value" and str(cell.cached_value) == "1" for cell in selector.cells
        )
        assert labels["concession duration"].unit == "years"
        toll = labels.get("toll rate")
        if toll is not None:
            assert toll.unit == "money"
            assert toll.hints.currency == "GBP"
        maint = labels.get("maintenance including heavy maintenance and spv costs")
        if maint is None:
            maint = next(
                (
                    row
                    for row in ia
                    if "maintenance" in normalize_label(row.label)
                    and "heavy" in normalize_label(row.label)
                ),
                None,
            )
        if maint is not None:
            assert maint.unit == "money"
            assert maint.hints.currency == "GBP"
        stub = next(
            (
                cell
                for row in context_rows
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
                for row in context_rows
                if row.sheet == "Construction" and row.row == 16
                for cell in row.cells
                if cell.role == "total"
            ),
            None,
        )
        assert total is not None
        phased = next(
            axis
            for axis in ctx_doc.axes
            if any(item.phase == "construction" for item in axis.periods)
        )
        by_id = {item.period_key: item for item in phased.periods}
        assert by_id["Y1"].phase == "construction" and by_id["Y1"].phase_year == 1
        assert by_id["Y4"].phase == "construction" and by_id["Y4"].phase_year == 4
        assert by_id["Y5"].phase == "operation" and by_id["Y5"].phase_year == 1
        construction = next(block for block in ctx_doc.blocks if block.sheet == "Construction")
        local_axis = next(axis for axis in ctx_doc.axes if axis.id == "Construction!r2")
        assert local_axis.timeline_id == phased.id
        assert construction.axis_ids == [local_axis.id]
        assert construction.periods
        assert construction.periods[0]["period_key"] == "Y1"
        assert construction.periods[0]["col"] == 5
        flag_mapped = [row for row in doc.rows if row.exclusion_reason == "flag"]
        assert flag_mapped
        assert all(row.concept_id is None for row in flag_mapped)
        quality = ctx_doc.mapping_stats.mapping_quality
        for rate in (
            quality.label_coverage,
            quality.semantic_coverage,
            quality.unit_coverage,
            quality.temporal_coverage,
            quality.formula_coverage,
        ):
            assert 0.0 <= rate <= 1.0
        repayments = [
            row
            for row in context_rows
            if normalize_label(row.label) == "principal repayment"
            and normalize_label(row.parent_label or "") == "debt repayment schedule"
        ]
        assert repayments
        assert all(
            row.hints.sign == "outflow" and row.concept_id == "cf.repayment" for row in repayments
        )
        for row in repayments:
            assert any(
                other.concept_id == "bs.debt" and other.label_path == row.label_path
                for other in context_rows
            )
        revenues = [
            row
            for row in context_rows
            if row.sheet == "CFS" and row.label == "Gross Revenues"
        ]
        assert revenues
        assert all(row.concept_id == "cf.receipts" for row in revenues)
        totals = [
            row
            for row in context_rows
            if row.sheet == "Balance Sheet" and row.label == "Total"
        ]
        assert len(totals) == 2
        assert all(row.disposition == "abstained" and row.concept_id is None for row in totals)
        check = next(
            row for row in context_rows if row.sheet == "Balance Sheet" and row.label == "CHECK"
        )
        assert check.kind == "helper"
        assert check.disposition == "excluded"
        assert check.formula
        assert ctx_doc.mapping_stats.concept_coverage == 94 / 96
        assert quality.semantic_coverage < 1.0
        assert quality.confidence_threshold_passed is False
    if filename == "rvi-project-finance.xlsx":
        axis = ctx_doc.axes[0]
        phases = [period.phase for period in axis.periods]
        assert phases.count("construction") == 2
        assert phases.count("operation") == 30
        assert axis.periods[0].start_date == "2024-01-01"
        assert axis.periods[-1].end_date == "2062-12-31"
        assert all(period.role == "forecast" for period in axis.periods)
        model_rows = [row for row in context_rows if row.sheet == "PF Model"]
        thousand = next(row for row in model_rows if row.label == "Thousand")
        assert thousand.hints.scale is None
        assert "1000" in (thousand.values or [])
        assert "1000" in (thousand.normalized_values or [])
        assert "1000000" not in (thousand.normalized_values or [])
        cpi_rate = next(row for row in model_rows if row.row == 165 and row.label == "CPI")
        cpi_index = next(row for row in model_rows if row.row == 172 and row.label == "CPI")
        assert cpi_rate.concept_id == "ops.inflation"
        assert cpi_index.concept_id == "ops.cpi"
        assert cpi_index.hints.time_semantics == "stock"
        current_total = next(
            row
            for row in model_rows
            if row.label == "Total" and row.parent_label == "Current assets"
        )
        assert current_total.concept_id == "bs.assets_current"
        principal = next(
            row
            for row in model_rows
            if row.label == "Principal repayment" and row.parent_label == "Linear repayment"
        )
        assert principal.concept_id == "cf.repayment"
        for label in (
            "Commercial Management",
            "O&M period 1",
            "O&M period 2",
            "O&M period 3",
            "Technical Management",
            "Balancing costs",
        ):
            cash_rows = [
                row
                for row in model_rows
                if row.label == label
                and (
                    row.parent_label == "Cashflow Statement"
                    or "Cashflow Statement" in (row.label_path or [])
                )
            ]
            assert cash_rows, label
            assert all(row.concept_id == "cf.opex_paid" for row in cash_rows)
        flags = {name for period in axis.periods for name in period.flags}
        assert not flags & {"live case", "case number", "mid case", "low case"}
        assert ctx_doc.mapping_stats.mapping_quality.unit_coverage >= 0.8
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
