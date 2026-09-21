from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.mapping.models import MappingDocument
from finance_context.render.markdown import render_markdown


def _c(sheet: str, addr: str, value: str | None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def test_build_keeps_display_unit_without_rescaling_values() -> None:
    cells = [
        _c("Input Assumptions", "B5", "Parameter"),
        _c("Input Assumptions", "C5", "Units"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "B8", "Toll Rate"),
        _c("Input Assumptions", "C8", "£"),
        _c("Input Assumptions", "D8", "3.56"),
        _c("Input Assumptions", "B9", "CAPEX"),
        _c("Input Assumptions", "C9", "k£"),
        _c("Input Assumptions", "D9", "75000"),
        _c("Input Assumptions", "B10", "Maintenance"),
        _c("Input Assumptions", "C10", "£/year"),
        _c("Input Assumptions", "D10", "8900000"),
        _c("Input Assumptions", "B11", "Traffic"),
        _c("Input Assumptions", "C11", "veh/year"),
        _c("Input Assumptions", "D11", "3125000"),
        _c("Input Assumptions", "B12", "Tax Rate"),
        _c("Input Assumptions", "C12", "%"),
        _c("Input Assumptions", "D12", "0.3"),
    ]
    layout = detect_layout(cells)
    doc = build_context(
        job_id="u",
        workbook_meta={"sheets": [{"name": s.name} for s in layout.sheets]},
        cells=cells,
        layout=layout,
        mapping=MappingDocument(),
    )
    by_label = {row.label: row for row in doc.inventory if row.sheet == "Input Assumptions"}
    pound = by_label["Toll Rate"].display_unit
    assert pound is not None and pound.raw == "£" and pound.scale == 1 and pound.currency == "GBP"
    assert by_label["Toll Rate"].unit == "money"
    kilo = by_label["CAPEX"].display_unit
    assert kilo is not None and kilo.raw == "k£" and kilo.scale == 1000
    capex_cells = by_label["CAPEX"].cells
    value = next(c.cached_value for c in capex_cells if c.role == "value")
    assert str(value).replace(",", "").startswith("75000")
    flow = by_label["Maintenance"].display_unit
    assert flow is not None and flow.per == "year" and flow.dimension == "money"
    traffic = by_label["Traffic"].display_unit
    assert traffic is not None and traffic.dimension == "count" and traffic.per == "year"
    assert by_label["Tax Rate"].unit == "rate"
    rendered = render_markdown(doc)
    assert "k£" in rendered or "£/year" in rendered
