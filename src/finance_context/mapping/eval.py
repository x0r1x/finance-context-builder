from __future__ import annotations

from collections import Counter


def mapping_metrics(
    predicted: list[tuple[str, str | None, str | None]],
) -> dict[str, float | int]:
    """predicted items are (row_id, gold_concept, pred_concept)."""
    fact_n = len(predicted)
    mapped = [(gold, pred) for _, gold, pred in predicted if pred]
    coverage = len(mapped) / fact_n if fact_n else 0.0
    errors = sum(1 for gold, pred in mapped if gold and pred != gold)
    selective_risk = errors / len(mapped) if mapped else 0.0
    return {
        "n": fact_n,
        "mapped": len(mapped),
        "coverage": coverage,
        "selective_risk": selective_risk,
        "errors": errors,
    }


def signal_counts(sources: list[str]) -> dict[str, int]:
    return dict(Counter(sources))
