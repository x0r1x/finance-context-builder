"""Time semantics on the public corpus. `scripts/fetch-corpus.py` downloads the books."""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_context.app.pipeline import Pipeline
from finance_context.models.context import BlockRow
from finance_context.settings import Settings

_CORPUS = Path(__file__).resolve().parents[2] / "resources" / "corpus"


def _rows(tmp_path: Path, name: str) -> dict[str, BlockRow]:
    workbook = _CORPUS / name
    if not workbook.is_file():
        pytest.skip(f"missing {workbook}; run scripts/fetch-corpus.py")
    dest = tmp_path / workbook.stem
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(workbook.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    doc = pipeline.run(dest, job_id=workbook.stem, source_filename=workbook.name)
    return {row.row_key: row for block in doc.blocks for row in block.rows}


def _time(row: BlockRow) -> tuple[str | None, str | None]:
    return row.hints.time_semantics, row.aggregation


@pytest.mark.e2e_models
def test_packt_cross_sheet_previous_column_is_not_a_roll_forward(tmp_path: Path) -> None:
    rows = _rows(tmp_path, "packt-project-finance.xlsx")
    assert _time(rows["CFS|8|CFS!r3"]) == ("flow", "sum")  # Gross Revenues
    assert _time(rows["CFS|14|CFS!r3"]) == ("flow", "sum")  # CFADS
    assert _time(rows["P&L|13|P&L!r2"]) == ("flow", "sum")  # EBITDA
    assert _time(rows["P&L|15|P&L!r2"]) == ("flow", "sum")  # Amortization
    assert _time(rows["Debt|8|Debt!r3"]) == ("bop", "first")
    assert _time(rows["Debt|11|Debt!r3"]) == ("eop", "last")
    assert _time(rows["Construction|33|Construction!r2"]) == ("bop", "first")
    assert _time(rows["Construction|35|Construction!r2"]) == ("eop", "last")


@pytest.mark.e2e_models
def test_packt_balance_sheet_rows_are_stocks(tmp_path: Path) -> None:
    rows = _rows(tmp_path, "packt-project-finance.xlsx")
    for row in (8, 9, 13, 14, 15, 16):
        key = f"Balance Sheet|{row}|Balance Sheet!r3"
        assert _time(rows[key]) == ("stock", "last"), key
    assert rows["Balance Sheet|9|Balance Sheet!r3"].period_position == "end"


@pytest.mark.e2e_models
def test_rvi_prices_and_coverage_ratios_are_not_summed(tmp_path: Path) -> None:
    rows = _rows(tmp_path, "rvi-project-finance.xlsx")
    assert _time(rows["PF Model|356|PF Model!r185"]) == ("rate", "none")  # Merchant price
    assert _time(rows["PF Model|363|PF Model!r185"]) == ("rate", "none")  # PPA price
    assert _time(rows["PF Model|537|PF Model!r185"]) == ("rate", "none")  # DSCR, x
    assert _time(rows["PF Model|357|PF Model!r185"]) == ("stock", "last")  # escalation index
    assert _time(rows["PF Model|332|PF Model!r185"]) == ("bop", "first")  # beginning of period
    assert _time(rows["PF Model|288|PF Model!r185"]) == ("stock", "last")  # Balance Sheet cash
    assert _time(rows["PF Model|462|PF Model!r185"]) == ("flow", "sum")  # total opex
