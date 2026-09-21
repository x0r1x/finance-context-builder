from __future__ import annotations

from finance_context.mapping.models import RowContext
from finance_context.mapping.statement import context_tokens, is_cashflow_context

ContextRole = str

_ASSUMPTION_TOKENS = {"assumption", "assumptions", "input"}
_DEBT_SCHEDULE_TOKENS = {"repayment", "amortisation", "amortization", "drawdown"}
_CFADS_INPUTS = {"cf.receipts", "pnl.revenue", "cf.opex_paid", "pnl.opex"}


def infer_context_role(ctx: RowContext, concept_id: str | None = None) -> str:
    tokens = context_tokens(
        sheet=ctx.sheet,
        section_path=ctx.section_path,
        parent=ctx.parent_label,
        label=ctx.label,
    )
    if "uses" in tokens:
        return "uses"
    if tokens & {"sources", "source"}:
        return "sources"
    if tokens & _DEBT_SCHEDULE_TOKENS and tokens & {"debt", "loan", "schedule"}:
        return "debt_schedule"
    if "irr" in tokens:
        return "irr"
    if tokens & _ASSUMPTION_TOKENS:
        return "assumption"
    if ctx.article_role == "assumption":
        return "assumption"
    if (
        concept_id in _CFADS_INPUTS
        and is_cashflow_context(
            sheet=ctx.sheet,
            section_path=ctx.section_path,
            parent=ctx.parent_label,
        )
        and "cfads" not in tokens
    ):
        return "cfads_input"
    return "line"


def infer_secondary_concepts(
    *,
    concept_id: str | None,
    context_role: str,
    feeds_cfads: bool = False,
) -> list[str]:
    extra: list[str] = []
    if concept_id == "cf.capex" and context_role == "uses":
        extra.append("cf.uses")
    if feeds_cfads and concept_id and concept_id != "cf.cfads":
        extra.append("cf.cfads")
    return extra


def infer_row_roles(
    ctx: RowContext, concept_id: str | None, *, feeds_cfads: bool = False
) -> tuple[str, list[str]]:
    role = infer_context_role(ctx, concept_id)
    return role, infer_secondary_concepts(
        concept_id=concept_id, context_role=role, feeds_cfads=feeds_cfads
    )
