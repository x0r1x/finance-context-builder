from finance_context.graph.formula_class import classify_formula, classify_formula_bits, note_lag


def test_formula_class_prefers_one_financial_shape() -> None:
    assert classify_formula("=IF(A1=1,B1,0)", ["S!A1", "S!B1"], ["same", "same"]) == "conditional"
    assert classify_formula("=SUM(C9:C12)", ["S!C9:C12"], ["same"]) == "aggregation"
    assert classify_formula("=B10", ["S!B10"], ["-1"]) == "rollforward"
    assert classify_formula("=B10", ["S!B10"], ["unaligned"]) == "same_period"
    assert classify_formula("=B10", ["S!B10"], ["unaligned", "-1"]) == "rollforward"
    assert classify_formula("=RC[-1]", ["S!B2"], ["-1"]) == "rollforward"
    assert classify_formula("=RC[-1]", ["S!B2"], ["same"]) == "same_period"
    assert classify_formula("=RC[-1]", ["S!B2"], []) == "same_period"
    assert classify_formula("=RC[-1]", ["S!B2"], None) == "same_period"
    assert classify_formula("=RC[-1]") == "hardcoded"
    assert classify_formula("=R[-4]C[0]", ["S!A1"], []) == "same_period"
    assert classify_formula("=B10*(1+C5)", ["S!B10", "S!C5"], ["-1", "same"]) == "cross_period"
    assert classify_formula("=Operation!C14", ["Operation!C14"], ["1"]) == "cross_period"
    assert classify_formula("=C15-C16", ["S!C15", "S!C16"], ["same", "same"]) == "same_period"
    assert classify_formula("=B10") == "same_period"
    assert classify_formula("=0.05") == "hardcoded"
    assert classify_formula(None) is None
    assert classify_formula("  ") is None


def test_lag_bits_classify_like_the_lag_list() -> None:
    samples = [
        None,
        [],
        ["same"],
        ["unaligned"],
        ["0"],
        ["-1"],
        ["-1", "-1"],
        ["-2"],
        ["-1", "-2"],
        ["1"],
        ["1", "5"],
        ["-1", "1"],
        ["-1", "-5", "1"],
        ["same", "-1"],
        ["12"],
        ["-12"],
        ["nope"],
        ["", "same", "-1"],
    ]
    formulas = ["=B10", "=B10*(1+C5)", "=SUM(A1)", "=IF(A1,B1,0)", "=0.05", None]
    for lags in samples:
        bits = 0
        for lag in lags or []:
            bits = note_lag(bits, lag)
        for formula in formulas:
            refs = ["S!B10"]
            assert classify_formula_bits(formula, refs, bits) == classify_formula(
                formula, refs, lags
            )
