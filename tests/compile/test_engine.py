from __future__ import annotations

from finance_context.formulas.engine import FormulaEngine


def test_ru_semicolon_is_argument_separator_not_replaced() -> None:
    engine = FormulaEngine(locale_hint="ru")
    parsed = engine.parse("=СУММ(1,5;A1)", sheet="Sheet1", addr="B1")
    assert parsed.unparsed is False
    assert parsed.ast is not None
    assert parsed.ast["op"] == "func"
    assert len(parsed.ast["args"]) == 2
    assert parsed.ast["args"][0] == {"op": "num", "value": 1.5}
    assert parsed.ast["args"][1]["op"] == "ref"
    assert parsed.ast["args"][1]["col"] == 1
    assert parsed.ast["args"][1]["row"] == 1


def test_en_comma_is_argument_separator() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM(1.5,A1)", sheet="Sheet1", addr="B1")
    assert parsed.unparsed is False
    assert parsed.ast is not None
    assert len(parsed.ast["args"]) == 2
    assert parsed.ast["args"][0] == {"op": "num", "value": 1.5}


def test_same_sheet_ref_edge() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=A1", sheet="Sheet1", addr="B1")
    kinds = {e.kind for e in parsed.edges}
    assert kinds == {"ref"}
    assert parsed.edges[0].source == "Sheet1!B1"
    assert parsed.edges[0].target == "Sheet1!A1"
    assert parsed.edges[0].unresolved is False


def test_range_edge() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM(A1:B2)", sheet="Sheet1", addr="C1")
    assert any(e.kind == "range" and e.target == "Sheet1!A1:B2" for e in parsed.edges)


def test_range_with_sheet_qualifier_on_right_end() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM(TBA!$D$10:'TBA'!D10)", sheet="Operation", addr="E7")
    assert parsed.unparsed is False
    assert any(
        e.kind == "range" and e.target == "TBA!D10:D10" for e in parsed.edges
    )


def test_cross_sheet_ref_becomes_r1c1_relative_to_origin() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=Inputs!D5", sheet="P&L", addr="D24")
    assert parsed.unparsed is False
    assert parsed.template == "=Inputs!R[-19]C[0]"
    assert any(e.kind == "cross_sheet" and e.target == "Inputs!D5" for e in parsed.edges)


def test_absolute_ref_r1c1() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=$D$5", sheet="Sheet1", addr="D24")
    assert parsed.template == "=R5C4"


def test_indirect_is_dynamic_unresolved() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse('=INDIRECT("A1")', sheet="Sheet1", addr="B1")
    assert parsed.unparsed is False
    assert any(e.kind == "dynamic" and e.unresolved for e in parsed.edges)
    assert not any(e.kind == "ref" for e in parsed.edges)


def test_offset_is_dynamic_unresolved() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=OFFSET(A1,1,0)", sheet="Sheet1", addr="B1")
    assert any(e.kind == "dynamic" and e.unresolved for e in parsed.edges)


def test_external_ref_edge() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=[1]Sheet1!A1", sheet="Sheet1", addr="B1")
    assert any(e.kind == "external" for e in parsed.edges)


def test_named_range_is_parsed() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=MAX(DS_Drawn_C:DS_Drawn_N)", sheet="Cover", addr="C20")
    assert parsed.unparsed is False
    assert parsed.template == "=MAX(DS_Drawn_C:DS_Drawn_N)"
    assert any(e.kind == "range" and e.target == "DS_Drawn_C:DS_Drawn_N" for e in parsed.edges)


def test_template_keeps_parens_around_lower_precedence() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=(1+Sub_Growth_M)^(H7-1)", sheet="Cover", addr="H11")
    assert parsed.unparsed is False
    assert parsed.template is not None
    assert "(1+Sub_Growth_M)" in parsed.template
    assert "(R[-4]C[0]-1)" in parsed.template


def test_ppmt_walks_all_cell_args() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=PPMT(B1,B2,B3,B4)", sheet="Debt", addr="C10")
    assert parsed.unparsed is False
    targets = {e.target for e in parsed.edges if e.kind == "ref"}
    assert targets == {"Debt!B1", "Debt!B2", "Debt!B3", "Debt!B4"}


def test_xlfn_ppmt_is_canonical_and_collects_refs() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=_xlfn.PPMT(B1,C1,D1,E1)", sheet="Debt", addr="F1")
    assert parsed.unparsed is False
    assert parsed.ast is not None
    assert parsed.ast["name"] == "PPMT"
    targets = {e.target for e in parsed.edges if e.kind == "ref"}
    assert targets == {"Debt!B1", "Debt!C1", "Debt!D1", "Debt!E1"}
    assert not any(e.kind == "dynamic" for e in parsed.edges)


def test_xlfn_indirect_stays_dynamic() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse('=_xlfn.INDIRECT("A1")', sheet="Sheet1", addr="B1")
    assert parsed.unparsed is False
    assert any(e.kind == "dynamic" and e.unresolved for e in parsed.edges)
    assert not any(e.kind == "ref" for e in parsed.edges)


def test_if_and_index_walk_nested_refs() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=IF(A1>0,INDEX(B1:D1,C2),SUM(E1,F1))", sheet="P&L", addr="G1")
    assert parsed.unparsed is False
    kinds = {(e.kind, e.target) for e in parsed.edges}
    assert ("ref", "P&L!A1") in kinds
    assert ("range", "P&L!B1:D1") in kinds
    assert ("ref", "P&L!C2") in kinds
    assert ("ref", "P&L!E1") in kinds
    assert ("ref", "P&L!F1") in kinds


def test_garbage_formula_is_unparsed() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=(((oops", sheet="Sheet1", addr="A1")
    assert parsed.unparsed is True
    assert parsed.ast is None
