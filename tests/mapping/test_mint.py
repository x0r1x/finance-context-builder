from __future__ import annotations

from pathlib import Path

from tests.helpers.policy import mint_ceiling, slot_wait
from tests.helpers.ports import FakeChat

from finance_context.mapping.induce import concept_id_for_label
from finance_context.mapping.models import (
    Concept,
    Facets,
    MappedRow,
    MappingDocument,
    TaxonomyDocument,
)
from finance_context.mapping.stage import _mint_unmatched
from finance_context.mapping.taxonomy import remember_concept


def _opex() -> Concept:
    return Concept(
        id="pnl.opex",
        labels=["Operating expenses", "OPEX"],
        statements=["pnl"],
        facets=Facets(statement="pnl", nature="flow", basis="accrual", unit="money"),
    )


def _write(path: Path, concepts: list[Concept]) -> None:
    document = TaxonomyDocument(version=2, concepts=concepts)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def _ids(path: Path) -> list[str]:
    document = TaxonomyDocument.model_validate_json(path.read_text(encoding="utf-8"))
    return [item.id for item in document.concepts]


def _row(
    label: str,
    alternatives: list[tuple[str, float]],
    *,
    concept_id: str | None = None,
    disposition: str = "abstained",
    unit: str = "money",
) -> MappedRow:
    return MappedRow(
        row_key="PF Model|9|PF Model!r7",
        sheet="PF Model",
        row=9,
        block_id="PF Model!r7",
        label=label,
        parent_label="Operating expenses",
        concept_id=concept_id,
        article_role="calculation",
        source="question",
        disposition=disposition,  # type: ignore[arg-type]
        alternatives=alternatives,
        memory_unit=unit,
        section_path=["Operational Expenditures"],
    )


def _extend(doc: MappingDocument, path: Path, taxonomy: list[Concept], chat=None) -> None:
    _mint_unmatched(
        doc,
        path,
        taxonomy,
        chat=chat,
        slots=None,
        slot_timeout_sec=slot_wait(),
        mint_score_max=mint_ceiling(),
    )


def test_mapped_section_child_is_not_extended(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    row = _row(
        "Commercial Management",
        [("pnl.opex", 0.2)],
        concept_id="pnl.opex",
        disposition="mapped",
    )
    _extend(MappingDocument(rows=[row]), path, [_opex()])
    assert _ids(path) == ["pnl.opex"]
    assert row.concept_id == "pnl.opex"


def test_high_score_and_empty_alternatives_do_not_mint(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    high = _row("Full-wrap EPC", [("cf.uses", 0.86)], unit="rate")
    empty = _row("Months per year", [])
    _extend(MappingDocument(rows=[high, empty]), path, [_opex()])
    assert _ids(path) == ["pnl.opex"]
    assert high.disposition == "abstained"
    assert empty.disposition == "abstained"


def test_weak_seed_neighbour_is_anchored_without_a_model(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    parent = _opex()
    _write(path, [parent])
    live = [parent]
    row = _row("Site insurance", [("pnl.opex", 0.2)])
    _extend(MappingDocument(rows=[row]), path, live)
    concept_id = concept_id_for_label("Site insurance", "Operating expenses", "PF Model")
    assert concept_id == "ops.site-insurance"
    assert _ids(path) == ["pnl.opex", "ops.site-insurance"]
    stored = TaxonomyDocument.model_validate_json(path.read_text(encoding="utf-8"))
    minted = stored.concepts[1]
    assert minted.broader == "pnl.opex"
    assert minted.facets.unit == "money"
    assert minted.labels == ["Site insurance"]
    assert row.concept_id == "ops.site-insurance"
    assert row.disposition == "mapped"
    assert row.source == "embed"
    assert row.score == 1.0
    assert live[-1].id == "ops.site-insurance"


def test_chat_extend_stores_one_sentence_on_the_seed_anchor(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    row = _row("Site insurance", [("pnl.opex", 0.2)])
    chat = FakeChat(
        payload={
            "action": "extend",
            "broader": "pnl.opex",
            "definition": "Insurance for the site. More than one sentence.",
        }
    )
    _extend(MappingDocument(rows=[row]), path, [_opex()], chat=chat)
    stored = TaxonomyDocument.model_validate_json(path.read_text(encoding="utf-8"))
    assert stored.concepts[1].broader == "pnl.opex"
    assert stored.concepts[1].definition == "Insurance for the site."
    assert stored.concepts[1].facets.unit == "money"
    assert row.disposition == "mapped"
    assert row.concept_id == "ops.site-insurance"


def test_nearest_non_seed_is_not_an_anchor(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    row = _row("Site insurance", [("ops.custom-line", 0.2), ("pnl.opex", 0.1)])
    _extend(MappingDocument(rows=[row]), path, [_opex()])
    assert _ids(path) == ["pnl.opex"]
    assert row.disposition == "abstained"


def test_chat_skip_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    before = path.read_text(encoding="utf-8")
    row = _row("Site insurance", [("pnl.opex", 0.2)])
    chat = FakeChat(payload={"action": "skip"})
    _extend(MappingDocument(rows=[row]), path, [_opex()], chat=chat)
    assert path.read_text(encoding="utf-8") == before
    assert row.disposition == "abstained"
    assert row.concept_id is None
    assert chat.calls == 1


def test_chat_broader_outside_the_shown_ids_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    before = path.read_text(encoding="utf-8")
    row = _row("Site insurance", [("pnl.opex", 0.2)])
    chat = FakeChat(payload={"action": "extend", "broader": "ops.not-a-seed", "definition": "No."})
    _extend(MappingDocument(rows=[row]), path, [_opex()], chat=chat)
    assert path.read_text(encoding="utf-8") == before
    assert row.disposition == "abstained"


def test_chat_cannot_supply_a_concept_id(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    before = path.read_text(encoding="utf-8")
    row = _row("Site insurance", [("pnl.opex", 0.2)])
    chat = FakeChat(
        payload={"action": "extend", "broader": "pnl.opex", "concept_id": "evil.invented"}
    )
    _extend(MappingDocument(rows=[row]), path, [_opex()], chat=chat)
    assert path.read_text(encoding="utf-8") == before
    assert row.disposition == "abstained"


def test_unit_conflict_does_not_mint(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    row = _row("Site insurance", [("pnl.opex", 0.2)], unit="rate")
    _extend(MappingDocument(rows=[row]), path, [_opex()])
    assert _ids(path) == ["pnl.opex"]
    assert row.disposition == "abstained"


def test_existing_id_is_reused(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    existing = Concept(
        id="ops.site-insurance",
        labels=["Site insurance"],
        broader="pnl.opex",
        statements=["pnl"],
        facets=Facets(statement="pnl", unit="money"),
    )
    _write(path, [_opex(), existing])
    row = _row("Site insurance", [("pnl.opex", 0.2)])
    _extend(MappingDocument(rows=[row]), path, [_opex(), existing])
    assert _ids(path) == ["pnl.opex", "ops.site-insurance"]
    assert row.concept_id == "ops.site-insurance"
    assert row.disposition == "mapped"


def test_remember_concept_rejects_a_missing_anchor(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    _write(path, [_opex()])
    before = path.read_text(encoding="utf-8")
    stored = remember_concept(
        path,
        Concept(id="ops.orphan", labels=["Orphan"], statements=["ops"]),
    )
    conflict = remember_concept(
        path,
        Concept(
            id="ops.rate-line",
            labels=["Rate line"],
            broader="pnl.opex",
            facets=Facets(unit="rate"),
        ),
    )
    assert stored is None
    assert conflict is None
    assert path.read_text(encoding="utf-8") == before
