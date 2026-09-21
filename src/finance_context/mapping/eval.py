from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.resolver import ACCEPT_MIN
from finance_context.mapping.statement import is_cashflow_context
from finance_context.mapping.taxonomy import load_taxonomy

_PNL_ON_CASHFLOW = {"pnl.revenue", "pnl.opex", "pnl.tax", "pnl.interest"}
_CASH_TWINS = {
    "cf.receipts": "pnl.revenue",
    "cf.opex_paid": "pnl.opex",
    "cf.tax_paid": "pnl.tax",
    "cf.interest_paid": "pnl.interest",
}
_ECONOMIC_LABELS = {
    "pnl.revenue": {"gross revenue", "gross revenues", "total revenue"},
    "pnl.opex": {"opex", "operating expenses", "total operating costs"},
    "pnl.tax": {"income tax"},
    "pnl.interest": {
        "interest",
        "interests",
        "interest expense",
        "loan interest",
        "capitalized interest",
        "capitalized interests",
    },
}
_FAMILY = {
    "pnl.revenue": "revenue",
    "cf.receipts": "revenue",
    "pnl.opex": "opex",
    "cf.opex_paid": "opex",
    "pnl.tax": "tax",
    "cf.tax_paid": "tax",
    "pnl.interest": "interest",
    "cf.interest_paid": "interest",
}
_DURATION_IDS = {
    "ops.lifetime",
    "ops.concession_duration",
    "ops.operating_period",
    "ops.construction_period",
}
_STOCK_TIME = {"stock", "bop", "eop"}


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
            mapped, lambda row: _label_supported(row, catalog.get(row.concept_id))
        ),
        "semantic_coverage": _rate(mapped, lambda row: id(row) not in semantic_fail),
        "unit_coverage": _rate(mapped, lambda row: _unit_ok(row, catalog.get(row.concept_id))),
        "temporal_coverage": _rate(
            mapped, lambda row: _temporal_ok(row, catalog.get(row.concept_id))
        ),
        "formula_coverage": 1.0 if not formula_rows else _rate(formula_rows, _formula_ok),
        "confidence_threshold_passed": (
            not semantic_fail and all(_confidence_ok(row) for row in mapped)
        ),
    }


def assess_mapping_quality(
    inventory: list,
    blocks: list | None = None,
    *,
    concepts: list | None = None,
) -> dict[str, float | bool]:
    return mapping_quality_metrics(
        quality_rows_from_context(inventory, blocks),
        concepts=concepts,
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


def _hint(row: QualityRow, name: str) -> Any:
    hints = row.hints
    if hints is None:
        return None
    if isinstance(hints, dict):
        return hints.get(name)
    return getattr(hints, name, None)


def _label_supported(row: QualityRow, concept: Any) -> bool:
    label = (row.label or "").strip()
    if not label:
        return False
    evidence = row.evidence or ""
    if "label matches" in evidence:
        return True
    if concept is None:
        return False
    normalized = normalize_label(label)
    phrases = [
        *getattr(concept, "labels", []),
        *getattr(concept, "aliases", []),
        *getattr(concept, "exact_labels", []),
    ]
    for phrase in phrases:
        if _phrase_matches(normalized, normalize_label(phrase)):
            return True
    return False


def _phrase_matches(label: str, phrase: str) -> bool:
    if not phrase:
        return False
    if label == phrase:
        return True
    phrase_tokens = phrase.split()
    label_tokens = label.split()
    if len(phrase_tokens) == 1 and len(phrase_tokens[0]) < 3:
        return False
    width = len(phrase_tokens)
    if width > len(label_tokens):
        return False
    return any(
        label_tokens[index : index + width] == phrase_tokens
        for index in range(len(label_tokens) - width + 1)
    )


def _semantic_failures(rows: list[QualityRow]) -> set[int]:
    failed = {id(row) for row in rows if not _semantic_ok(row, rows)}
    return failed | _family_collisions(rows)


def _semantic_ok(row: QualityRow, rows: list[QualityRow]) -> bool:
    if row.semantic_identity is None or row.cash_semantics is None:
        return False
    concept_id = row.concept_id
    statement = _hint(row, "statement")
    if concept_id in _PNL_ON_CASHFLOW and (
        statement == "cf" or _row_is_cashflow(row)
    ):
        return False
    expected = _expected_economic_identity(row.label, concept_id)
    if expected and getattr(row.semantic_identity, "concept_id", None) != expected:
        return False
    if concept_id in _CASH_TWINS and row.cash_semantics.recognition != "cash":
        return False
    if concept_id.startswith("bs."):
        if row.cash_semantics.recognition != "stock":
            return False
        if _hint(row, "time_semantics") not in _STOCK_TIME:
            return False
    if _is_capitalized(row.label) and row.cash_semantics.recognition != "noncash":
        return False
    if concept_id == "cf.repayment" or concept_id.startswith("cf.repayment."):
        if row.cash_semantics.cash_movement != "outflow":
            return False
        if _hint(row, "sign") != "outflow":
            return False
        if not _block_has_debt_stock(row, rows):
            return False
    return True


def _family_collisions(rows: list[QualityRow]) -> set[int]:
    grouped: dict[str, list[QualityRow]] = {}
    for row in rows:
        if row.semantic_identity is None or row.cash_semantics is None:
            continue
        grouped.setdefault(_family_of(row), []).append(row)
    collided: set[int] = set()
    for members in grouped.values():
        accrual = {row.concept_id for row in members if row.cash_semantics.recognition == "accrual"}
        cash = {row.concept_id for row in members if row.cash_semantics.recognition == "cash"}
        overlap = accrual & cash
        if not overlap:
            continue
        for row in members:
            if row.concept_id in overlap and row.cash_semantics.recognition in {"accrual", "cash"}:
                collided.add(id(row))
    return collided


def _family_of(row: QualityRow) -> str:
    family = getattr(row.semantic_identity, "family", None)
    if family:
        return family
    return _FAMILY.get(row.concept_id, row.concept_id)


def _expected_economic_identity(label: str, concept_id: str) -> str | None:
    economic = _CASH_TWINS.get(concept_id)
    if economic and normalize_label(label) in _ECONOMIC_LABELS.get(economic, set()):
        return economic
    return None


def _row_is_cashflow(row: QualityRow) -> bool:
    return is_cashflow_context(
        sheet=row.sheet,
        section_path=row.label_path,
        parent=row.parent_label,
        label=row.label,
    )


def _is_capitalized(label: str) -> bool:
    normalized = normalize_label(label)
    return "capitalized" in normalized or "capitalised" in normalized


def _block_has_debt_stock(row: QualityRow, rows: list[QualityRow]) -> bool:
    for other in rows:
        if other is row or other.block_id != row.block_id:
            continue
        if other.concept_id == "bs.debt" or other.concept_id.startswith("bs.debt."):
            return True
    return False


def _unit_ok(row: QualityRow, concept: Any) -> bool:
    actual = _hint(row, "unit") or getattr(row, "unit", None)
    expected = _expected_unit(row.concept_id, concept)
    return bool(actual) and actual == expected


def _expected_unit(concept_id: str, concept: Any) -> str:
    if concept_id in _DURATION_IDS:
        return "years"
    if concept_id.endswith("_rate"):
        return "rate"
    facets = getattr(concept, "facets", None) if concept is not None else None
    facet_unit = getattr(facets, "unit", None)
    if facet_unit:
        return facet_unit
    prefix = concept_id.split(".", 1)[0]
    if prefix in {"pnl", "cf", "bs", "debt"}:
        return "money"
    return "money"


def _temporal_ok(row: QualityRow, concept: Any) -> bool:
    time_semantics = _hint(row, "time_semantics")
    if not time_semantics:
        return False
    tokens = set(normalize_label(row.label).split())
    blob = normalize_label(row.label)
    if "opening" in tokens or "b/f" in blob or "brought forward" in blob:
        if time_semantics != "bop":
            return False
    if "closing" in tokens or "c/f" in blob or "carried forward" in blob:
        if time_semantics != "eop":
            return False
    nature = _hint(row, "nature")
    if row.concept_id.startswith("bs.") or nature == "balance":
        if time_semantics not in _STOCK_TIME:
            return False
    if _is_rate_concept(row.concept_id, concept) and time_semantics != "rate":
        return False
    return True


def _is_rate_concept(concept_id: str, concept: Any) -> bool:
    if concept_id.endswith("_rate"):
        return True
    facets = getattr(concept, "facets", None) if concept is not None else None
    return getattr(facets, "unit", None) in {"rate", "ratio"}


def _formula_cells(row: QualityRow) -> list[Any]:
    marked = [value for value in row.values if getattr(value, "has_formula", False)]
    if marked:
        return marked
    if row.formula_fingerprint and row.values and all(
        not hasattr(value, "has_formula") for value in row.values
    ):
        return [row.formula_fingerprint]
    return []


def _formula_ok(row: QualityRow) -> bool:
    cells = _formula_cells(row)
    if not row.formula_fingerprint or not cells:
        return False
    if all(isinstance(value, str) for value in cells):
        return True
    return all(getattr(value, "formula", None) for value in cells)


def _confidence_ok(row: QualityRow) -> bool:
    return row.confidence == "high" and row.score is not None and row.score >= ACCEPT_MIN


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
