from __future__ import annotations

from pathlib import Path

from finance_context.mapping.glossary import (
    GlossarySignal,
    learn_from_rows,
    load_glossary,
    save_glossary,
)
from finance_context.mapping.models import MappedRow


def test_glossary_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "glossary.json"
    save_glossary(path, {("opening cash", ""): "bs.cash"})
    loaded = load_glossary(path)
    assert loaded[("opening cash", "")] == "bs.cash"


def test_learn_from_high_confidence_rows() -> None:
    rows = [
        MappedRow(
            row_key="a",
            sheet="S",
            row=2,
            block_id="S!r1",
            label="Opening cash",
            concept_id="bs.cash",
            article_role="database_like",
            source="rule",
            confidence="high",
        ),
        MappedRow(
            row_key="b",
            sheet="S",
            row=3,
            block_id="S!r1",
            label="Mystery",
            concept_id="pnl.revenue",
            article_role="database_like",
            source="chat",
            confidence="medium",
        ),
    ]
    learned = learn_from_rows({}, rows)
    assert learned[("opening cash", "")] == "bs.cash"
    assert ("mystery", "") not in learned


def test_glossary_truncated_parent_uses_section_class() -> None:
    from finance_context.layout.models import (
        Axis,
        AxisHeader,
        Block,
        Layout,
        LayoutRow,
        SheetLayout,
    )
    from finance_context.mapping.models import Concept, RowContext
    from finance_context.mapping.structure import BookView

    layout = Layout(
        sheets=[
            SheetLayout(
                name="Weekly_Forecast",
                blocks=[
                    Block(
                        block_id="Weekly_Forecast!r1",
                        label_col=1,
                        axis=Axis(
                            id="a",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2, text="W1", role="forecast", period_key="2026-01-11"
                                )
                            ],
                        ),
                        rows=[LayoutRow(row=2, label="Other Income")],
                    )
                ],
            )
        ]
    )
    book = BookView(layout, [], [Concept(id="cf.receipts.other", labels=["Other Income"])])
    signal = GlossarySignal({("other income", "cash inflows"): "cf.receipts.other"})
    ctx = RowContext(
        row_key="k",
        sheet="Weekly_Forecast",
        row=2,
        block_id="Weekly_Forecast!r1",
        label="Other Income",
        parent_label="CASH INFLOWS (PRORATED FROM MONTHLY COLLECTIONS",
    )
    proposed = signal.propose(ctx, book)
    assert proposed[0].concept_id == "cf.receipts.other"
