from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from finance_context.mapping.glossary import _STATEMENT_PAIRS
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.resolver import ACCEPT_MIN
from finance_context.mapping.statement import is_cashflow_context
from finance_context.mapping.taxonomy import load_taxonomy, seed_authored_units

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
_STATEMENT_PREFIXES = {"pnl", "cf", "bs", "debt"}
_PURE_UNITS = frozenset({"rate", "ratio"})
_STOCK_TIME = {"stock", "bop", "eop", "instant"}


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
            mapped, lambda row: _label_supported(row, catalog.get(row.concept_id), catalog)
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


_STEM = {
    "operational": "operating",
    "operations": "operating",
    "expenditure": "expense",
    "expenditures": "expense",
    "expenses": "expense",
    "costs": "cost",
    "revenues": "revenue",
    "dividends": "dividend",
}
_ROLE_LABELS = {
    "total",
    "balance b/f",
    "balance c/f",
    "brought forward",
    "carried forward",
    "opening",
    "closing",
    "additions",
}


def _label_supported(row: QualityRow, concept: Any, catalog: dict[str, Any]) -> bool:
    label = (row.label or "").strip()
    if not label:
        return False
    evidence = row.evidence or ""
    if "label matches" in evidence:
        return True
    if concept is None:
        return False
    if _legacy_phrase_supported(label, concept):
        return True
    label_tokens = _support_tokens(label)
    if any(_phrase_contains(label_tokens, _support_tokens(phrase)) for phrase in _phrases(concept)):
        return True
    if _role_supported(label, evidence):
        return True
    concept_id = str(getattr(concept, "id", "") or "")
    if _names_statement_twin(label_tokens, row.label_path, concept_id, catalog):
        return True
    foreign = _foreign_standard_id(label_tokens, concept_id, catalog)
    if foreign and not _statement_pair(concept_id, foreign):
        return False
    return any(
        _heading_supports(_support_tokens(heading), concept)
        for heading in row.label_path
        if heading
    )


def _role_supported(label: str, evidence: str) -> bool:
    key = re.sub(r"\s+", " ", label.casefold().replace("&", " and ")).strip()
    if key not in _ROLE_LABELS:
        return False
    folded = evidence.casefold()
    if "opening/closing" in folded or "roll-forward" in folded or "total of section" in folded:
        return True
    return key == "total" and folded.startswith("sum of")


def _names_statement_twin(
    label_tokens: list[str],
    label_path: list[str],
    concept_id: str,
    catalog: dict[str, Any],
) -> bool:
    surfaces = [label_tokens]
    surfaces.extend(_support_tokens(heading) for heading in label_path if heading)
    for twin_id in _twin_ids(concept_id):
        twin = catalog.get(twin_id)
        if twin is None:
            continue
        names = [_support_tokens(phrase) for phrase in _phrases(twin)]
        if any(surface and surface in names for surface in surfaces):
            return True
    return False


def _heading_supports(heading: list[str], concept: Any) -> bool:
    for phrase in _phrases(concept):
        tokens = _support_tokens(phrase)
        if _phrase_contains(heading, tokens):
            return True
        if len(heading) >= 2 and _phrase_contains(tokens, heading):
            return True
    return False


def _foreign_standard_id(
    label_tokens: list[str], concept_id: str, catalog: dict[str, Any]
) -> str | None:
    if not label_tokens:
        return None
    for other_id, other in catalog.items():
        if other_id == concept_id:
            continue
        if any(_support_tokens(phrase) == label_tokens for phrase in _phrases(other)):
            return other_id
    return None


def _statement_pair(left: str, right: str) -> bool:
    return (left, right) in _STATEMENT_PAIRS or (right, left) in _STATEMENT_PAIRS


def _twin_ids(concept_id: str) -> set[str]:
    found: set[str] = set()
    for left, right in _STATEMENT_PAIRS:
        if concept_id == left:
            found.add(right)
        elif concept_id == right:
            found.add(left)
    return found


def _legacy_phrase_supported(label: str, concept: Any) -> bool:
    """`normalize_label` rewrites cashflows. That dictionary pass stays."""
    normalized = normalize_label(label)
    return any(_legacy_span(normalized, normalize_label(phrase)) for phrase in _phrases(concept))


def _legacy_span(label: str, phrase: str) -> bool:
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


def _phrases(concept: Any) -> list[str]:
    return [
        *getattr(concept, "labels", []),
        *getattr(concept, "aliases", []),
        *getattr(concept, "exact_labels", []),
    ]


def _support_tokens(text: str) -> list[str]:
    """Tokens for the label check. Parentheses stay words; memory normalization does not."""
    raw = str(text or "")
    raw = raw.replace("&", " and ").replace("−", " ").replace("–", " ").replace("+", " ")
    raw = raw.replace("/", " ")
    raw = re.sub(r"[()（）\[\]]", " ", raw)
    raw = re.sub(r"[^0-9a-zа-яё]+", " ", raw.casefold())
    return [_STEM.get(token, token) for token in raw.split()]


def _phrase_contains(label: list[str], phrase: list[str]) -> bool:
    if not phrase:
        return False
    if label == phrase:
        return True
    if len(phrase) == 1 and len(phrase[0]) < 3:
        return False
    width = len(phrase)
    if width > len(label):
        return False
    return any(label[index : index + width] == phrase for index in range(len(label) - width + 1))


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
        if _hint(row, "time_semantics") not in _STOCK_TIME and not _stock_movement(row):
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


_RATIO_TIME = {"stock", "instant", "bop", "eop", "flow", "rate"}
_INSTANT_TIME = {"instant", "stock", "bop", "eop", "rate"}


def _unit_ok(row: QualityRow, concept: Any) -> bool:
    actual = _hint(row, "unit") or getattr(row, "unit", None)
    expected = _expected_unit(row.concept_id, concept)
    if expected is None:
        return True
    if actual == "price" and expected == "money":
        return True
    if actual == "years" and expected == "count":
        return True
    if actual in _PURE_UNITS and expected in _PURE_UNITS:
        return True
    return bool(actual) and actual == expected


def _expected_unit(concept_id: str, concept: Any) -> str | None:
    authored = _authored_unit(concept_id, concept)
    if authored:
        return authored
    prefix = concept_id.split(".", 1)[0]
    if prefix in _STATEMENT_PREFIXES:
        return "money"
    return None


def _authored_unit(concept_id: str, concept: Any) -> str | None:
    """Unit written before enrich fills an empty unit with money.

    Packaged ids use the yaml and its prefix defaults. A minted id keeps money
    when its parent declares money or belongs to pnl, cf, bs, or debt.
    """
    seeds = seed_authored_units()
    if concept_id in seeds:
        return seeds[concept_id]
    stored = _facet_unit(concept)
    if stored != "money":
        return stored
    broader = getattr(concept, "broader", None) if concept is not None else None
    if not broader:
        return stored
    parent_id = str(broader)
    if parent_id not in seeds:
        return stored
    parent_unit = seeds[parent_id]
    if parent_unit == "money":
        return "money"
    if parent_unit is None and parent_id.split(".", 1)[0] in _STATEMENT_PREFIXES:
        return "money"
    return None


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
        if time_semantics not in _STOCK_TIME and not _stock_movement(row):
            return False
    if _period_type(concept) == "instant":
        return time_semantics in _INSTANT_TIME
    if _facet_unit(concept) == "ratio":
        return time_semantics in _RATIO_TIME
    if _is_rate_concept(row.concept_id, concept) and time_semantics != "rate":
        return False
    return True


def _stock_movement(row: QualityRow) -> bool:
    """A roll-forward line between b/f and c/f is the period change of a stock."""
    return _hint(row, "time_semantics") == "flow" and _hint(row, "nature") == "flow"


def _facet_unit(concept: Any) -> str | None:
    facets = getattr(concept, "facets", None) if concept is not None else None
    unit = getattr(facets, "unit", None)
    return str(unit) if unit else None


def _period_type(concept: Any) -> str | None:
    facets = getattr(concept, "facets", None) if concept is not None else None
    period = getattr(facets, "period_type", None)
    return str(period) if period else None


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
