from __future__ import annotations

from finance_context.mapping.taxonomy import load_taxonomy


def test_taxonomy_includes_fcf_repayment_drawdown() -> None:
    ids = {c.id for c in load_taxonomy()}
    assert {
        "cf.fcf",
        "cf.repayment",
        "cf.drawdown",
        "pnl.interest_rate",
        "pnl.tax_rate",
        "cf.da",
        "bs.ppe",
        "pnl.volume",
        "pnl.price",
        "covenant.headroom",
        "val.npv",
        "val.irr",
        "val.wacc",
        "ops.headcount",
        "fx.rate",
        "cov.dscr",
        "cov.llcr",
        "cov.plcr",
        "fx.debt",
        "fx.ppe",
        "cf.equity_issue",
        "pnl.deferred_tax",
        "fx.equity",
        "bs.re_adj",
        "fx.cash",
        "cf.receipts",
        "cf.disbursements",
        "cf.net",
        "bs.nwc",
        "liq.min_cash_target",
        "cf.disbursements.payroll",
        "cov.leverage_headroom",
    } <= ids


def test_fcf_labels_include_net_cf_before_financing() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["cf.fcf"].labels}
    assert "fcf" in labels
    assert "net cf before financing" in labels


def test_tax_rate_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["pnl.tax_rate"].labels}
    assert "tax rate" in labels
    assert "ставка налога" in labels
    assert "effective tax rate" in labels
    assert "ставка" not in labels


def test_cf_da_labels_are_addback_not_generic_depreciation() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["cf.da"].labels}
    assert "d&a add-back" in labels
    assert "depreciation add-back" in labels
    assert "depreciation" not in labels
    assert "амортизация" not in labels


def test_ppe_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    labels = {label.lower() for label in by_id["bs.ppe"].labels}
    assert "ppe" in labels
    assert "fixed assets" in labels
    assert "основные средства" in labels
    assert "assets" not in labels


def test_volume_and_price_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    volume = {label.lower() for label in by_id["pnl.volume"].labels}
    price = {label.lower() for label in by_id["pnl.price"].labels}
    assert "volume" in volume
    assert "объём" in volume
    assert "price" in price
    assert "цена" in price
    assert "sales" not in volume
    assert "sales" not in price
    assert "revenue" not in volume
    assert "revenue" not in price


def test_covenant_headroom_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "covenant.headroom" in by_id
    labels = {label.lower() for label in by_id["covenant.headroom"].labels}
    assert "covenant headroom" in labels
    assert "запас по ковенанту" in labels
    assert "headroom" not in labels
    assert "dscr" not in labels


def test_npv_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "val.npv" in by_id
    labels = {label.lower() for label in by_id["val.npv"].labels}
    assert "npv" in labels
    assert "net present value" in labels
    assert "чпс" in labels
    assert "value" not in labels
    assert "irr" not in labels
    assert "wacc" not in labels


def test_irr_and_wacc_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "val.irr" in by_id
    assert "val.wacc" in by_id
    irr = {label.lower() for label in by_id["val.irr"].labels}
    wacc = {label.lower() for label in by_id["val.wacc"].labels}
    assert "irr" in irr
    assert "internal rate of return" in irr
    assert "внд" in irr
    assert "wacc" in wacc
    assert "discount rate" in wacc
    assert "ставка дисконта" in wacc
    assert "rate" not in irr
    assert "rate" not in wacc
    assert "npv" not in irr
    assert "npv" not in wacc


def test_headcount_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "ops.headcount" in by_id
    labels = {label.lower() for label in by_id["ops.headcount"].labels}
    assert "headcount" in labels
    assert "fte" in labels
    assert "численность" in labels
    assert "staff" not in labels
    assert "employees" not in labels
    assert "opex" not in labels


def test_fx_rate_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "fx.rate" in by_id
    labels = {label.lower() for label in by_id["fx.rate"].labels}
    assert "fx rate" in labels
    assert "exchange rate" in labels
    assert "курс валюты" in labels
    assert "rate" not in labels
    assert "курс" not in labels
    assert "usd/rub" not in labels


def test_dscr_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "cov.dscr" in by_id
    labels = {label.lower() for label in by_id["cov.dscr"].labels}
    assert "dscr" in labels
    assert "debt service coverage" in labels
    assert "icr" not in labels
    assert "llcr" not in labels
    assert "coverage" not in labels


def test_llcr_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "cov.llcr" in by_id
    labels = {label.lower() for label in by_id["cov.llcr"].labels}
    assert "llcr" in labels
    assert "loan life coverage" in labels
    assert "dscr" not in labels
    assert "icr" not in labels
    assert "plcr" not in labels
    assert "coverage" not in labels


def test_plcr_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "cov.plcr" in by_id
    labels = {label.lower() for label in by_id["cov.plcr"].labels}
    assert "plcr" in labels
    assert "project life coverage" in labels
    assert "dscr" not in labels
    assert "icr" not in labels
    assert "llcr" not in labels
    assert "coverage" not in labels


def test_debt_fx_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "fx.debt" in by_id
    labels = {label.lower() for label in by_id["fx.debt"].labels}
    assert "debt fx" in labels
    assert "fx on debt" in labels
    assert "переоценка долга" in labels
    assert "fx" not in labels
    assert "fx rate" not in labels
    assert "курс" not in labels
    assert "usd/rub" not in labels


def test_ppe_fx_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "fx.ppe" in by_id
    labels = {label.lower() for label in by_id["fx.ppe"].labels}
    assert "ppe fx" in labels
    assert "fx on ppe" in labels
    assert "переоценка ос" in labels
    assert "fx" not in labels
    assert "fx rate" not in labels
    assert "курс" not in labels
    assert "usd/rub" not in labels
    assert "переоценка долга" not in labels


def test_equity_issue_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "cf.equity_issue" in by_id
    labels = {label.lower() for label in by_id["cf.equity_issue"].labels}
    assert "equity issue" in labels
    assert "share issue" in labels
    assert "эмиссия" in labels
    assert "dividends" not in labels
    assert "equity" not in labels
    assert "issue" not in labels
    assert "proceeds" not in labels


def test_deferred_tax_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "pnl.deferred_tax" in by_id
    labels = {label.lower() for label in by_id["pnl.deferred_tax"].labels}
    assert "deferred tax" in labels
    assert "deferred tax expense" in labels
    assert "отложенный налог" in labels
    assert "tax" not in labels
    assert "tax rate" not in labels
    assert "налог" not in labels
    assert "deferred" not in labels


def test_equity_fx_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "fx.equity" in by_id
    labels = {label.lower() for label in by_id["fx.equity"].labels}
    assert "equity fx" in labels
    assert "fx on equity" in labels
    assert "переоценка капитала" in labels
    assert "fx" not in labels
    assert "fx rate" not in labels
    assert "курс" not in labels
    assert "usd/rub" not in labels
    assert "переоценка долга" not in labels
    assert "переоценка ос" not in labels
    assert "equity" not in labels


def test_re_adj_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "bs.re_adj" in by_id
    labels = {label.lower() for label in by_id["bs.re_adj"].labels}
    assert "re adjustment" in labels
    assert "prior period adjustment" in labels
    assert "корректировка нп" in labels
    assert "adjustment" not in labels
    assert "dividends" not in labels
    assert "equity" not in labels
    assert "retained earnings" not in labels
    assert "корректировка" not in labels


def test_cash_fx_labels_are_specific() -> None:
    by_id = {c.id: c for c in load_taxonomy()}
    assert "fx.cash" in by_id
    labels = {label.lower() for label in by_id["fx.cash"].labels}
    assert "cash fx" in labels
    assert "fx on cash" in labels
    assert "переоценка кассы" in labels
    assert "fx" not in labels
    assert "fx rate" not in labels
    assert "курс" not in labels
    assert "usd/rub" not in labels
    assert "cash" not in labels
    assert "fcf" not in labels
