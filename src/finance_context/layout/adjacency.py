from __future__ import annotations

from collections import defaultdict

from finance_context.excel.a1 import parse_addr
from finance_context.formulas.csr import SheetUsage, expand_range
from finance_context.formulas.models import Edge, as_edge


def cell_ref_row(ref: str | None) -> tuple[str, int] | None:
    if not ref or "!" not in ref:
        return None
    sheet, addr = ref.rsplit("!", 1)
    sheet = sheet.strip().strip("'").replace("''", "'")
    addr = addr.split(":")[0].replace("$", "")
    try:
        _col, row = parse_addr(addr)
    except ValueError:
        return None
    if not sheet:
        return None
    return sheet, row


def row_key_ref(sheet: str, row: int) -> str:
    return f"{sheet}!{row}"


def _targets(edge: Edge, usage: SheetUsage | None = None) -> list[str]:
    target = str(edge.target or "")
    if not target:
        return []
    # Cell edges already name one target. Only a formula-level range still expands.
    body = target.rsplit("!", 1)[-1]
    if edge.kind == "range" and not edge.unresolved and ":" in body:
        try:
            cells, _trunc = expand_range(target, usage=usage)
        except ValueError:
            return [target]
        return cells
    return [target]


def row_adjacency(
    edges: list,
    *,
    limit: int = 8,
    usage: SheetUsage | None = None,
) -> tuple[dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]]]:
    precedents: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
    dependents: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
    for raw in edges:
        edge = as_edge(raw)
        if edge.named:
            continue
        source = cell_ref_row(str(edge.source or ""))
        if source is None:
            continue
        for target_ref in _targets(edge, usage):
            target = cell_ref_row(target_ref)
            if target is None or source == target:
                continue
            src_ref = row_key_ref(*source)
            tgt_ref = row_key_ref(*target)
            precedents[source].setdefault(tgt_ref, None)
            dependents[target].setdefault(src_ref, None)
    cut = limit if limit > 0 else None
    return (
        {key: list(values)[:cut] for key, values in precedents.items()},
        {key: list(values)[:cut] for key, values in dependents.items()},
    )
