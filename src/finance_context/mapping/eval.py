from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from finance_context.mapping.quality import (
    _formula_cells,
    _formula_ok,
    _label_supported,
    _semantic_failures,
    _temporal_ok,
    _unit_ok,
)
from finance_context.mapping.taxonomy import load_taxonomy


def content_completeness(layout_rows: int, inventory_rows: int) -> float:
    if layout_rows <= 0:
        return 1.0
    return inventory_rows / layout_rows


def concept_coverage(mapped: int, abstained: int) -> float:
    annotatable = mapped + abstained
    if annotatable <= 0:
        return 0.0
    return mapped / annotatable


def mapping_metrics(
    predicted: list[tuple[str, str | None, str | None]],
) -> dict[str, float | int]:
    """predicted items are (row_id, gold_concept, pred_concept)."""
    fact_n = len(predicted)
    mapped = [(gold, pred) for _, gold, pred in predicted if pred]
    abstained = fact_n - len(mapped)
    coverage = concept_coverage(len(mapped), abstained)
    errors = sum(1 for gold, pred in mapped if gold and pred != gold)
    selective_risk = errors / len(mapped) if mapped else 0.0
    return {
        "n": fact_n,
        "mapped": len(mapped),
        "coverage": coverage,
        "concept_coverage": coverage,
        "selective_risk": selective_risk,
        "errors": errors,
    }


def disposition_metrics(rows: list) -> dict[str, float | int]:
    n = len(rows)
    mapped = sum(1 for row in rows if getattr(row, "disposition", None) == "mapped")
    excluded = sum(1 for row in rows if getattr(row, "disposition", None) == "excluded")
    abstained = sum(1 for row in rows if getattr(row, "disposition", None) == "abstained")
    return {
        "n": n,
        "mapped": mapped,
        "excluded": excluded,
        "abstained": abstained,
        "processed_rate": (mapped + excluded + abstained) / n if n else 0.0,
        "concept_coverage": concept_coverage(mapped, abstained),
    }


def context_report_metrics(
    *,
    layout_rows: int,
    inventory_rows: int,
    mapped: int,
    abstained: int,
    excluded: int = 0,
    abstract: int = 0,
    unmapped_series: int = 0,
) -> dict[str, float | int]:
    return {
        "layout_rows": layout_rows,
        "inventory_rows": inventory_rows,
        "content_completeness": content_completeness(layout_rows, inventory_rows),
        "mapped": mapped,
        "abstained": abstained,
        "excluded": excluded,
        "abstract": abstract,
        "unmapped_series": unmapped_series,
        "concept_coverage": concept_coverage(mapped, abstained),
    }


def inventory_coverage_counts(rows: list) -> dict[str, int]:
    mapped = 0
    abstained = 0
    excluded = 0
    abstract = 0
    for row in rows:
        kind = getattr(row, "kind", None)
        disposition = getattr(row, "disposition", None)
        if kind == "abstract" or disposition == "header":
            abstract += 1
        if disposition == "mapped":
            mapped += 1
        elif disposition == "excluded":
            excluded += 1
        elif disposition == "abstained":
            abstained += 1
        elif getattr(row, "concept_id", None):
            mapped += 1
        elif kind in {None, "fact", "flag"}:
            abstained += 1
    return {
        "mapped": mapped,
        "abstained": abstained,
        "excluded": excluded,
        "abstract": abstract,
    }


def signal_counts(sources: list[str]) -> dict[str, int]:
    return dict(Counter(sources))


def abstain_rate(rows: list) -> float:
    n = len(rows)
    if not n:
        return 0.0
    abstained = sum(1 for row in rows if getattr(row, "disposition", None) == "abstained")
    return abstained / n


@dataclass
class QualityRow:
    """One accepted mapping scored by `mapping_quality_metrics`."""

    label: str
    concept_id: str
    block_id: str = ""
    hints: Any = None
    semantic_identity: Any = None
    cash_semantics: Any = None
    sheet: str = ""
    label_path: list[str] = field(default_factory=list)
    parent_label: str | None = None
    evidence: str | None = None
    score: float | None = None
    confidence: str | None = None
    formula_fingerprint: str | None = None
    values: list[Any] = field(default_factory=list)


def quality_rows_from_context(inventory: list, blocks: list | None = None) -> list[QualityRow]:
    """Mapped block rows. `inventory` is the same line list when blocks are omitted."""
    series_by_key: dict[str, Any] = {}
    block_of: dict[str, str] = {}
    for block in blocks or []:
        block_id = getattr(block, "block_id", "") or ""
        lines = list(getattr(block, "rows", None) or []) or list(
            getattr(block, "metrics", None) or []
        )
        for metric in lines:
            key = getattr(metric, "row_key", None)
            if key:
                series_by_key[key] = metric
                block_of[key] = block_id
    if inventory:
        source = [row for row in inventory if _is_mapped(row)]
    else:
        source = [row for row in series_by_key.values() if _is_mapped(row)]
    rows: list[QualityRow] = []
    for row in source:
        key = getattr(row, "row_key", None)
        series = series_by_key.get(key) if key else None
        mapping = getattr(series, "mapping", None) or getattr(row, "mapping", None)
        hints = getattr(row, "hints", None)
        if hints is None and series is not None:
            hints = getattr(series, "hints", None)
        identity = getattr(row, "semantic_identity", None)
        if identity is None and series is not None:
            identity = getattr(series, "semantic_identity", None)
        cash = getattr(row, "cash_semantics", None)
        if cash is None and series is not None:
            cash = getattr(series, "cash_semantics", None)
        values = list(getattr(series, "values", None) or getattr(row, "values", None) or [])
        fingerprint = getattr(row, "formula", None) or getattr(row, "formula_fingerprint", None)
        if not fingerprint and series is not None:
            fingerprint = getattr(series, "formula", None) or getattr(
                series, "formula_fingerprint", None
            )
        sheet = getattr(row, "sheet", None) or ""
        if not sheet and series is not None:
            sheet = getattr(series, "sheet", None) or getattr(
                getattr(series, "source", None), "sheet", ""
            ) or ""
        label_path = getattr(row, "label_path", None) or getattr(series, "label_path", None) or []
        parent = getattr(row, "parent_label", None) or getattr(series, "parent_label", None)
        if mapping is not None:
            evidence = getattr(mapping, "evidence", None)
            score = getattr(mapping, "score", None)
            confidence = getattr(mapping, "confidence", None)
        else:
            evidence = getattr(row, "evidence", None)
            score = getattr(row, "score", None)
            confidence = getattr(row, "confidence", None)
        rows.append(
            QualityRow(
                label=getattr(row, "label", "") or "",
                concept_id=getattr(row, "concept_id", None) or "",
                block_id=block_of.get(key or "", getattr(row, "block_id", "") or ""),
                hints=hints,
                semantic_identity=identity,
                cash_semantics=cash,
                sheet=sheet,
                label_path=list(label_path),
                parent_label=parent,
                evidence=evidence,
                score=score,
                confidence=confidence,
                formula_fingerprint=fingerprint,
                values=values,
            )
        )
    return rows


def mapping_quality_metrics(
    rows: list[QualityRow],
    *,
    concepts: list | None = None,
    concept_accept_min: float,
) -> dict[str, float | bool]:
    """Real checks on accepted concepts. Empty input scores 1.0 and passes the threshold."""
    catalog = {item.id: item for item in (concepts if concepts is not None else load_taxonomy())}
    mapped = [row for row in rows if row.concept_id]
    if not mapped:
        return {
            "label_coverage": 1.0,
            "semantic_coverage": 1.0,
            "unit_coverage": 1.0,
            "temporal_coverage": 1.0,
            "formula_coverage": 1.0,
            "confidence_threshold_passed": True,
        }
    semantic_fail = _semantic_failures(mapped)
    formula_rows = [row for row in mapped if _formula_cells(row)]
    return {
        "label_coverage": _rate(
            mapped, lambda row: _label_supported(row, catalog.get(row.concept_id), catalog)
        ),
        "semantic_coverage": _rate(mapped, lambda row: id(row) not in semantic_fail),
        "unit_coverage": _rate(mapped, lambda row: _unit_ok(row, catalog.get(row.concept_id))),
        "temporal_coverage": _rate(
            mapped, lambda row: _temporal_ok(row, catalog.get(row.concept_id))
        ),
        "formula_coverage": 1.0 if not formula_rows else _rate(formula_rows, _formula_ok),
        "confidence_threshold_passed": (
            not semantic_fail
            and all(_confidence_ok(row, concept_accept_min) for row in mapped)
        ),
    }


def assess_mapping_quality(
    inventory: list,
    blocks: list | None = None,
    *,
    concepts: list | None = None,
    concept_accept_min: float,
) -> dict[str, float | bool]:
    return mapping_quality_metrics(
        quality_rows_from_context(inventory, blocks),
        concepts=concepts,
        concept_accept_min=concept_accept_min,
    )


def _is_mapped(row: Any) -> bool:
    if not getattr(row, "concept_id", None):
        return False
    disposition = getattr(row, "disposition", None)
    return disposition not in {"excluded", "header", "abstained"}


def _rate(rows: list[QualityRow], predicate) -> float:
    if not rows:
        return 1.0
    return sum(1 for row in rows if predicate(row)) / len(rows)


def _confidence_ok(row: QualityRow, concept_accept_min: float) -> bool:
    return row.confidence == "high" and row.score is not None and row.score >= concept_accept_min


def risk_coverage_curve(
    items: list[tuple[str | None, str | None, float | None]],
) -> list[dict[str, float | int]]:
    """items are (gold_concept, pred_concept, score). Predictions ranked by score."""
    ranked = sorted(
        [(gold, pred, score or 0.0) for gold, pred, score in items if pred],
        key=lambda item: item[2],
        reverse=True,
    )
    curve: list[dict[str, float | int]] = []
    errors = 0
    for index, (gold, pred, _score) in enumerate(ranked, start=1):
        if gold and pred != gold:
            errors += 1
        curve.append(
            {
                "k": index,
                "coverage": index / len(items) if items else 0.0,
                "risk": errors / index,
            }
        )
    return curve
