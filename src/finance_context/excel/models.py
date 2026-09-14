from __future__ import annotations

from pydantic import BaseModel, Field


class SheetInfo(BaseModel):
    name: str
    sheet_id: int
    hidden: bool = False


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
