from __future__ import annotations

import re

import numpy as np
from scipy import sparse

from finance_context.excel.a1 import col_to_index, format_addr, index_to_col
from finance_context.formulas.models import CsrGraph, Edge

RANGE_EXPAND_CAP = 2000
_EXCEL_MAX_ROW = 1_048_576
_EXCEL_MAX_COL = 16_384

_CELL = re.compile(r"^\$?([A-Za-z]+)\$?(\d+)$", re.I)
_COLS = re.compile(r"^\$?([A-Za-z]+):\$?([A-Za-z]+)$", re.I)
_ROWS = re.compile(r"^\$?(\d+):\$?(\d+)$")


def split_sheet_ref(target: str) -> tuple[str, str]:
    if target.startswith("'"):
        end = target.find("'!")
        if end < 0:
            raise ValueError(target)
        return target[1:end], target[end + 2 :]
    if "!" not in target:
        raise ValueError(target)
    sheet, body = target.split("!", 1)
    return sheet, body


def expand_range(target: str, cap: int = RANGE_EXPAND_CAP) -> tuple[list[str], bool]:
    sheet, body = split_sheet_ref(target)
    cell = _CELL.fullmatch(body)
    if cell:
        return [f"{sheet}!{format_addr(col_to_index(cell.group(1)), int(cell.group(2)))}"], False

    cells_range = re.fullmatch(
        r"\$?([A-Za-z]+)\$?(\d+):\$?([A-Za-z]+)\$?(\d+)", body, re.I
    )
    if cells_range:
        c1 = col_to_index(cells_range.group(1))
        r1 = int(cells_range.group(2))
        c2 = col_to_index(cells_range.group(3))
        r2 = int(cells_range.group(4))
        if c1 > c2:
            c1, c2 = c2, c1
        if r1 > r2:
            r1, r2 = r2, r1
        return _take(sheet, c1, c2, r1, r2, cap)

    cols = _COLS.fullmatch(body)
    if cols:
        c1 = col_to_index(cols.group(1))
        c2 = col_to_index(cols.group(2))
        if c1 > c2:
            c1, c2 = c2, c1
        return _take(sheet, c1, c2, 1, _EXCEL_MAX_ROW, cap)

    rows = _ROWS.fullmatch(body)
    if rows:
        r1, r2 = int(rows.group(1)), int(rows.group(2))
        if r1 > r2:
            r1, r2 = r2, r1
        return _take(sheet, 1, _EXCEL_MAX_COL, r1, r2, cap)

    return [target], False


def _take(
    sheet: str, c1: int, c2: int, r1: int, r2: int, cap: int
) -> tuple[list[str], bool]:
    out: list[str] = []
    truncated = False
    for row in range(r1, r2 + 1):
        for col in range(c1, c2 + 1):
            if len(out) >= cap:
                return out, True
            out.append(f"{sheet}!{index_to_col(col)}{row}")
    if (c2 - c1 + 1) * (r2 - r1 + 1) > len(out):
        truncated = True
    return out, truncated


def build_csr(
    edges: list[Edge], extra_nodes: list[str] | None = None
) -> CsrGraph:
    nodes: set[str] = set(extra_nodes or [])
    pairs: list[tuple[str, str]] = []
    truncated_sources: set[str] = set()
    for edge in edges:
        nodes.add(edge.source)
        if edge.kind == "range" and edge.target:
            if edge.unresolved or "!" not in edge.target:
                nodes.add(edge.target)
                continue
            cells, trunc = expand_range(edge.target)
            if trunc:
                truncated_sources.add(edge.source)
                edge.truncated = True
            for cell in cells:
                nodes.add(cell)
                pairs.append((edge.source, cell))
        elif edge.target and edge.kind in {"ref", "cross_sheet"}:
            nodes.add(edge.target)
            pairs.append((edge.source, edge.target))

    ordered = sorted(nodes)
    index = {name: i for i, name in enumerate(ordered)}
    n = len(ordered)
    if not pairs or n == 0:
        matrix = sparse.csr_matrix((n, n), dtype=np.uint8)
        return CsrGraph(
            nodes=ordered, node_index=index, matrix=matrix, truncated_sources=truncated_sources
        )
    rows = np.fromiter((index[src] for src, _ in pairs), dtype=np.int32, count=len(pairs))
    cols = np.fromiter((index[dst] for _, dst in pairs), dtype=np.int32, count=len(pairs))
    data = np.ones(len(pairs), dtype=np.uint8)
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(n, n), dtype=np.uint8)
    return CsrGraph(
        nodes=ordered, node_index=index, matrix=matrix, truncated_sources=truncated_sources
    )
