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


def _target_rows(edge: Edge, usage: SheetUsage | None) -> list[tuple[str, int]]:
    rows = (cell_ref_row(ref) for ref in _targets(edge, usage))
    return list(dict.fromkeys(row for row in rows if row is not None))


def row_adjacency(
    edges: list,
    *,
    limit: int = 8,
    usage: SheetUsage | None = None,
) -> tuple[dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]]]:
    """Row precedents and dependents, at most ``limit`` each (0 keeps all).

    A copied-down SUMIF(A:A, ...) repeats one range thousands of times. Its rows
    are resolved once, and a source row that already took a range is skipped.
    Rows are kept only for a range seen a second time, so one-off windows such
    as a copied-down SUM(A1:A1000) are not held for the whole call.
    With a limit, a full list stops taking entries and a range whose rows are
    all full is not walked again. The lists are the first ``limit`` entries of
    one pass per edge, in the same order.
    """
    precedents: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
    dependents: dict[tuple[str, int], dict[str, None]] = defaultdict(dict)
    cap = limit if limit > 0 else None
    # Rows of each reused range, and the rows of it whose dependents are not full yet.
    rows_of: dict[tuple[str, bool], list[tuple[str, int]]] = {}
    open_rows: dict[tuple[str, bool], list[tuple[str, int]]] = {}
    seen_once: set[tuple[str, bool]] = set()
    done: set[tuple[tuple[str, int], str, bool]] = set()
    for raw in edges:
        edge = as_edge(raw)
        if edge.named:
            continue
        source = cell_ref_row(str(edge.source or ""))
        if source is None:
            continue
        slot: tuple[str, bool] | None = None
        if edge.kind == "range":
            slot = (str(edge.target or ""), bool(edge.unresolved))
            if (source, *slot) in done:
                continue
            done.add((source, *slot))
            targets = rows_of.get(slot)
            if targets is None:
                targets = _target_rows(edge, usage)
                if slot in seen_once:
                    rows_of[slot] = targets
                    open_rows[slot] = targets
                else:
                    seen_once.add(slot)
        else:
            targets = _target_rows(edge, usage)
        src_ref = row_key_ref(*source)
        own = precedents[source]
        for target in targets:
            if cap is not None and len(own) >= cap:
                break
            if source != target:
                own.setdefault(row_key_ref(*target), None)
        waiting = open_rows.get(slot, targets) if slot is not None else targets
        for target in waiting:
            if source == target:
                continue
            readers = dependents[target]
            if cap is None or len(readers) < cap:
                readers.setdefault(src_ref, None)
        if slot in open_rows and cap is not None:
            open_rows[slot] = [row for row in waiting if len(dependents[row]) < cap]
    return (
        {key: list(values)[:cap] for key, values in precedents.items() if values},
        {key: list(values)[:cap] for key, values in dependents.items() if values},
    )
