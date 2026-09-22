"""A small FAST-style project finance sheet shaped like the RVI corpus model.

Rows 6/7 carry period start/end dates, rows 8/9 the construction and operations
flags, rows 46-64 a scenario table (`Live Case` in L, `Case 1..3` in N..P), and
rows 185+ the calculations on the same ruler. Column L of a calculation row is a
lifetime total, not a period.
"""

from __future__ import annotations

from datetime import date

from finance_context.excel.a1 import index_to_col, parse_addr

SHEET = "PF"
YEARS = list(range(2024, 2032))
FIRST_COL = 13
DATE_FORMAT = "d-mmm-yy"


def serial(day: date) -> str:
    return str((day - date(1899, 12, 30)).days)


def cell(
    addr: str,
    value: object,
    *,
    formula: str | None = None,
    number_format: str | None = None,
) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": SHEET,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": None if value is None else str(value),
        "hidden": False,
        "formula_raw": formula,
        "formula_template": None,
        "unparsed": False,
        "number_format": number_format,
        "comment": None,
    }


def period_col(index: int) -> str:
    return index_to_col(FIRST_COL + index)


def series(row: int, values: list[object], *, formula: str | None = None) -> list[dict]:
    return [
        cell(f"{period_col(i)}{row}", value, formula=formula)
        for i, value in enumerate(values)
        if value is not None
    ]


def fast_cells() -> list[dict]:
    last = period_col(len(YEARS) - 1)
    out: list[dict] = [
        cell("B5", "Model timeline"),
        cell("D6", "Start of period"),
        cell("D7", "End of period"),
        cell("G7", "Start"),
        cell("H7", "End"),
        cell("L7", serial(date(2023, 12, 31)), formula="Model_start", number_format=DATE_FORMAT),
        cell("D8", "Construction"),
        cell("D9", "Operations"),
    ]
    for i, year in enumerate(YEARS):
        col = period_col(i)
        out.append(
            cell(
                f"{col}6",
                serial(date(year, 1, 1)),
                formula=f"{col}7+1",
                number_format=DATE_FORMAT,
            )
        )
        out.append(cell(f"{col}7", serial(date(year, 12, 31)), number_format=DATE_FORMAT))
    out += series(8, [1, 1, 0, 0, 0, 0, 0, 0], formula="IF(AND(M$6>=$G8,M$7<=$H8),1,0)")
    out += series(9, [0, 0, 1, 1, 1, 1, 1, 1], formula="IF(AND(M$6>=$G9,M$7<=$H9),1,0)")
    out += [
        cell("B11", "Checks"),
        cell("C12", "Integrity Checks"),
        cell("E12", "Reference"),
        cell("G12", "Result"),
        cell("D13", "Balance Sheet check"),
        cell("E13", 0, formula="Check_balance_sheet"),
        cell("G13", 0, formula="IF(E13<>0,1,0)"),
        cell("D14", "Negative cash balance check"),
        cell("E14", 0, formula="Check_negative_cash"),
        cell("G14", 0, formula="IF(E14<>0,1,0)"),
        cell("D15", "Integrity Check"),
        cell("G15", 0, formula="SUM(G13:G14)"),
        cell("B28", "Technical Inputs"),
        cell("D30", "Constants"),
        cell("D31", "Months per year"),
        cell("H31", 12),
        cell("D32", "Thousand"),
        cell("H32", 1000),
        cell("B46", "Scenario Output"),
        cell("D48", "Live Case"),
        cell("N48", 1, formula="IF(N49=Live_case,1,0)"),
        cell("O48", 0, formula="IF(O49=Live_case,1,0)"),
        cell("P48", 0, formula="IF(P49=Live_case,1,0)"),
        cell("D49", "Case Number"),
        cell("L49", "Live Case"),
        cell("N49", 1),
        cell("O49", 2, formula="N49+1"),
        cell("P49", 3, formula="O49+1"),
        cell("D50", "Net Present Value (NPV)"),
        cell("E50", "EUR'000", formula="Applied_currency & \"'000\""),
        cell("L50", 100, formula="G567"),
        cell("N50", 100),
        cell("O50", 90),
        cell("P50", 80),
        cell("D51", "Internal Rate of Return (IRR)"),
        cell("E51", "%"),
        cell("L51", 0.08, formula="G560"),
        cell("N51", 0.08),
        cell("O51", 0.07),
        cell("P51", 0.06),
        cell("B57", "Inputs Time Independent"),
        cell("D62", "Timing"),
        cell("D63", "Model start / Construction start"),
        cell("E63", "Date"),
        cell("G63", "Start"),
        cell("H63", "End"),
        cell("L63", serial(date(2023, 12, 31)), number_format=DATE_FORMAT),
        cell("D64", "Development & Construction"),
        cell("E64", "Years"),
        cell(
            "G64",
            serial(date(2024, 1, 1)),
            formula="Model_start+1",
            number_format=DATE_FORMAT,
        ),
        cell(
            "H64",
            serial(date(2025, 12, 31)),
            formula="EOMONTH(G64,24-1)",
            number_format=DATE_FORMAT,
        ),
        cell(
            "L64",
            2,
            formula="IF(ISBLANK(OFFSET($M64,0,Live_case)),$N64,OFFSET($M64,0,Live_case))",
        ),
        cell("N64", 2),
        cell("B163", "Inputs Time Dependent"),
        cell("D165", "CPI"),
        cell("E165", "%"),
    ]
    out += series(165, [0.02] * len(YEARS))
    out.append(cell("C185", "Electricity price forecast (real terms)"))
    out += [
        cell(f"{period_col(i)}185", year, formula=f"YEAR({period_col(i)}7)")
        for i, year in enumerate(YEARS)
    ]
    out += [cell("D186", "Mid case"), cell("E186", "EUR/MWh")]
    out += series(186, [56, 57, 52, 55, 51, 49, 51, 47])
    out += [
        cell("B191", "Cashflow Statement"),
        cell("D192", "Uses of funds"),
        cell("D193", "Full-wrap EPC"),
        cell("E193", "EUR'000", formula="Applied_currency & \"'000\""),
        cell("L193", -100, formula=f"SUM(M193:{last}193)"),
        cell("D196", "Share premium"),
        cell("E196", "EUR'000", formula="Applied_currency & \"'000\""),
        cell("L196", -30, formula=f"SUM(M196:{last}196)"),
        cell("D199", "Sources of funds"),
        cell("D201", "Debt"),
        cell("E201", "EUR'000", formula="Applied_currency & \"'000\""),
        cell("L201", 60, formula=f"SUM(M201:{last}201)"),
    ]
    out += series(193, [-20, -80, 0, 0, 0, 0, 0, 0])
    out += series(196, [-30, 0, 0, 0, 0, 0, 0, 0])
    out += series(201, [0, 60, 0, 0, 0, 0, 0, 0])
    out += [
        cell("D252", "Cash account"),
        cell("D253", "Cash b/f"),
        cell("D254", "Change in cash"),
        cell("D255", "Cash c/f"),
    ]
    out += series(253, [0, 0, 0, 5, 9, 12, 14, 15])
    out += series(254, [0, 0, 5, 4, 3, 2, 1, 1])
    out += series(255, [0, 0, 5, 9, 12, 14, 15, 16])
    out += [
        cell("B281", "Balance Sheet"),
        cell("C282", "Non-current assets"),
        cell("D283", "Long term assets"),
        cell("D284", "Goodwill"),
        cell("D285", "Total"),
        cell("C293", "Liabilities"),
        cell("D294", "Debt"),
        cell("E294", "EUR'000", formula="Applied_currency & \"'000\""),
    ]
    out += series(283, [20, 100, 95, 90, 85, 80, 75, 70])
    out += series(284, [3, 3, 3, 3, 3, 3, 3, 3])
    out += series(285, [23, 103, 98, 93, 88, 83, 78, 73], formula="SUM(M283:M284)")
    out += series(294, [0, -60000, -30000, "1.9099388737231493E-11", 0, 0, 0, 0])
    out += [
        cell("C559", "Internal Rate of Return (IRR)"),
        cell("D560", "Internal Rate of Return (IRR)"),
        cell("E560", "%"),
        cell("G560", 0.08, formula=f"XIRR(M562:{last}562,M7:{last}7)"),
    ]
    return out
