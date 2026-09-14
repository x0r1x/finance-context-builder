from __future__ import annotations

import logging

from tests.helpers.ports import CapBudget, DenySlots, FakeChat, FakeEmbed, GrantSlots

from finance_context.errors import PortError
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.models import Concept
from finance_context.mapping.normalize import normalize_label

TAXONOMY = [
    Concept(id="pnl.revenue", labels=["Revenue", "Выручка", "Sales"]),
    Concept(id="pnl.gmv", labels=["GMV"]),
    Concept(id="bs.assets_total", labels=["Total Assets", "Итого активы"]),
    Concept(id="bs.equity", labels=["Equity"]),
]

VECS = {
    "revenue": [1.0, 0.0, 0.0],
    "выручка": [1.0, 0.0, 0.0],
    "sales": [1.0, 0.0, 0.0],
    "gmv": [0.0, 1.0, 0.0],
    "total assets": [0.0, 0.0, 1.0],
    "итого активы": [0.0, 0.0, 1.0],
    "equity": [0.0, 0.5, 0.5],
}


def _layout(*rows: LayoutRow, sheet: str = "P&L") -> Layout:
    headers = [
        AxisHeader(col=2, text="2023", role="historical", period_key="2023"),
        AxisHeader(col=3, text="2024E", role="forecast", period_key="2024E"),
    ]
    return Layout(
        sheets=[
            SheetLayout(
                name=sheet,
                blocks=[
                    Block(
                        block_id=f"{sheet}!r1",
                        label_col=1,
                        axis=Axis(id=f"{sheet}!r1", row=1, headers=headers),
                        rows=list(rows),
                    )
                ],
            )
        ]
    )


def test_normalize_strips_whole_parentheses() -> None:
    assert normalize_label("Revenue (net)") == "revenue"
    assert normalize_label("EBITDA (adj.)") == "ebitda"


def test_glossary_exact_beats_knn() -> None:
    embed = FakeEmbed(VECS)
    layout = _layout(LayoutRow(row=2, label="Выручка"))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={("выручка", ""): "pnl.revenue"},
        embed=embed,
        chat=FakeChat("pnl.gmv"),
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id == "pnl.revenue"
    assert doc.rows[0].source == "glossary"
    assert embed.calls == 0


def test_gmv_is_not_pnl_revenue() -> None:
    layout = _layout(LayoutRow(row=2, label="GMV"))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={("gmv", ""): "pnl.revenue"},
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id != "pnl.revenue"
    assert doc.rows[0].concept_id == "pnl.gmv"


def test_confident_cosine_maps_without_chat() -> None:
    chat = FakeChat("bs.equity")
    layout = _layout(LayoutRow(row=2, label="Выручка"))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=FakeEmbed(VECS),
        chat=chat,
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id == "pnl.revenue"
    assert doc.rows[0].source == "embed"
    assert chat.calls == 0


def test_ambiguous_without_chat_emits_question() -> None:
    layout = _layout(LayoutRow(row=2, label="Mystery line"))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=FakeEmbed(VECS),
        chat=None,
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id is None
    assert doc.rows[0].source == "question"
    assert doc.questions
    assert doc.questions[0].kind == "mapping"
    assert "unknown" in doc.questions[0].options


def test_no_slot_does_not_call_embed_or_chat(caplog) -> None:
    embed = FakeEmbed(VECS)
    chat = FakeChat("pnl.revenue")
    layout = _layout(LayoutRow(row=2, label="Выручка"))
    caplog.set_level(logging.INFO, logger="finance_context")
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=embed,
        chat=chat,
        slots=DenySlots(),
    )
    assert embed.calls == 0
    assert chat.calls == 0
    assert doc.rows[0].source == "question"
    assert any(
        r.__dict__.get("event") == "port_fallback"
        and r.__dict__.get("reason") == "slot_timeout"
        for r in caplog.records
    )


def test_embed_port_error_logs_port_fallback(caplog) -> None:
    class BoomEmbed:
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise PortError("embed down")

    caplog.set_level(logging.INFO, logger="finance_context")
    layout = _layout(LayoutRow(row=2, label="Выручка"))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=BoomEmbed(),
        chat=None,
        slots=GrantSlots(),
    )
    assert doc.rows[0].source == "question"
    assert any(
        r.__dict__.get("event") == "port_fallback"
        and r.__dict__.get("reason") == "port_error"
        and r.__dict__.get("port") == "embed"
        for r in caplog.records
    )


def test_mapping_row_embed_skipped_when_budget_zero() -> None:
    embed = FakeEmbed(VECS)
    layout = _layout(LayoutRow(row=2, label="Выручка"))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=embed,
        chat=None,
        slots=CapBudget({"embed": 0, "llm": 0}),
    )
    assert embed.calls == 1
    assert doc.rows[0].source != "embed"


def test_prompt_contains_ids_not_cached_value() -> None:
    chat = FakeChat("pnl.revenue")
    layout = _layout(LayoutRow(row=2, label="Mystery line"))
    cells = [
        {
            "sheet": "P&L",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "cached_value": "SECRET_VALUE_42",
            "formula_raw": None,
            "formula_template": None,
        }
    ]
    map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=None,
        chat=chat,
        slots=GrantSlots(),
        cells=cells,
    )
    blob = str(chat.messages_seen)
    assert chat.calls == 1
    assert "SECRET_VALUE_42" not in blob
    assert "pnl.revenue" in blob
    assert "Mystery line" in blob


def test_check_row_article_role_is_check() -> None:
    layout = _layout(LayoutRow(row=3, label="Проверка баланса", check_row=True))
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    assert doc.rows[0].article_role == "check"
