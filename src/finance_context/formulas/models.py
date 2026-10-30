from __future__ import annotations

import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

EdgeKind = Literal["ref", "range", "cross_sheet", "external", "dynamic"]


@dataclass(slots=True)
class Edge:
    kind: EdgeKind
    source: str
    target: str | None = None
    unresolved: bool = False
    truncated: bool = False
    abs_col: bool | None = None
    abs_row: bool | None = None
    abs_col_end: bool | None = None
    abs_row_end: bool | None = None
    named: bool = False


@dataclass(slots=True)
class ParsedFormula:
    ast: dict[str, Any] | None
    template: str | None
    unparsed: bool
    edges: list[Edge] = field(default_factory=list)


_IR_CELL_FIELDS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "formula_template",
    "unparsed",
    "cached_value",
    "hidden",
    "number_format",
    "comment",
    "ast_json",
    "merge_end_col",
    "style_indent",
    "outline_level",
    "col_outline_level",
)
_IR_CELL_FIELD_SET = frozenset(_IR_CELL_FIELDS)
_shared_sheets: dict[str, str] = {}


def _share_sheet(name: str) -> str:
    return _shared_sheets.setdefault(name, name)


@dataclass(slots=True)
class IrCell:
    """One compiled cell. Slot storage, with dict-style reads for layout and context."""

    sheet: str
    row: int
    col: int
    addr: str
    formula_raw: str | None = None
    formula_template: str | None = None
    unparsed: bool | None = False
    cached_value: str | None = None
    hidden: bool | None = None
    number_format: str | None = None
    comment: str | None = None
    ast_json: str | bytes | None = None
    merge_end_col: int | None = None
    style_indent: int | None = None
    outline_level: int | None = None
    col_outline_level: int | None = None

    def __post_init__(self) -> None:
        self.sheet = _share_sheet(self.sheet)

    def get(self, key: str, default: object = None) -> object:
        if key not in _IR_CELL_FIELD_SET:
            return default
        return getattr(self, key)

    def __getitem__(self, key: str) -> object:
        if key not in _IR_CELL_FIELD_SET:
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key: str, value: object) -> None:
        if key not in _IR_CELL_FIELD_SET:
            raise KeyError(key)
        setattr(self, key, value)

    def keys(self):
        return _IR_CELL_FIELDS

    def __iter__(self):
        return iter(_IR_CELL_FIELDS)

    def __len__(self) -> int:
        return len(_IR_CELL_FIELDS)


def as_ir_cell(value: IrCell | Mapping[str, Any]) -> IrCell:
    """One cell. A dict is copied once; an IrCell is the same object."""
    if isinstance(value, IrCell):
        return value
    sheet = value.get("sheet")
    addr = value.get("addr")
    row = value.get("row")
    col = value.get("col")
    return IrCell(
        sheet="" if sheet is None else str(sheet),
        row=0 if row is None else int(row),
        col=0 if col is None else int(col),
        addr="" if addr is None else str(addr),
        formula_raw=_opt_text(value.get("formula_raw")),
        formula_template=_opt_text(value.get("formula_template")),
        unparsed=_opt_bool(value.get("unparsed")),
        cached_value=_opt_text(value.get("cached_value")),
        hidden=_opt_bool(value.get("hidden")),
        number_format=_opt_text(value.get("number_format")),
        comment=_opt_text(value.get("comment")),
        ast_json=_opt_text(value.get("ast_json")),
        merge_end_col=_opt_int(value.get("merge_end_col")),
        style_indent=_opt_int(value.get("style_indent")),
        outline_level=_opt_int(value.get("outline_level")),
        col_outline_level=_opt_int(value.get("col_outline_level")),
    )


def ast_json_text(value: str | bytes | None) -> str | None:
    """Plain AST JSON. In-memory cells may hold zlib bytes until relations run."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return zlib.decompress(value).decode("utf-8")
    return value


def cell_parquet_row(cell: IrCell) -> tuple[object, ...]:
    return (
        cell.sheet,
        cell.row,
        cell.col,
        cell.addr,
        cell.formula_raw,
        cell.formula_template,
        cell.unparsed,
        cell.cached_value,
        cell.hidden,
        cell.number_format,
        cell.comment,
        ast_json_text(cell.ast_json),
    )


def release_cell_asts(cells: list) -> None:
    """Drop formula trees once relations have read them. Parquet already has the JSON."""
    for cell in cells:
        if isinstance(cell, IrCell):
            cell.ast_json = None
        elif isinstance(cell, dict):
            cell.pop("ast_json", None)


def _opt_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


@dataclass
class CompileResult:
    """Compiled edges. Cells stay in ``ir/cells.parquet``; ``cells`` is empty."""

    cells: list[IrCell] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    cell_edges: list[Any] = field(default_factory=list)
    cell_count: int = 0
    usage: object | None = None


def as_edge(value: Edge | Mapping[str, Any]) -> Edge:
    """One formula edge. A dict from parquet is copied once; an Edge is reused."""
    if isinstance(value, Edge):
        return value
    target = value.get("target")
    return Edge(
        kind=value.get("kind") or "ref",  # type: ignore[arg-type]
        source=str(value.get("source") or ""),
        target=None if target is None else str(target),
        unresolved=bool(value.get("unresolved")),
        truncated=bool(value.get("truncated")),
        abs_col=_opt_bool(value.get("abs_col")),
        abs_row=_opt_bool(value.get("abs_row")),
        abs_col_end=_opt_bool(value.get("abs_col_end")),
        abs_row_end=_opt_bool(value.get("abs_row_end")),
        named=bool(value.get("named")),
    )
