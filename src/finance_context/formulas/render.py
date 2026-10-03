from __future__ import annotations

from typing import Any

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.models import Edge
from finance_context.formulas.parse import _canonical_func_name
from finance_context.formulas.scan import FormulaSyntaxError

_DYNAMIC_FUNCS = {"INDIRECT", "OFFSET"}


def _shift_point(
    point: dict[str, Any], dc: int, dr: int, *, cols: bool, rows: bool
) -> dict[str, Any]:
    out = dict(point)
    if cols and "col" in out and not out.get("abs_col"):
        out["col"] = max(int(out["col"]) + dc, 1)
    if rows and "row" in out and not out.get("abs_row"):
        out["row"] = max(int(out["row"]) + dr, 1)
    return out


def _shift_ast(node: dict[str, Any], dc: int, dr: int) -> dict[str, Any]:
    op = node.get("op")
    if op == "ref":
        return _shift_point(node, dc, dr, cols=True, rows=True)
    if op == "range":
        out = dict(node)
        move_cols = not node.get("row_only")
        move_rows = not node.get("col_only")
        if isinstance(node.get("start"), dict):
            out["start"] = _shift_point(
                node["start"], dc, dr, cols=move_cols, rows=move_rows
            )
        if isinstance(node.get("end"), dict):
            out["end"] = _shift_point(node["end"], dc, dr, cols=move_cols, rows=move_rows)
        return out
    if op == "func":
        return {**node, "args": [_shift_ast(arg, dc, dr) for arg in node.get("args") or []]}
    if op == "bin":
        return {
            **node,
            "left": _shift_ast(node["left"], dc, dr),
            "right": _shift_ast(node["right"], dc, dr),
        }
    if op in {"unary", "percent"}:
        return {**node, "expr": _shift_ast(node["expr"], dc, dr)}
    return dict(node)


def _collect_edges(
    node: dict[str, Any],
    current_sheet: str,
    source: str,
    edges: list[Edge],
    *,
    suppress: bool,
) -> None:
    op = node["op"]
    if op == "func":
        dynamic = _canonical_func_name(node["name"]).upper() in _DYNAMIC_FUNCS
        if dynamic and not suppress:
            edges.append(Edge(kind="dynamic", source=source, target=None, unresolved=True))
        for arg in node["args"]:
            _collect_edges(arg, current_sheet, source, edges, suppress=suppress or dynamic)
        return
    if op == "bin":
        _collect_edges(node["left"], current_sheet, source, edges, suppress=suppress)
        _collect_edges(node["right"], current_sheet, source, edges, suppress=suppress)
        return
    if op in {"unary", "percent"}:
        _collect_edges(node["expr"], current_sheet, source, edges, suppress=suppress)
        return
    if suppress:
        return
    if op == "name":
        token = str(node.get("value") or "")
        if token:
            edges.append(
                Edge(
                    kind="ref",
                    source=source,
                    target=token,
                    unresolved=True,
                    named=True,
                )
            )
        return
    if op == "ref":
        edges.append(_ref_edge(node, current_sheet, source))
        return
    if op == "range":
        if node.get("named"):
            target = f"{node['start_name']}:{node['end_name']}"
            edges.append(
                Edge(kind="range", source=source, target=target, unresolved=True, named=True)
            )
            return
        edges.append(_range_edge(node, current_sheet, source))


def _ref_edge(node: dict[str, Any], current_sheet: str, source: str) -> Edge:
    sheet = node.get("sheet") or current_sheet
    target = f"{sheet}!{index_to_col(node['col'])}{node['row']}"
    abs_col = bool(node.get("abs_col"))
    abs_row = bool(node.get("abs_row"))
    if node.get("external"):
        spec = node["external"]
        return Edge(
            kind="external",
            source=source,
            target=f"[{spec}]{target}",
            abs_col=abs_col,
            abs_row=abs_row,
        )
    kind: str = "cross_sheet" if node.get("sheet") and node["sheet"] != current_sheet else "ref"
    return Edge(
        kind=kind,  # type: ignore[arg-type]
        source=source,
        target=target,
        abs_col=abs_col,
        abs_row=abs_row,
    )


def _range_target(node: dict[str, Any], sheet: str) -> str:
    if node.get("col_only"):
        c1 = index_to_col(node["start"]["col"])
        c2 = index_to_col(node["end"]["col"])
        return f"{sheet}!{c1}:{c2}"
    if node.get("row_only"):
        return f"{sheet}!{node['start']['row']}:{node['end']['row']}"
    a = f"{index_to_col(node['start']['col'])}{node['start']['row']}"
    b = f"{index_to_col(node['end']['col'])}{node['end']['row']}"
    return f"{sheet}!{a}:{b}"


def _range_edge(node: dict[str, Any], current_sheet: str, source: str) -> Edge:
    sheet = node.get("sheet") or current_sheet
    target = _range_target(node, sheet)
    start = node.get("start") or {}
    end = node.get("end") or {}
    anchors = {
        "abs_col": start.get("abs_col"),
        "abs_row": start.get("abs_row"),
        "abs_col_end": end.get("abs_col"),
        "abs_row_end": end.get("abs_row"),
    }
    if node.get("external"):
        return Edge(
            kind="external",
            source=source,
            target=f"[{node['external']}]{target}",
            **anchors,
        )
    return Edge(kind="range", source=source, target=target, **anchors)


def _r1c1(col: int, row: int, abs_col: bool, abs_row: bool, ocol: int, orow: int) -> str:
    r = f"R{row}" if abs_row else f"R[{row - orow}]"
    c = f"C{col}" if abs_col else f"C[{col - ocol}]"
    return r + c


def _prec(node: dict[str, Any]) -> int:
    op = node.get("op")
    if op == "bin":
        return {
            "^": 5,
            "*": 4,
            "/": 4,
            "+": 3,
            "-": 3,
            "=": 2,
            "<>": 2,
            "<": 2,
            ">": 2,
            "<=": 2,
            ">=": 2,
            "&": 1,
        }.get(node.get("kind", ""), 0)
    if op == "unary":
        return 6
    if op == "percent":
        return 7
    return 8


def _paren(child: dict[str, Any], text: str, parent_prec: int, *, right: bool, op: str) -> str:
    child_prec = _prec(child)
    if child_prec < parent_prec:
        return f"({text})"
    if right and child_prec == parent_prec and op in {"-", "/", "*"}:
        return f"({text})"
    return text


def _render(node: dict[str, Any], ocol: int, orow: int, sep: str, decimal: str) -> str:
    op = node["op"]
    if op == "num":
        value = node["value"]
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = str(value)
        if decimal == ",":
            return text.replace(".", ",")
        return text
    if op == "str":
        inner = node["value"].replace('"', '""')
        return f'"{inner}"'
    if op == "err":
        return node["value"]
    if op == "name":
        return node["value"]
    if op == "ref":
        return _render_ref(node, ocol, orow)
    if op == "range":
        if node.get("named"):
            return f"{node['start_name']}:{node['end_name']}"
        return _render_range(node, ocol, orow)
    if op == "unary":
        inner = _render(node["expr"], ocol, orow, sep, decimal)
        if _prec(node["expr"]) < _prec(node):
            inner = f"({inner})"
        return node["kind"] + inner
    if op == "percent":
        return _render(node["expr"], ocol, orow, sep, decimal) + "%"
    if op == "bin":
        left = _render(node["left"], ocol, orow, sep, decimal)
        right = _render(node["right"], ocol, orow, sep, decimal)
        prec = _prec(node)
        left = _paren(node["left"], left, prec, right=False, op=node["kind"])
        right = _paren(node["right"], right, prec, right=True, op=node["kind"])
        return left + node["kind"] + right
    if op == "func":
        args = sep.join(_render(a, ocol, orow, sep, decimal) for a in node["args"])
        return f"{node['name']}({args})"
    raise FormulaSyntaxError("cannot render")


def _sheet_prefix(node: dict[str, Any]) -> str:
    extra = ""
    if node.get("external"):
        extra = f"[{node['external']}]"
    sheet = node.get("sheet")
    if sheet:
        return f"{extra}{sheet}!"
    return extra


def _render_ref(node: dict[str, Any], ocol: int, orow: int) -> str:
    body = _r1c1(
        node["col"],
        node["row"],
        node.get("abs_col", False),
        node.get("abs_row", False),
        ocol,
        orow,
    )
    return _sheet_prefix(node) + body


def _render_range(node: dict[str, Any], ocol: int, orow: int) -> str:
    prefix = _sheet_prefix(node)
    if node.get("col_only"):
        c1 = _r1c1(node["start"]["col"], 1, node["start"].get("abs_col", False), False, ocol, orow)
        c2 = _r1c1(node["end"]["col"], 1, node["end"].get("abs_col", False), False, ocol, orow)
        return prefix + c1[c1.index("C") :] + ":" + c2[c2.index("C") :]
    if node.get("row_only"):
        r1 = _r1c1(1, node["start"]["row"], False, node["start"].get("abs_row", False), ocol, orow)
        r2 = _r1c1(1, node["end"]["row"], False, node["end"].get("abs_row", False), ocol, orow)
        return prefix + r1[: r1.index("C")] + ":" + r2[: r2.index("C")]
    a = _render_ref({**node["start"], "sheet": None}, ocol, orow)
    b = _render_ref({**node["end"], "sheet": None}, ocol, orow)
    return prefix + a + ":" + b
