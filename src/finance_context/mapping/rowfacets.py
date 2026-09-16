from __future__ import annotations

from finance_context.mapping.models import FacetGuess, InferredFacets, RowContext, ValueKind
from finance_context.mapping.normalize import normalize_label, section_class

_OPENING = ("opening", "beg", "bf", "brought")
_CLOSING = ("closing", "ending", "carried", "cf")
_ACCRUAL = ("accrual", "accrued", "revenue earned")
_CASH = ("cash inflow", "cash outflow", "collection", "collections", "receipt", "receipts", "paid")


def infer_row_facets(ctx: RowContext, *, pattern_kind: str | None = None) -> InferredFacets:
    n = normalize_label(ctx.label)
    tokens = set(n.split())
    blob = normalize_label(
        " ".join([ctx.label, ctx.parent_label or "", *ctx.section_path, ctx.sheet])
    )
    klass = section_class(ctx.parent_label, ctx.section_path, ctx.sheet)
    unit = ctx.value_kind
    return InferredFacets(
        unit=FacetGuess(value=unit, confident=True),
        series=_series(ctx),
        nature=_nature(n, tokens, pattern_kind),
        position=_position(tokens, n),
        direction=_direction(klass, blob),
        basis=_basis(blob, klass),
        statement=_statement(n),
    )


def inferred_value_kind(ctx: RowContext) -> ValueKind:
    unit = ctx.inferred_facets.unit.value
    if unit in {"money", "rate", "ratio", "count"}:
        return unit  # type: ignore[return-value]
    return ctx.value_kind


def _series(ctx: RowContext) -> FacetGuess:
    if not ctx.period_headers:
        return FacetGuess(value="constant", confident=True)
    return FacetGuess(value="series", confident=True)


def _nature(label: str, tokens: set[str], pattern_kind: str | None) -> FacetGuess:
    balance_words = tokens & {"opening", "closing", "balance", "beg", "ending"}
    if balance_words:
        return FacetGuess(value="balance", confident=True)
    if pattern_kind == "roll" and balance_words:
        return FacetGuess(value="balance", confident=True)
    return FacetGuess()


def _position(tokens: set[str], label: str) -> FacetGuess:
    opening = bool(tokens & set(_OPENING)) or "brought forward" in label
    closing = bool(tokens & set(_CLOSING)) or "carried forward" in label
    if opening and not closing:
        return FacetGuess(value="opening", confident=True)
    if closing and not opening:
        return FacetGuess(value="closing", confident=True)
    return FacetGuess()


def _direction(klass: str, blob: str) -> FacetGuess:
    if klass == "cash inflows":
        return FacetGuess(value="inflow", confident=True)
    if klass == "cash outflows":
        return FacetGuess(value="outflow", confident=True)
    return FacetGuess()


def _basis(blob: str, klass: str) -> FacetGuess:
    if any(token in blob for token in _ACCRUAL):
        return FacetGuess(value="accrual", confident=True)
    if any(token in blob for token in _CASH) or klass in {"cash inflows", "cash outflows"}:
        return FacetGuess(value="cash", confident=True)
    return FacetGuess()


def _statement(label: str) -> FacetGuess:
    if any(token in label.split() for token in ("dscr", "llcr", "plcr")):
        return FacetGuess(value="cov", confident=True)
    return FacetGuess()
