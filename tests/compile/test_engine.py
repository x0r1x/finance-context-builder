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


def test_garbage_formula_is_unparsed() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=(((oops", sheet="Sheet1", addr="A1")
    assert parsed.unparsed is True
    assert parsed.ast is None
