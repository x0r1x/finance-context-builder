"""Catalog projection. Rows and axes without cell caches."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_serializer

CATALOG_SCHEMA_VERSION = "catalog-2"


class CatalogHints(BaseModel):
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None


class CatalogPeriod(BaseModel):
    period_key: str
    start_date: str | None = None
    end_date: str | None = None
    group_key: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        for key in ("start_date", "end_date", "group_key"):
            if data.get(key) is None:
                data.pop(key, None)
        if not data.get("flags"):
            data.pop("flags", None)
        return data


class CatalogAxis(BaseModel):
    id: str
    sheet: str
    grain: str | None = None
    periods: list[CatalogPeriod] = Field(default_factory=list)


class CatalogRow(BaseModel):
    row_key: str
    sheet: str
    label: str
    label_path: list[str] = Field(default_factory=list)
    kind: str
    axis_ids: list[str] = Field(default_factory=list)
    has_formula: bool = False
    hints: CatalogHints = Field(default_factory=CatalogHints)


class CatalogDocument(BaseModel):
    """Rows of one finished job, without values. Schema catalog-2."""

    schema_version: Literal["catalog-2"] = CATALOG_SCHEMA_VERSION
    total: int
    offset: int = 0
    limit: int | None = None
    axes: list[CatalogAxis] = Field(default_factory=list)
    rows: list[CatalogRow] = Field(default_factory=list)
