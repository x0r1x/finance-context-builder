from __future__ import annotations

import pytest

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


def test_unquoted_cyrillic_sheet_is_cross_sheet_ref() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=Допущения!C4+1", sheet="Модель", addr="D5")
    assert parsed.unparsed is False
    assert parsed.template == "=Допущения!R[-1]C[-1]+1"
    assert [(e.kind, e.target) for e in parsed.edges] == [("cross_sheet", "Допущения!C4")]


def test_unquoted_cyrillic_sheet_with_digit_is_cross_sheet_ref() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=Лист1!A1*2", sheet="Модель", addr="B2")
    assert parsed.unparsed is False
    assert [(e.kind, e.target) for e in parsed.edges] == [("cross_sheet", "Лист1!A1")]


def test_cyrillic_sheet_qualifies_both_range_ends() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM(Допущения!C4:Допущения!C6)", sheet="Модель", addr="D5")
    assert parsed.unparsed is False
    assert [(e.kind, e.target) for e in parsed.edges] == [("range", "Допущения!C4:C6")]


def test_external_book_with_cyrillic_sheet() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=[1]Допущения!A1", sheet="Модель", addr="B1")
    assert parsed.unparsed is False
    assert [e.kind for e in parsed.edges] == ["external"]


def test_bare_cells_stay_cells_next_to_unicode_sheet_names() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=A1+XFD1048576", sheet="Модель", addr="B1")
    assert parsed.unparsed is False
    assert [(e.kind, e.target) for e in parsed.edges] == [
        ("ref", "Модель!A1"),
        ("ref", "Модель!XFD1048576"),
    ]


def test_column_array_constant_is_parsed() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=INDEX({1;2;3},2)", sheet="Inputs", addr="D1")
    assert parsed.unparsed is False
    assert parsed.template == "=INDEX({1;2;3},2)"
    assert parsed.edges == []


def test_two_dimensional_array_constant_keeps_rows_and_columns() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse('=INDEX({1,-2.5;"x",TRUE},2,1)', sheet="Inputs", addr="D1")
    assert parsed.unparsed is False
    assert parsed.ast is not None
    assert parsed.ast["args"][0] == {
        "op": "array",
        "rows": [
            [{"op": "num", "value": 1.0}, {"op": "num", "value": -2.5}],
            [{"op": "str", "value": "x"}, {"op": "name", "value": "TRUE"}],
        ],
    }
    assert parsed.template == '=INDEX({1,-2.5;"x",TRUE},2,1)'


def test_array_constant_next_to_a_reference_keeps_the_reference_edge() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse('=LOOKUP(A1,{0,10},{"low","high"})', sheet="Inputs", addr="B1")
    assert parsed.unparsed is False
    assert [(e.kind, e.target) for e in parsed.edges] == [("ref", "Inputs!A1")]


def test_array_constant_rejects_references_and_ragged_rows() -> None:
    engine = FormulaEngine(locale_hint="en")
    assert engine.parse("=SUM({A1,2})", sheet="Inputs", addr="B1").unparsed is True
    assert engine.parse("=SUM({1,2;3})", sheet="Inputs", addr="B1").unparsed is True
    assert engine.parse("=SUM({1,2", sheet="Inputs", addr="B1").unparsed is True


def test_percent_literals_are_array_constants() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM({10%,-20%})", sheet="Inputs", addr="B1")
    assert parsed.unparsed is False
    assert parsed.template == "=SUM({10%,-20%})"
    lookup = engine.parse('=LOOKUP(A1,{0,10%},{"a","b"})', sheet="Inputs", addr="B1")
    assert lookup.unparsed is False
    assert [(e.kind, e.target) for e in lookup.edges] == [("ref", "Inputs!A1")]


@pytest.mark.parametrize(
    ("formula", "template", "targets"),
    [
        ("=LOG10(A1)", "=LOG10(R[-1]C[-1])", ["S!A1"]),
        ("=ATAN2(A1,B1)", "=ATAN2(R[-1]C[-1],R[-1]C[0])", ["S!A1", "S!B1"]),
        ("=DAYS360(A1,B1)", "=DAYS360(R[-1]C[-1],R[-1]C[0])", ["S!A1", "S!B1"]),
        ("=DEC2HEX(A1)", "=DEC2HEX(R[-1]C[-1])", ["S!A1"]),
    ],
)
def test_function_name_ending_in_digits_is_a_function(
    formula: str, template: str, targets: list[str]
) -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse(formula, sheet="S", addr="B2")
    assert parsed.unparsed is False
    assert parsed.template == template
    assert [(e.kind, e.target) for e in parsed.edges] == [("ref", t) for t in targets]


@pytest.mark.parametrize(
    ("formula", "name"),
    [
        ("=Rate1*2", "Rate1"),
        ("=WACC1*2", "WACC1"),
        ("=XFE1*2", "XFE1"),
        ("=A1048577*2", "A1048577"),
    ],
)
def test_name_outside_the_grid_is_a_defined_name(formula: str, name: str) -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse(formula, sheet="S", addr="B2")
    assert parsed.unparsed is False
    assert parsed.template == f"={name}*2"
    assert [(e.kind, e.target, e.named) for e in parsed.edges] == [("ref", name, True)]


def test_wide_letters_pair_is_a_named_range_not_columns() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM(Start:Finish)", sheet="S", addr="B2")
    assert parsed.unparsed is False
    assert [(e.kind, e.target, e.named) for e in parsed.edges] == [
        ("range", "Start:Finish", True)
    ]


def test_sheet_qualified_ref_error_matches_bare_ref_error() -> None:
    engine = FormulaEngine(locale_hint="en")
    bare = engine.parse("=#REF!", sheet="CF", addr="C2")
    quoted = engine.parse("='Денежные потоки'!#REF!", sheet="CF", addr="C2")
    assert bare.unparsed is False
    assert quoted.unparsed is False
    assert quoted.ast == bare.ast == {"op": "err", "value": "#REF!"}
    assert quoted.template == bare.template == "=#REF!"
    assert quoted.edges == bare.edges == []

    for formula in (
        "=Sheet1!#REF!",
        "=[1]Sheet1!#REF!",
        "='[1]Денежные потоки'!#REF!",
    ):
        parsed = engine.parse(formula, sheet="CF", addr="C2")
        assert parsed.unparsed is False, formula
        assert parsed.ast == bare.ast
        assert parsed.template == bare.template
        assert parsed.edges == []


def test_sheet_qualified_ref_error_inside_an_expression() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=A1+'Sheet 2'!#REF!*2", sheet="CF", addr="B2")
    assert parsed.unparsed is False
    assert parsed.ast == {
        "op": "bin",
        "kind": "+",
        "left": {
            "op": "ref",
            "col": 1,
            "row": 1,
            "abs_col": False,
            "abs_row": False,
        },
        "right": {
            "op": "bin",
            "kind": "*",
            "left": {"op": "err", "value": "#REF!"},
            "right": {"op": "num", "value": 2.0},
        },
    }
    assert [(edge.kind, edge.target) for edge in parsed.edges] == [("ref", "CF!A1")]


def test_sheet_qualifier_does_not_hide_other_unparseable_formulas() -> None:
    engine = FormulaEngine(locale_hint="en")
    assert engine.parse("=Sheet1!#NAME?", sheet="CF", addr="C2").unparsed is True
    assert engine.parse("=Sheet1!not_a_cell", sheet="CF", addr="C2").unparsed is True
    assert engine.parse("='Денежные потоки'!???", sheet="CF", addr="C2").unparsed is True


def test_column_range_inside_the_grid_stays_columns() -> None:
    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=SUM(A:C)+SUM($XFD:$XFD)", sheet="S", addr="B2")
    assert [(e.kind, e.target) for e in parsed.edges] == [
        ("range", "S!A:C"),
        ("range", "S!XFD:XFD"),
    ]
