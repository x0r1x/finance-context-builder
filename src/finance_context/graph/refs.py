from __future__ import annotations

from finance_context.excel.a1 import parse_addr


def parse_node_id(ref: str | None) -> tuple[str, int, int] | None:
    if not ref or "!" not in ref:
        return None
    sheet, addr = ref.rsplit("!", 1)
    sheet = sheet.strip().strip("'").replace("''", "'")
    addr = addr.split(":")[0].replace("$", "")
    try:
        col, row = parse_addr(addr)
    except ValueError:
        return None
    if not sheet:
        return None
    return sheet, col, row


def node_id(sheet: str, addr: str) -> str:
    return f"{sheet}!{addr}"
