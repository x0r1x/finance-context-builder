from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from finance_context.mapping.glossary import STATEMENT_PAIRS
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.statement import is_cashflow_context
from finance_context.mapping.taxonomy import seed_authored_units

if TYPE_CHECKING:
    from finance_context.mapping.eval import QualityRow


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
    return (left, right) in STATEMENT_PAIRS or (right, left) in STATEMENT_PAIRS


def _twin_ids(concept_id: str) -> set[str]:
    found: set[str] = set()
    for left, right in STATEMENT_PAIRS:
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
