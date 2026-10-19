from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_serializer


class SheetInfo(BaseModel):
    name: str
    sheet_id: int
    hidden: bool = False
    hidden_rows: list[int] = Field(default_factory=list)
    hidden_cols: list[int] = Field(default_factory=list)
    # Excel's default: the group summary sits below the detail rows.
    summary_below: bool = True

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if data.get("summary_below", True):
            data.pop("summary_below", None)
        return data


class DefinedName(BaseModel):
    name: str
    formula: str
    hidden: bool = False


class WorkbookMeta(BaseModel):
    sheets: list[SheetInfo]
    has_vba: bool = False
    has_xlm: bool = False
    externals: list[str] = Field(default_factory=list)
    locale_hint: str | None = None
    iterate: bool = False
    date1904: bool = False
    defined_names: list[DefinedName] = Field(default_factory=list)


class CellPresence(BaseModel):
    sheet: str
    addr: str
    presence: Literal["populated", "styled_blank"]


class RawCell(BaseModel):
    sheet: str
    row: int
    col: int
    addr: str
    formula_raw: str | None = None
    cached_value: str | None = None
    hidden: bool = False
    number_format: str | None = None
    comment: str | None = None
    shared_si: int | None = None
    shared_master: bool = False
    style_indent: int = 0
    outline_level: int = 0
    col_outline_level: int = 0
