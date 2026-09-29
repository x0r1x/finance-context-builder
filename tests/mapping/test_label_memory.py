from __future__ import annotations

from pathlib import Path

from tests.helpers.ports import FakeEmbed

from finance_context.mapping.glossary import (
    GlossarySignal,
    LabelHit,
    learned_hits,
    load_label_memory,
    merge_label_hits,
    save_label_memory,
)
from finance_context.mapping.induce import concept_id_for_label, should_mint
from finance_context.mapping.knn import concept_vectors
from finance_context.mapping.models import Concept, MappedRow, RowContext
from finance_context.mapping.normalize import memory_section, memory_unit, normalize_label
from finance_context.mapping.resolver import Resolver


def test_alias_and_definition_enter_the_concept_vector() -> None:
    embed = FakeEmbed({})
    concept_vectors(
        embed,
        [
            Concept(id="cf.uses", labels=["Uses"]),
            Concept(
                id="cf.capex",
                labels=["Capex"],
                aliases=["EPC"],
                definition="Construction cost",
                broader="cf.uses",
            ),
        ],
    )
    texts = embed.texts[0]
    assert "Capex" in texts
    assert "EPC" in texts
    assert "Construction cost" in texts
    assert "Uses" in texts


def test_same_label_and_parent_keep_one_id() -> None:
    first = concept_id_for_label("Full-wrap EPC", "Construction", "PF Model")
    second = concept_id_for_label("Full-wrap EPC", "Construction", "PF Model")
    assert first == second
    assert first is not None


def test_pnl_and_cfs_do_not_share_an_id() -> None:
    pnl = concept_id_for_label("Gross Revenues", "Revenue", "P&L")
    cfs = concept_id_for_label("Gross Revenues", "Cash flow", "CFS")
    assert pnl is not None and cfs is not None
    assert pnl != cfs
    assert pnl.startswith("pnl.")
    assert cfs.startswith("cf.")


def test_parameters_and_weak_scores_do_not_mint() -> None:
    assert concept_id_for_label("Months per year", "Technical inputs", "PF Model") is None
    assert concept_id_for_label("Thousand", "Technical inputs", "PF Model") is None
    assert concept_id_for_label("On", "Flags", "PF Model") is None
    assert concept_id_for_label("Off", "Flags", "PF Model") is None
    assert concept_id_for_label("Total", "Assets", "Balance Sheet") is None
    assert should_mint(0.6) is False
    assert should_mint(None) is False
    assert should_mint(0.2) is True


def _mapped(
    label: str,
    concept: str | None,
    *,
    parent: str | None = None,
    section: list[str] | None = None,
    unit: str = "",
    source: str = "rule",
    confidence: str = "high",
    disposition: str = "mapped",
    score: float | None = None,
    row: int = 1,
) -> MappedRow:
    return MappedRow(
        row_key=f"S|{row}|S!r1",
        sheet="S",
        row=row,
        block_id="S!r1",
        label=label,
        parent_label=parent,
        concept_id=concept,
        article_role="database_like",
        source=source,  # type: ignore[arg-type]
        score=score,
        confidence=confidence,  # type: ignore[arg-type]
        disposition=disposition,  # type: ignore[arg-type]
        section_path=list(section or []),
        memory_unit=unit,
    )


def test_higher_score_replaces_lower(tmp_path: Path) -> None:
    path = tmp_path / "label_memory.json"
    key = ("full-wrap epc", "construction", "money")
    save_label_memory(path, {key: LabelHit("cf.uses", 0.8, "embed")})
    save_label_memory(path, {key: LabelHit("cf.capex", 0.9, "embed")})
    save_label_memory(path, {key: LabelHit("cf.opex", 0.8, "embed")})
    loaded = load_label_memory(path)
    assert loaded[key].concept_id == "cf.capex"
    assert loaded[key].score == 0.9


def test_two_saves_keep_both_keys(tmp_path: Path) -> None:
    path = tmp_path / "label_memory.json"
    save_label_memory(path, {("opening cash", "", ""): LabelHit("bs.cash", 1.0, "rule")})
    save_label_memory(path, {("revenue", "sales", "money"): LabelHit("pnl.revenue", 0.9, "embed")})
    loaded = load_label_memory(path)
    assert loaded[("opening cash", "", "")].concept_id == "bs.cash"
    assert loaded[("revenue", "sales", "money")].concept_id == "pnl.revenue"


def test_embed_high_is_learned_and_chat_is_not() -> None:
    rows = [
        _mapped("Sales", "pnl.revenue", parent="Revenue", source="embed", score=0.9),
        _mapped(
            "Mystery",
            "pnl.revenue",
            source="chat",
            confidence="medium",
            row=3,
        ),
    ]
    learned, _drop = learned_hits(rows)
    assert "section_path" not in rows[0].model_dump()
    assert "memory_unit" not in rows[0].model_dump()
    assert learned[("sales", "revenue", "")].concept_id == "pnl.revenue"
    assert learned[("sales", "revenue", "")].score == 0.9
    assert ("mystery", "", "") not in learned


def test_higher_score_wins_between_session_and_shared() -> None:
    key = ("sales", "revenue", "money")
    shared = {key: LabelHit("pnl.revenue", 0.95, "embed")}
    merged = merge_label_hits(shared, {key: "cf.receipts"})
    assert merged[key].concept_id == "cf.receipts"
    shared_higher = {key: LabelHit("pnl.revenue", 1.1, "embed")}
    merged = merge_label_hits(shared_higher, {key: "cf.receipts"})
    assert merged[key].concept_id == "pnl.revenue"


def test_memory_unit_keeps_rate_money_years_and_blank() -> None:
    assert memory_unit("%", "money") == "rate"
    assert memory_unit("EUR'000", "money") == "money"
    assert memory_unit("years", "count") == "years"
    assert memory_unit("per year", "count") == "rate"
    assert memory_unit(None, "money") == ""
    assert memory_unit(None, "rate") == "rate"
    assert memory_unit("EUR'000", "rate") == "rate"
    assert memory_unit("Index", "ratio") == ""
    assert memory_unit("MWh p.a.", "count") == ""


def test_cpi_sections_stay_two_pairs() -> None:
    rows = [
        _mapped(
            "CPI",
            "ops.inflation",
            parent="Inflation profiles (annually)",
            section=["Inputs", "Inflation profiles (annually)"],
            unit="rate",
        ),
        _mapped(
            "CPI",
            "ops.cpi",
            parent="Inflation profiles (annually)",
            section=["Inputs", "Inflation profiles (annually)", "Indexation"],
            unit="",
            row=2,
        ),
    ]
    learned, _drop = learned_hits(rows)
    assert learned[_key("CPI", ["Inflation profiles (annually)"], "rate")].concept_id == (
        "ops.inflation"
    )
    assert learned[_key("CPI", ["Indexation"], "")].concept_id == "ops.cpi"


def test_balance_brought_forward_splits_on_section() -> None:
    rows = [
        _mapped(
            "Balance b/f",
            "bs.equity",
            parent="Funding",
            section=["Uses", "Funding", "Equity"],
            unit="money",
        ),
        _mapped(
            "Balance b/f",
            "bs.debt",
            parent="Funding",
            section=["Uses", "Funding", "Debt"],
            unit="money",
            row=2,
        ),
    ]
    learned, _drop = learned_hits(rows)
    assert learned[_key("Balance b/f", ["Equity"], "money")].concept_id == "bs.equity"
    assert learned[_key("Balance b/f", ["Debt"], "money")].concept_id == "bs.debt"


def test_upfront_fee_splits_on_unit_inside_one_section() -> None:
    section = ["Senior Debt", "Linear repayment", "Senior debt tranche"]
    rows = [
        _mapped(
            "Up-front fee",
            "debt.upfront_fee_rate",
            parent="Linear repayment",
            section=section,
            unit="rate",
        ),
        _mapped(
            "Up-front fee",
            "debt.commitment_fee",
            parent="Linear repayment",
            section=section,
            unit="money",
            row=2,
        ),
    ]
    learned, _drop = learned_hits(rows)
    assert learned[_key("Up-front fee", section, "rate")].concept_id == "debt.upfront_fee_rate"
    assert learned[_key("Up-front fee", section, "money")].concept_id == "debt.commitment_fee"


def test_full_wrap_epc_money_is_kept_when_the_rate_row_abstains() -> None:
    rows = [
        _mapped(
            "Full-wrap EPC",
            "cf.capex",
            parent="Construction & development cost",
            section=["Inputs", "Construction & development cost", "Capital Expenditures (Capex)"],
            unit="money",
        ),
        _mapped(
            "Full-wrap EPC",
            None,
            parent="Construction & development cost",
            section=["Inputs", "Construction & development cost"],
            unit="rate",
            source="question",
            confidence="low",
            disposition="abstained",
            row=2,
        ),
    ]
    learned, drop = learned_hits(rows)
    money = _key(
        "Full-wrap EPC",
        ["Capital Expenditures (Capex)"],
        "money",
    )
    rate = _key("Full-wrap EPC", ["Construction & development cost"], "rate")
    assert learned[money].concept_id == "cf.capex"
    assert rate not in learned
    assert rate in drop
    coarse = (
        normalize_label("Full-wrap EPC"),
        normalize_label("Construction & development cost"),
    )
    assert coarse in drop


def test_disputed_triple_is_not_written_and_removes_the_old_record(tmp_path: Path) -> None:
    path = tmp_path / "label_memory.json"
    disputed = ("fee", "terms", "money")
    other = ("revenue", "sales", "money")
    save_label_memory(
        path,
        {
            disputed: LabelHit("pnl.opex", 1.0, "rule"),
            other: LabelHit("pnl.revenue", 1.0, "rule"),
        },
    )
    rows = [
        _mapped("Fee", "pnl.opex", parent="Terms", section=["Terms"], unit="money"),
        _mapped("Fee", "cf.opex_paid", parent="Terms", section=["Terms"], unit="money", row=2),
    ]
    learned, drop = learned_hits(rows)
    assert disputed not in learned
    save_label_memory(path, learned, drop)
    loaded = load_label_memory(path)
    assert disputed not in loaded
    assert loaded[other].concept_id == "pnl.revenue"


def test_legacy_parent_key_is_not_loaded_as_a_triple_and_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "label_memory.json"
    path.write_text(
        '{"entries": [{"label": "cpi", "parent": "inflation profiles", '
        '"concept_id": "ops.inflation", "score": 1.0, "source": "rule"}]}',
        encoding="utf-8",
    )
    assert load_label_memory(path) == {}
    rows = [
        _mapped(
            "CPI",
            "ops.inflation",
            parent="Inflation profiles",
            section=["Inflation profiles"],
            unit="rate",
        )
    ]
    learned, drop = learned_hits(rows)
    save_label_memory(path, learned, drop)
    loaded = load_label_memory(path)
    assert loaded[_key("CPI", ["Inflation profiles"], "rate")].concept_id == "ops.inflation"
    raw = path.read_text(encoding="utf-8")
    assert '"parent"' not in raw


def test_pnl_and_cfs_sections_stay_different_pairs() -> None:
    rows = [
        _mapped(
            "Gross Revenues",
            "pnl.revenue",
            parent="Revenue",
            section=["Profit & Loss", "Revenue"],
            unit="money",
        ),
        _mapped(
            "Gross Revenues",
            "cf.receipts",
            parent="Receipts",
            section=["Cash flow statement", "Receipts"],
            unit="money",
            row=2,
        ),
    ]
    learned, _drop = learned_hits(rows)
    assert learned[_key("Gross Revenues", ["Revenue"], "money")].concept_id == "pnl.revenue"
    assert learned[_key("Gross Revenues", ["Receipts"], "money")].concept_id == "cf.receipts"


def test_section_class_is_not_stored() -> None:
    rows = [
        _mapped(
            "Other Income",
            "cf.receipts.other",
            parent="CASH INFLOWS (PRORATED FROM MONTHLY COLLECTIONS",
            section=["CASH INFLOWS (PRORATED FROM MONTHLY COLLECTIONS"],
            unit="money",
        )
    ]
    learned, drop = learned_hits(rows)
    assert list(learned) == [
        _key("Other Income", ["CASH INFLOWS (PRORATED FROM MONTHLY COLLECTIONS"], "money")
    ]
    assert (normalize_label("Other Income"), "cash inflows") in drop


def test_triple_beats_a_coarse_session_pair_and_still_meets_facets() -> None:
    from finance_context.layout.models import (
        Axis,
        AxisHeader,
        Block,
        Layout,
        LayoutRow,
        SheetLayout,
    )
    from finance_context.mapping.structure import BookView

    layout = Layout(
        sheets=[
            SheetLayout(
                name="PF",
                blocks=[
                    Block(
                        block_id="PF!r1",
                        label_col=1,
                        axis=Axis(
                            id="a",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="2026", role="forecast", period_key="2026")
                            ],
                        ),
                        rows=[LayoutRow(row=2, label="Full-wrap EPC")],
                    )
                ],
            )
        ]
    )
    concept = Concept(id="cf.capex", labels=["Capex"], value_kind="money")
    uses = Concept(id="cf.uses", labels=["Uses"], value_kind="money")
    book = BookView(layout, [], [concept, uses])
    section = ["Capital Expenditures (Capex)"]
    signal = GlossarySignal(
        {(normalize_label("Full-wrap EPC"), "construction and development cost"): "cf.uses"},
        label_memory={_key("Full-wrap EPC", section, "money"): "cf.capex"},
    )
    ctx = RowContext(
        row_key="k",
        sheet="PF",
        row=2,
        block_id="PF!r1",
        label="Full-wrap EPC",
        parent_label="Construction & development cost",
        section_path=section,
        memory_unit="money",
        value_kind="money",
    )
    proposed = signal.propose(ctx, book)
    assert proposed[0].concept_id == "cf.capex"
    assert proposed[0].score == 1.0
    rate = ctx.model_copy(update={"value_kind": "rate", "memory_unit": "rate"})
    assert signal.propose(rate, book)[0].concept_id == "cf.uses"
    assert Resolver([concept, uses]).fuse(ctx, proposed) == proposed
    assert Resolver([concept, uses]).fuse(rate, proposed) == []


def _key(label: str, section_path: list[str], unit: str) -> tuple[str, str, str]:
    return (normalize_label(label), memory_section(section_path, None), unit)
