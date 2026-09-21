"""Split a row into economic identity, reporting roles, and cash semantics.

`concept_id` stays the selected reporting concept (calculations and gold dispositions).
It does not by itself say what the line means, where else it is used, or whether the
amount is accrued, capitalized, or paid.
"""

from __future__ import annotations

from finance_context.mapping.normalize import normalize_label
from finance_context.models.context import CashSemantics, ReportingRole, SemanticIdentity

_FAMILY = {
    "pnl.revenue": "revenue",
    "cf.receipts": "revenue",
    "pnl.opex": "opex",
    "cf.opex_paid": "opex",
    "pnl.tax": "tax",
    "cf.tax_paid": "tax",
    "pnl.interest": "interest",
    "cf.interest_paid": "interest",
    "pnl.interest_rate": "interest_rate",
    "bs.equity": "equity",
    "cf.equity_issue": "equity",
    "cf.sources": "sources",
    "cf.uses": "uses",
    "cf.capex": "capex",
    "cf.cfads": "cfads",
    "debt.arrangement_fee": "arrangement_fee",
    "debt.arrangement_fee_rate": "arrangement_fee",
    "ops.inflation": "inflation",
    "ops.inflation_revenue": "inflation",
    "ops.inflation_cost": "inflation",
}

# Cash-statement projection → accrual concept, used only when the label names that concept.
_ECONOMIC = {
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

_RATE_PREFIXES = ("ops.inflation",)
_OUTFLOW = {
    "cf.capex",
    "cf.uses",
    "cf.disbursements",
    "cf.interest_paid",
    "cf.opex_paid",
    "cf.tax_paid",
    "cf.repayment",
    "debt.arrangement_fee",
    "pnl.interest",
    "pnl.opex",
    "pnl.tax",
}
_INFLOW = {"cf.receipts", "cf.sources", "cf.equity_issue", "cf.drawdown", "pnl.revenue"}
_LAYOUT_ROLE_CONCEPTS = {"cf.uses", "cf.sources"}


def classify_semantics(
    *,
    label: str,
    concept_id: str | None,
    score: float | None,
    alternatives: list[tuple[str, float]] | None = None,
    context_role: str | None = None,
    secondary_concepts: list[str] | None = None,
) -> tuple[SemanticIdentity | None, list[ReportingRole], CashSemantics | None]:
    if not concept_id:
        return None, [], None
    pairs = list(alternatives or [])
    identity_id, confidence = _identity(
        label=label,
        concept_id=concept_id,
        score=score,
        alternatives=pairs,
        context_role=context_role,
    )
    return (
        SemanticIdentity(
            family=_family(identity_id),
            concept_id=identity_id,
            confidence=confidence,
        ),
        _roles(
            concept_id=concept_id,
            score=score,
            context_role=context_role,
            secondary_concepts=secondary_concepts or [],
            alternatives=pairs,
        ),
        _cash(label=label, concept_id=concept_id, context_role=context_role),
    )


def _family(concept_id: str) -> str:
    if concept_id in _FAMILY:
        return _FAMILY[concept_id]
    return concept_id.split(".", 1)[-1]


def _identity(
    *,
    label: str,
    concept_id: str,
    score: float | None,
    alternatives: list[tuple[str, float]],
    context_role: str | None,
) -> tuple[str, float]:
    selected = score if score is not None else 1.0
    if context_role == "sources" and _family(concept_id) == "equity":
        return "cf.equity_issue", _score_of("cf.equity_issue", alternatives, selected)
    economic = _ECONOMIC.get(concept_id)
    if economic and _names(label, economic):
        return economic, _score_of(economic, alternatives, 1.0)
    return concept_id, selected


def _names(label: str, concept_id: str) -> bool:
    return normalize_label(label) in _ECONOMIC_LABELS.get(concept_id, set())


def _score_of(concept_id: str, alternatives: list[tuple[str, float]], default: float) -> float:
    for candidate_id, score in alternatives:
        if candidate_id == concept_id:
            return score
    return default


def _roles(
    *,
    concept_id: str,
    score: float | None,
    context_role: str | None,
    secondary_concepts: list[str],
    alternatives: list[tuple[str, float]],
) -> list[ReportingRole]:
    roles: list[ReportingRole] = []
    seen: set[str] = set()

    def add(role: str | None, confidence: float, *, selected: bool = False) -> None:
        if not role or role in seen or role == "line":
            return
        seen.add(role)
        roles.append(ReportingRole(role=role, confidence=confidence, selected=selected))

    add(concept_id, score if score is not None else 1.0, selected=True)
    add(context_role, 0.9)
    for concept in secondary_concepts:
        if concept in _LAYOUT_ROLE_CONCEPTS:
            add(concept, 0.86)
    for candidate_id, candidate_score in alternatives:
        if candidate_id in _LAYOUT_ROLE_CONCEPTS:
            add(candidate_id, candidate_score)
    return roles


def _cash(*, label: str, concept_id: str, context_role: str | None) -> CashSemantics:
    normalized = normalize_label(label)
    if concept_id.endswith("_rate") or concept_id.startswith(_RATE_PREFIXES):
        return CashSemantics(recognition="rate", cash_movement="none")
    if concept_id.startswith("bs."):
        return CashSemantics(recognition="stock", cash_movement="none")
    if "capitalized" in normalized or "capitalised" in normalized:
        return CashSemantics(recognition="noncash", cash_movement="outflow")
    if concept_id.startswith("cf.") or concept_id.startswith("debt."):
        recognition = "cash"
    else:
        recognition = "accrual"
    if context_role == "uses" or concept_id in _OUTFLOW:
        movement = "outflow"
    elif context_role == "sources" or concept_id in _INFLOW:
        movement = "inflow"
    else:
        movement = "none"
    if context_role == "uses":
        movement = "outflow"
    elif context_role == "sources":
        movement = "inflow"
    return CashSemantics(recognition=recognition, cash_movement=movement)
