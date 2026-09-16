from __future__ import annotations

from collections import defaultdict

from finance_context.excel.a1 import parse_addr


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


def row_adjacency(
    edges: list[dict],
    *,
    limit: int = 8,
) -> tuple[dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]]]:
    precedents: dict[tuple[str, int], list[str]] = defaultdict(list)
    dependents: dict[tuple[str, int], list[str]] = defaultdict(list)
    for edge in edges:
        source = cell_ref_row(str(edge.get("source") or ""))
        target = cell_ref_row(str(edge.get("target") or "") or None)
        if source is None or target is None or source == target:
            continue
        src_ref = row_key_ref(*source)
        tgt_ref = row_key_ref(*target)
        if tgt_ref not in precedents[source]:
            precedents[source].append(tgt_ref)
        if src_ref not in dependents[target]:
            dependents[target].append(src_ref)
    if limit > 0:
        for table in (precedents, dependents):
            for key, values in table.items():
                table[key] = values[:limit]
    return dict(precedents), dict(dependents)
