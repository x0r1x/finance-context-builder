from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.policy import default_thresholds, llm_workers, slot_wait, thresholds
from tests.helpers.ports import FakeChat, FakeEmbed, GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping import mapping_workbook
from finance_context.settings import Settings
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
    doc = mapping_workbook(
        dest,
        embed=embed,
        chat=chat,
        slots=GrantSlots(),
        thresholds=thresholds(),
        default_thresholds=default_thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
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
        thresholds=thresholds(),
        default_thresholds=default_thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    assert (dest / "mapping.json").is_file()
    data = json.loads((dest / "mapping.json").read_text(encoding="utf-8"))
    assert data["rows"][0]["concept_id"] == "pnl.revenue"
    assert data["thresholds"] == thresholds().model_dump(mode="json")


def _revenue_layout() -> Layout:
    return Layout(
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


def _stale_row(concept_id: str) -> dict[str, object]:
    return {
        "row_key": "P&L|2|P&L!r1",
        "sheet": "P&L",
        "row": 2,
        "block_id": "P&L!r1",
        "label": "Revenue",
        "parent_label": None,
        "concept_id": concept_id,
        "article_role": "calculation",
        "source": "glossary",
    }


def _run(dest: Path, *, current: Settings | None = None, glossary: dict | None = None):
    settings = current or Settings(_env_file=None)
    return mapping_workbook(
        dest,
        embed=FakeEmbed(),
        chat=None,
        slots=GrantSlots(),
        glossary=glossary,
        thresholds=settings.mapping_thresholds(),
        default_thresholds=default_thresholds(),
        slot_timeout_sec=settings.llm_slot_wait_sec,
        llm_concurrency=settings.llm_concurrency,
    )


def test_changed_threshold_remaps_an_unstamped_file(dest: Path) -> None:
    write_json(dest / "layout.json", _revenue_layout().model_dump(mode="json"))
    (dest / "mapping.json").write_text(
        json.dumps({"rows": [_stale_row("stale.keep")], "questions": []}),
        encoding="utf-8",
    )
    custom = Settings(_env_file=None, concept_accept_min=0.91)
    doc = _run(dest, current=custom, glossary={("revenue", ""): "pnl.revenue"})
    assert doc.rows[0].concept_id == "pnl.revenue"
    saved = json.loads((dest / "mapping.json").read_text(encoding="utf-8"))
    assert saved["thresholds"] == custom.mapping_thresholds().model_dump(mode="json")


def test_matching_stamp_keeps_the_cached_mapping(dest: Path) -> None:
    payload = {
        "rows": [_stale_row("stale.keep")],
        "questions": [],
        "thresholds": thresholds().model_dump(mode="json"),
    }
    (dest / "mapping.json").write_text(json.dumps(payload), encoding="utf-8")
    doc = _run(dest)
    assert doc.rows[0].concept_id == "stale.keep"


def test_different_stamp_remaps(dest: Path) -> None:
    write_json(dest / "layout.json", _revenue_layout().model_dump(mode="json"))
    stamped = Settings(_env_file=None, concept_accept_min=0.91).mapping_thresholds()
    payload = {
        "rows": [_stale_row("stale.keep")],
        "questions": [],
        "thresholds": stamped.model_dump(mode="json"),
    }
    (dest / "mapping.json").write_text(json.dumps(payload), encoding="utf-8")
    doc = _run(dest, glossary={("revenue", ""): "pnl.revenue"})
    assert doc.rows[0].concept_id == "pnl.revenue"
