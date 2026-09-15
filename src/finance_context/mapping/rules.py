from __future__ import annotations

import re

from finance_context.mapping.normalize import normalize_label

_NOISE_EXACT = {
    "helper",
    "helpers",
    "trend charts",
    "liquidity bridge",
    "cover",
    "dashboard",
    "assumptions",
    "notes",
    "chart",
    "charts",
    "bridge",
}
_NOISE_TAILS = (" charts", " chart", " bridge")
_TOKEN = re.compile(r"[a-z0-9]+")


def is_noise_label(label: str | None) -> bool:
    n = normalize_label(label)
    if not n:
        return True
    if n in _NOISE_EXACT:
        return True
    return any(n.endswith(tail) for tail in _NOISE_TAILS)


def rule_concept(label: str | None, concept_ids: set[str]) -> str | None:
    n = normalize_label(label)
    if not n or is_noise_label(n):
        return None
    tokens = set(_TOKEN.findall(n))
    picked: str | None = None
    if _is_ar(n, tokens):
        picked = "bs.ar"
    elif _is_ap(n, tokens):
        picked = "bs.ap"
    elif _is_cash_balance(n, tokens):
        picked = "bs.cash"
    elif _is_receipts(n, tokens):
        picked = "cf.receipts"
    elif _is_disbursements(n, tokens):
        picked = "cf.disbursements"
    elif _is_net_flow(n, tokens):
        picked = "cf.net" if "cf.net" in concept_ids else "cf.fcf"
    elif "npv" in tokens or "net present value" in n:
        picked = "val.npv"
    if picked and picked in concept_ids:
        return picked
    return None


def _is_ar(n: str, tokens: set[str]) -> bool:
    if "receivable" in n or "receivables" in tokens or "дебитор" in n:
        return True
    return "ar" in tokens and bool(tokens & {"opening", "closing", "ending", "begin"})


def _is_ap(n: str, tokens: set[str]) -> bool:
    if "payable" in n or "payables" in tokens or "кредитор" in n:
        return True
    return "ap" in tokens and bool(tokens & {"opening", "closing", "ending", "begin"})


def _is_cash_balance(n: str, tokens: set[str]) -> bool:
    if n in {"cash", "cash balance", "cash on hand"}:
        return True
    if "cash" in tokens and tokens & {"opening", "closing", "ending", "begin"}:
        return True
    return "денежн" in n and ("начал" in n or "конеч" in n)


def _is_receipts(n: str, tokens: set[str]) -> bool:
    if tokens & {"receipt", "receipts", "collections", "collection"}:
        return True
    return "поступлен" in n


def _is_disbursements(n: str, tokens: set[str]) -> bool:
    if tokens & {"disbursement", "disbursements"}:
        return True
    return "выплат" in n and "дивиденд" not in n


def _is_net_flow(n: str, tokens: set[str]) -> bool:
    if "npv" in tokens or "present" in n or "financing" in n or "fcf" in tokens:
        return False
    if "cumulative net" in n or "net flow" in n:
        return True
    return n in {"net cash flow", "net cf", "net cashflow"}
