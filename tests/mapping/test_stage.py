from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.ports import FakeChat, FakeEmbed, GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping import mapping_workbook
from finance_context.store.fs import write_json


def test_skip_existing_mapping_does_not_call_ports(dest: Path) -> None:
    payload = {
        "rows": [
            {
                "row_key": "P&L|2|P&L!r1",
                "sheet": "P&L",
                "row": 2,
                "block_id": "P&L!r1",
                "label": "Revenue",
                "parent_label": None,
                "concept_id": "pnl.revenue",
                "article_role": "calculation",
                "source": "glossary",
            }
        ],
        "questions": [],
    }
    (dest / "mapping.json").write_text(json.dumps(payload), encoding="utf-8")
    embed = FakeEmbed()
    chat = FakeChat()
    doc = mapping_workbook(dest, embed=embed, chat=chat, slots=GrantSlots())
    assert embed.calls == 0
    assert chat.calls == 0
    assert doc.rows[0].concept_id == "pnl.revenue"


def test_mapping_writes_json(dest: Path) -> None:
    layout = Layout(
        sheets=[
            SheetLayout(
                name="P&L",
                blocks=[
                    Block(
                        block_id="P&L!r1",
                        label_col=1,
                        axis=Axis(
                            id="P&L!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2, text="2023", role="historical", period_key="2023"
                                )
                            ],
                        ),
                        rows=[LayoutRow(row=2, label="Revenue")],
                    )
                ],
            )
        ]
    )
    write_json(dest / "layout.json", layout.model_dump(mode="json"))
    glossary = {("revenue", ""): "pnl.revenue"}
    mapping_workbook(
        dest,
        embed=FakeEmbed(),
        chat=None,
        slots=GrantSlots(),
        glossary=glossary,
    )
    assert (dest / "mapping.json").is_file()
    data = json.loads((dest / "mapping.json").read_text(encoding="utf-8"))
    assert data["rows"][0]["concept_id"] == "pnl.revenue"
