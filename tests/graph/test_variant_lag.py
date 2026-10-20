"""Variant columns keep distinct period ids and share one calendar lag slot."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet


def _text(addr: str, value: str) -> CellSpec:
    return CellSpec(addr=addr, value=value, type="inlineStr")


def _run(
    tmp_path: Path,
    cells: list[CellSpec] | None = None,
    sheets: list[SheetSpec] | None = None,
) -> tuple[dict, list[dict]]:
    source = build_xlsx(
        tmp_path / "model.xlsx",
        sheets=sheets or [SheetSpec(name="P&L", cells=cells or [])],
    )
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None)).run(
        dest, job_id="variant-lag", source_filename="model.xlsx"
    )
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    edges = read_parquet(dest / "ir" / "graph_edges.parquet")
    return graph, edges


def _link(graph: dict, cell: str) -> dict:
    return next(item for item in graph["links"] if item["cell"] == cell)


def _lag(edges: list[dict], source: str, target: str) -> str:
    found = [edge for edge in edges if edge["source"] == source and edge["target"] == target]
    assert found, (source, target)
    return str(found[0]["period_lag"])


def test_fact_and_plan_of_one_year_are_the_same_period(tmp_path: Path) -> None:
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2024 факт"),
            _text("C1", "2024 план"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="100"),
            CellSpec(addr="C2", value="80", formula="=B2"),
        ],
    )
    assert _lag(edges, "P&L!C2", "P&L!B2") == "same"
    link = _link(graph, "P&L!C2")
    assert link["formula_class"] == "same_period"
    assert link["period_id"] == "2024@план"


def test_prior_year_variant_is_one_calendar_step(tmp_path: Path) -> None:
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2023 ф"),
            _text("C1", "2023 п"),
            _text("D1", "2024 ф"),
            _text("E1", "2024 п"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1"),
            CellSpec(addr="C2", value="2"),
            CellSpec(addr="D2", value="3", formula="=B2"),
            CellSpec(addr="E2", value="4", formula="=C2"),
        ],
    )
    assert _lag(edges, "P&L!D2", "P&L!B2") == "-1"
    assert _lag(edges, "P&L!E2", "P&L!C2") == "-1"
    assert _link(graph, "P&L!D2")["formula_class"] == "rollforward"
    assert _link(graph, "P&L!E2")["formula_class"] == "rollforward"
    assert _link(graph, "P&L!E2")["period_id"] == "2024@п"


def test_mutual_variant_refs_are_unexpected(tmp_path: Path) -> None:
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2024 факт"),
            _text("C1", "2024 план"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="100", formula="=C2"),
            CellSpec(addr="C2", value="80", formula="=B2"),
        ],
    )
    assert _lag(edges, "P&L!B2", "P&L!C2") == "same"
    assert _lag(edges, "P&L!C2", "P&L!B2") == "same"
    assert _link(graph, "P&L!B2")["formula_class"] == "same_period"
    assert _link(graph, "P&L!C2")["formula_class"] == "same_period"
    assert graph["cycles"]
    assert {cycle["class"] for cycle in graph["cycles"]} == {"unexpected"}
    assert any(
        {"P&L!B2", "P&L!C2"} <= set(cycle["members"]) for cycle in graph["cycles"]
    )


def test_bare_year_beside_estimate_stays_split_and_same_slot(tmp_path: Path) -> None:
    """``2024`` next to ``2024E`` stays two period ids so the columns do not collapse.

    Lag still treats them as one calendar year. ``2025E`` is the next slot.
    """
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2024"),
            _text("C1", "2024E"),
            _text("D1", "2025E"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="100"),
            CellSpec(addr="C2", value="80", formula="=B2"),
            CellSpec(addr="D2", value="90", formula="=C2"),
        ],
    )
    assert _link(graph, "P&L!C2")["period_id"] == "2024@e"
    assert _link(graph, "P&L!D2")["period_id"] == "2025"
    assert _lag(edges, "P&L!C2", "P&L!B2") == "same"
    assert _link(graph, "P&L!C2")["formula_class"] == "same_period"
    assert _lag(edges, "P&L!D2", "P&L!C2") == "-1"
    assert _link(graph, "P&L!D2")["formula_class"] == "rollforward"


def test_out_of_order_variant_columns_rank_by_calendar_year(tmp_path: Path) -> None:
    """``2025 план | 2026 план | 2023 факт | 2024 факт | 2025 факт``.

    First-seen slots put ``2025`` at 0 and ``2024`` at 3, so ``2025 факт`` →
    ``2024 факт`` was lag 3. The slot is the year's calendar rank.
    """
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2025 план"),
            _text("C1", "2026 план"),
            _text("D1", "2023 факт"),
            _text("E1", "2024 факт"),
            _text("F1", "2025 факт"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1"),
            CellSpec(addr="C2", value="2"),
            CellSpec(addr="D2", value="3"),
            CellSpec(addr="E2", value="4", formula="=D2"),
            CellSpec(addr="F2", value="5", formula="=E2"),
        ],
    )
    assert _link(graph, "P&L!E2")["period_id"] == "2024"
    assert _link(graph, "P&L!F2")["period_id"] == "2025@факт"
    assert _lag(edges, "P&L!E2", "P&L!D2") == "-1"
    assert _lag(edges, "P&L!F2", "P&L!E2") == "-1"
    assert _link(graph, "P&L!E2")["formula_class"] == "rollforward"
    assert _link(graph, "P&L!F2")["formula_class"] == "rollforward"


def test_forecast_sheet_before_history_keeps_year_lag(tmp_path: Path) -> None:
    """History ``2024`` → ``2023`` stays one year back when Forecast is seen first.

    Forecast introduces ``2024`` and ``2025`` before History's ``2022 | 2023 | 2024``.
    First-seen then scored that edge as lag 3.
    """
    graph, edges = _run(
        tmp_path,
        sheets=[
            SheetSpec(
                name="Forecast",
                cells=[
                    _text("A1", "Item"),
                    _text("B1", "2024 факт"),
                    _text("C1", "2024 план"),
                    _text("D1", "2025 план"),
                    _text("A2", "Revenue"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="C2", value="2", formula="=B2"),
                    CellSpec(addr="D2", value="3"),
                ],
            ),
            SheetSpec(
                name="History",
                cells=[
                    _text("A1", "Item"),
                    _text("B1", "2022"),
                    _text("C1", "2023"),
                    _text("D1", "2024"),
                    _text("A2", "Revenue"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="C2", value="2"),
                    CellSpec(addr="D2", value="3", formula="=C2"),
                ],
            ),
        ],
    )
    assert _link(graph, "Forecast!C2")["period_id"] == "2024@план"
    assert _lag(edges, "Forecast!C2", "Forecast!B2") == "same"
    assert _link(graph, "History!D2")["period_id"] == "2024"
    assert _lag(edges, "History!D2", "History!C2") == "-1"
    assert _link(graph, "History!D2")["formula_class"] == "rollforward"


def test_out_of_order_columns_rank_by_calendar_year(tmp_path: Path) -> None:
    """A descending ``2025E | 2024E | 2023E`` axis lags by year, not column position.

    A leftward reference hits a later year, so the lag is positive.
    """
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2025E"),
            _text("C1", "2024E"),
            _text("D1", "2023E"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1"),
            CellSpec(addr="C2", value="2", formula="=B2"),
            CellSpec(addr="D2", value="3", formula="=C2"),
        ],
    )
    assert _link(graph, "P&L!C2")["period_id"] == "2024"
    assert _link(graph, "P&L!D2")["period_id"] == "2023"
    assert _lag(edges, "P&L!C2", "P&L!B2") == "1"
    assert _lag(edges, "P&L!D2", "P&L!C2") == "1"
    assert _link(graph, "P&L!C2")["formula_class"] == "cross_period"
    assert _link(graph, "P&L!D2")["formula_class"] == "cross_period"


def test_scrambled_year_columns_keep_calendar_steps(tmp_path: Path) -> None:
    """``2025E | 2026E | 2023E | 2024E``: ``2025`` → ``2024`` is one year, not three slots."""
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2025E"),
            _text("C1", "2026E"),
            _text("D1", "2023E"),
            _text("E1", "2024E"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1", formula="=E2"),
            CellSpec(addr="C2", value="2"),
            CellSpec(addr="D2", value="3"),
            CellSpec(addr="E2", value="4", formula="=D2"),
        ],
    )
    assert _lag(edges, "P&L!E2", "P&L!D2") == "-1"
    assert _lag(edges, "P&L!B2", "P&L!E2") == "-1"
    assert _link(graph, "P&L!B2")["formula_class"] == "rollforward"
    assert _link(graph, "P&L!E2")["formula_class"] == "rollforward"


def test_out_of_order_halves_stay_distinct_calendar_slots(tmp_path: Path) -> None:
    """``2024H1`` and ``2024H2`` do not collapse, and column order does not set the lag."""
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2025H2"),
            _text("C1", "2024H1"),
            _text("D1", "2025H1"),
            _text("E1", "2024H2"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1", formula="=D2"),
            CellSpec(addr="C2", value="2"),
            CellSpec(addr="D2", value="3", formula="=E2"),
            CellSpec(addr="E2", value="4", formula="=C2"),
        ],
    )
    assert _link(graph, "P&L!E2")["period_id"] == "2024H2"
    assert _link(graph, "P&L!D2")["period_id"] == "2025H1"
    assert _lag(edges, "P&L!E2", "P&L!C2") == "-1"
    assert _lag(edges, "P&L!D2", "P&L!E2") == "-1"
    assert _lag(edges, "P&L!B2", "P&L!D2") == "-1"
    assert _link(graph, "P&L!E2")["formula_class"] == "rollforward"
    assert _link(graph, "P&L!D2")["formula_class"] == "rollforward"


def test_quarter_sheet_does_not_change_year_lag(tmp_path: Path) -> None:
    """Years and quarters stay separate runs. A quarter sheet does not sit between years."""
    graph, edges = _run(
        tmp_path,
        sheets=[
            SheetSpec(
                name="Quarters",
                cells=[
                    _text("A1", "Item"),
                    _text("B1", "2024Q1"),
                    _text("C1", "2024Q2"),
                    _text("D1", "2024Q3"),
                    _text("A2", "Revenue"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="C2", value="2", formula="=B2"),
                    CellSpec(addr="D2", value="3", formula="=C2"),
                ],
            ),
            SheetSpec(
                name="Years",
                cells=[
                    _text("A1", "Item"),
                    _text("B1", "2023"),
                    _text("C1", "2024"),
                    _text("D1", "2025"),
                    _text("A2", "Revenue"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="C2", value="2", formula="=B2"),
                    CellSpec(addr="D2", value="3", formula="=C2"),
                ],
            ),
        ],
    )
    assert _lag(edges, "Quarters!C2", "Quarters!B2") == "-1"
    assert _lag(edges, "Quarters!D2", "Quarters!C2") == "-1"
    assert _lag(edges, "Years!C2", "Years!B2") == "-1"
    assert _lag(edges, "Years!D2", "Years!C2") == "-1"
    assert _link(graph, "Years!D2")["formula_class"] == "rollforward"
    assert _link(graph, "Quarters!C2")["formula_class"] == "rollforward"


def test_quarter_gap_is_the_true_calendar_distance(tmp_path: Path) -> None:
    """``2016Q4`` → ``2011Q2`` is 22 quarters, not the count of columns in the book."""
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "2011Q2"),
            _text("C1", "2016Q4"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1"),
            CellSpec(addr="C2", value="2", formula="=B2"),
        ],
    )
    assert _link(graph, "P&L!C2")["period_id"] == "2016Q4"
    assert _lag(edges, "P&L!C2", "P&L!B2") == "-22"
    assert _link(graph, "P&L!C2")["formula_class"] == "cross_period"


def test_cross_grain_reference_is_unaligned(tmp_path: Path) -> None:
    """A quarter cell pointing at a year is not a numeric lag, so it is not a roll-forward."""
    graph, edges = _run(
        tmp_path,
        sheets=[
            SheetSpec(
                name="Quarters",
                cells=[
                    _text("A1", "Item"),
                    _text("B1", "2024Q1"),
                    _text("C1", "2024Q2"),
                    _text("A2", "Revenue"),
                    CellSpec(addr="B2", value="1", formula="=Years!D2"),
                    CellSpec(addr="C2", value="2", formula="=B2"),
                ],
            ),
            SheetSpec(
                name="Years",
                cells=[
                    _text("A1", "Item"),
                    _text("B1", "2023"),
                    _text("C1", "2024"),
                    _text("D1", "2025"),
                    _text("A2", "Revenue"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="C2", value="2"),
                    CellSpec(addr="D2", value="3"),
                ],
            ),
        ],
    )
    assert _lag(edges, "Quarters!B2", "Years!D2") == "unaligned"
    assert _link(graph, "Quarters!B2")["formula_class"] == "same_period"
    assert _lag(edges, "Quarters!C2", "Quarters!B2") == "-1"


def test_model_year_reference_is_unaligned(tmp_path: Path) -> None:
    """``Y1`` is not a calendar key, so the step is not a number."""
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Year"),
            _text("B1", "1"),
            _text("C1", "2"),
            _text("D1", "3"),
            _text("A2", "Revenue"),
            CellSpec(addr="B2", value="1"),
            CellSpec(addr="C2", value="2", formula="=B2"),
            CellSpec(addr="D2", value="3"),
        ],
    )
    assert _link(graph, "P&L!C2")["period_id"] == "Y2"
    assert _lag(edges, "P&L!C2", "P&L!B2") == "unaligned"
    assert _link(graph, "P&L!C2")["formula_class"] == "same_period"


def test_day_lag_counts_days(tmp_path: Path) -> None:
    """Weekly dates keep a day key, and the lag is the number of days."""
    graph, edges = _run(
        tmp_path,
        [
            _text("A1", "Item"),
            _text("B1", "11.01.2026"),
            _text("C1", "18.01.2026"),
            _text("D1", "25.01.2026"),
            _text("A2", "Receipts"),
            CellSpec(addr="B2", value="1"),
            CellSpec(addr="C2", value="2", formula="=B2"),
            CellSpec(addr="D2", value="3", formula="=C2"),
        ],
    )
    assert _link(graph, "P&L!C2")["period_id"] == "2026-01-18"
    assert _lag(edges, "P&L!C2", "P&L!B2") == "-7"
    assert _lag(edges, "P&L!D2", "P&L!C2") == "-7"
    assert _link(graph, "P&L!C2")["formula_class"] == "cross_period"
