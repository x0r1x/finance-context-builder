from __future__ import annotations

import logging

from tests.helpers.ports import CapBudget, DenySlots, FakeChat, FakeEmbed, GrantSlots

from finance_context.errors import PortError
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.models import (
    Concept,
    LexicalPattern,
    PatternWhen,
    TaxonomyDocument,
)
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.taxonomy import register_document

TAXONOMY = [
    Concept(id="pnl.revenue", labels=["Revenue", "Выручка", "Sales"]),
    Concept(id="pnl.gmv", labels=["GMV"], exact_labels=["GMV"]),
    Concept(id="bs.assets_total", labels=["Total Assets", "Итого активы"]),
    Concept(id="bs.equity", labels=["Equity"]),
    Concept(id="bs.ar", labels=["Accounts receivable", "Opening AR"]),
    Concept(id="bs.cash", labels=["Cash", "Opening cash"]),
    Concept(id="cf.receipts", labels=["Receipts", "Collections"]),
    Concept(
        id="cf.receipts.subscription",
        labels=["Subscription collections"],
        broader="cf.receipts",
    ),
    Concept(id="cf.disbursements", labels=["Disbursements"]),
    Concept(id="cf.net", labels=["Net cash flow"]),
    Concept(id="cf.drawdown", labels=["Drawdown"]),
    Concept(id="val.npv", labels=["NPV"]),
]

register_document(
    TaxonomyDocument(
        concepts=TAXONOMY,
        patterns=[
            LexicalPattern(
                concept="cf.net",
                score=0.93,
                evidence="net flow phrasing",
                when=PatternWhen(
                    any=[
                        PatternWhen(label_contains=["cumulative net"]),
                        PatternWhen(label_in=["net cf", "net cashflow", "net cash flow"]),
                        PatternWhen(label_tokens=["net", "flow"]),
                    ],
                    unless=PatternWhen(label_contains=["present", "financing"]),
                ),
            )
        ],
    )
)

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


def test_normalize_keeps_metric_acronyms_and_splits_cashflow() -> None:
    assert normalize_label("Operating Income or Loss (EBITDA)") == (
        "operating income or loss ebitda"
    )
    assert normalize_label("Cashflow available for debt service (CFADS)") == (
        "cash flow available for debt service cfads"
    )


def test_normalize_keeps_qualifiers_and_unclosed_parens() -> None:
    assert normalize_label("Payroll (lumpy") == "payroll lumpy"
    assert normalize_label("Marketing (fixed") == "marketing fixed"
    assert normalize_label("IT & Telecom") == "it and telecom"
    assert normalize_label("Total Cash in/Cash out") == "total cash in cash out"


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
    assert doc.rows[0].source in {"embed", "rule"}
    assert chat.calls == 0


def test_embed_maps_when_label_is_not_lexical() -> None:
    chat = FakeChat("bs.equity")
    layout = _layout(LayoutRow(row=2, label="Mystery line"))
    vecs = {**VECS, "mystery line": [1.0, 0.0, 0.0]}
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=FakeEmbed(vecs),
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
    layout = _layout(LayoutRow(row=2, label="Mystery line"))
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
    layout = _layout(LayoutRow(row=2, label="Mystery line"))
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
    layout = _layout(LayoutRow(row=2, label="Mystery line"))
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
    assert "unknown" in blob
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


def test_opening_ar_maps_by_rule() -> None:
    chat = FakeChat("cf.drawdown")
    layout = _layout(LayoutRow(row=2, label="Opening AR"), sheet="Working_Capital")
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=FakeEmbed(VECS),
        chat=chat,
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id == "bs.ar"
    assert doc.rows[0].source == "rule"
    assert chat.calls == 0


def test_receipts_and_disbursements_not_pnl_or_ap() -> None:
    layout = _layout(
        LayoutRow(row=2, label="Receipts"),
        LayoutRow(row=3, label="Disbursements"),
        LayoutRow(row=4, label="Cumulative Net Flow"),
        LayoutRow(row=5, label="Subscription Collections"),
    )
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=None,
        chat=FakeChat("pnl.revenue"),
        slots=GrantSlots(),
        patterns=[
            LexicalPattern(
                concept="cf.net",
                score=0.93,
                evidence="net flow phrasing",
                when=PatternWhen(
                    any=[
                        PatternWhen(label_contains=["cumulative net"]),
                        PatternWhen(label_tokens=["net", "flow"]),
                    ],
                    unless=PatternWhen(label_contains=["present", "financing"]),
                ),
            )
        ],
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Receipts"] == "cf.receipts"
    assert by_label["Disbursements"] == "cf.disbursements"
    assert by_label["Cumulative Net Flow"] == "cf.net"
    assert by_label["Subscription Collections"] == "cf.receipts.subscription"


def test_section_header_is_skipped() -> None:
    chat = FakeChat("pnl.revenue")
    layout = _layout(
        LayoutRow(row=2, label="Trend Charts"),
        LayoutRow(row=3, label="Revenue"),
    )
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={("revenue", ""): "pnl.revenue"},
        embed=None,
        chat=chat,
        slots=GrantSlots(),
    )
    assert [row.label for row in doc.rows] == ["Revenue"]
    assert chat.calls == 0
