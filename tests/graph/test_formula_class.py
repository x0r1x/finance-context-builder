from finance_context.graph.formula_class import classify_formula


def test_formula_class_prefers_one_financial_shape() -> None:
    assert classify_formula("=IF(A1=1,B1,0)", ["S!A1", "S!B1"], ["same", "same"]) == "conditional"
    assert classify_formula("=SUM(C9:C12)", ["S!C9:C12"], ["same"]) == "aggregation"
    assert classify_formula("=B10", ["S!B10"], ["-1"]) == "rollforward"
    assert classify_formula("=RC[-1]") == "rollforward"
    assert classify_formula("=B10*(1+C5)", ["S!B10", "S!C5"], ["-1", "same"]) == "cross_period"
    assert classify_formula("=Operation!C14", ["Operation!C14"], ["1"]) == "cross_period"
    assert classify_formula("=C15-C16", ["S!C15", "S!C16"], ["same", "same"]) == "same_period"
    assert classify_formula("=0.05") == "hardcoded"
    assert classify_formula(None) is None
    assert classify_formula("  ") is None
