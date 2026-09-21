from __future__ import annotations

from collections import Counter


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
