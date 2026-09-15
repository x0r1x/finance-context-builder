from __future__ import annotations

from finance_context.mapping.models import Candidate, Concept, RowContext, ValueKind

_PREFIX_STATEMENT = {
    "pnl": "pnl",
    "bs": "bs",
    "cf": "cf",
    "val": "val",
    "ops": "ops",
    "fx": "fx",
    "cov": "cov",
    "covenant": "cov",
    "debt": "bs",
    "liq": "cf",
}

_KIND_BY_ID: dict[str, ValueKind] = {
    "pnl.interest_rate": "rate",
    "pnl.tax_rate": "rate",
    "pnl.volume": "count",
    "fx.rate": "rate",
    "val.wacc": "rate",
    "val.irr": "rate",
    "ops.headcount": "count",
    "cov.dscr": "ratio",
    "cov.llcr": "ratio",
    "cov.plcr": "ratio",
    "covenant.headroom": "ratio",
    "cov.leverage_limit": "ratio",
    "cov.leverage_headroom": "ratio",
    "liq.runway": "ratio",
    "liq.cash_conversion": "ratio",
    "liq.operating_cash_ratio": "ratio",
    "liq.liquidity_coverage": "ratio",
    "liq.trough_period": "count",
}

_COMPATIBLE: dict[ValueKind, set[str]] = {
    "money": {"money"},
    "rate": {"rate"},
    "ratio": {"ratio", "rate"},
    "count": {"count"},
}

_RATIO_LABELS = (
    "dscr",
    "llcr",
    "plcr",
    "coverage",
    "conversion",
    "leverage",
    "runway",
    "ratio",
)


def enrich_concept(concept: Concept) -> Concept:
    prefix = concept.id.split(".", 1)[0]
    statements = list(concept.statements) or (
        [_PREFIX_STATEMENT[prefix]] if prefix in _PREFIX_STATEMENT else []
    )
    value_kind = concept.value_kind or _KIND_BY_ID.get(concept.id, "money")
    definition = concept.definition or (
        f"{concept.id}: {', '.join(concept.labels)}"
    )
    return concept.model_copy(
        update={
            "statements": statements,
            "value_kind": value_kind,
            "definition": definition,
        }
    )


def prune_candidates(
    candidates: list[Candidate],
    ctx: RowContext,
    taxonomy: dict[str, Concept],
) -> list[Candidate]:
    allowed = _COMPATIBLE.get(ctx.value_kind, {"money"})
    label = (ctx.label or "").casefold()
    kept: list[Candidate] = []
    for item in candidates:
        concept = taxonomy.get(item.concept_id)
        if concept is None:
            continue
        kind = concept.value_kind or "money"
        if kind not in allowed and not _ratio_label_exception(label, kind, ctx.value_kind):
            continue
        if any(anti.casefold() in label for anti in concept.anti_labels if anti):
            continue
        kept.append(item)
    return kept


def _ratio_label_exception(label: str, concept_kind: str, row_kind: ValueKind) -> bool:
    if concept_kind != "ratio":
        return False
    if row_kind == "money" and any(token in label for token in _RATIO_LABELS):
        return True
    return False
