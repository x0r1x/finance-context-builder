"""A vertical ruler stays joined through context, markdown, and the graph."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.published import published_context
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.cli import build
from finance_context.context.observations import build_observations
from finance_context.formulas.stage import IR_CELL_COLUMNS
from finance_context.graph.models import GraphDocument
from finance_context.graph.stage import build_formula_graph
from finance_context.layout.detect import detect_layout
from finance_context.layout.relations import formula_relations
from finance_context.models.context import ContextDocument
from finance_context.settings import Settings
from finance_context.store.duckdb_io import read_parquet_dicts


def _book(path: Path) -> None:
    text = (
        ("A1", "млн руб."),
        ("B1", "Revenue"),
        ("C1", "Rate"),
    )
    cells = [CellSpec(addr=addr, value=value, type="inlineStr") for addr, value in text]
    years = ("2020", "2021", "2022", "2023")
    revenue = ("10", "11", "12", "13")
    rate = ("20", "22", "36", "26")
    for row, year, left, right in zip(range(2, 6), years, revenue, rate, strict=True):
        cells.append(CellSpec(addr=f"A{row}", value=year))
        if row == 2:
            cells.append(CellSpec(addr=f"B{row}", value=left))
        else:
            cells.append(CellSpec(addr=f"B{row}", value=left, formula=f"=B{row - 1}*1.1"))
        factor = "3" if row == 4 else "2"
        cells.append(CellSpec(addr=f"C{row}", value=right, formula=f"=B{row}*{factor}"))
    build_xlsx(path, sheets=[SheetSpec(name="V", cells=cells)])


def _offline_settings(**kwargs: object) -> Settings:
    data_dir = kwargs.get("data_dir", Path("data"))
    return Settings(data_dir=data_dir, _env_file=None)  # type: ignore[arg-type]


def test_vertical_book_joins_context_markdown_and_graph(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("finance_context.cli.Settings", _offline_settings)
    source = tmp_path / "vertical.xlsx"
    _book(source)
    dest = tmp_path / "out"
    build(source, output=dest, data_dir=tmp_path / "data")

    columns = [name for name, _dtype in IR_CELL_COLUMNS]
    cells = read_parquet_dicts(dest / "ir" / "cells.parquet", columns)
    layout = detect_layout(cells)
    assert formula_relations(layout, cells) == []
    memory = published_context(
        job_id="vertical",
        workbook_meta={"sheets": [{"name": "V"}]},
        cells=cells,
        layout=layout,
        source_filename="vertical.xlsx",
    )
    published = ContextDocument.model_validate_json(
        (dest / "context" / "context.json").read_text(encoding="utf-8")
    )
    memory_rows = {(row.label): row for block in memory.blocks for row in block.rows}
    file_rows = {(row.label): row for block in published.blocks for row in block.rows}
    assert set(memory_rows) == {"Revenue", "Rate"}
    for label in ("Revenue", "Rate"):
        assert memory_rows[label].values == file_rows[label].values
        assert memory_rows[label].formula == file_rows[label].formula
        assert memory_rows[label].formula_exceptions == file_rows[label].formula_exceptions
        assert memory_rows[label].series[0].formula_exceptions == file_rows[label].series[
            0
        ].formula_exceptions
        assert memory_rows[label].neighbors == []
        assert file_rows[label].hints.currency == "RUB"
        assert file_rows[label].hints.scale == "m"

    revenue = file_rows["Revenue"]
    rate = file_rows["Rate"]
    assert revenue.values == ["10", "11", "12", "13"]
    assert rate.formula_exceptions == ["V!C4"]
    assert rate.series[0].formula_exceptions == ["V!C4"]
    assert revenue.row_key == "V|1|c2|V!c1r2"

    markdown = (dest / "context" / "context.md").read_text(encoding="utf-8")
    assert "[V!B2]" in markdown
    assert "[V!A1]" not in markdown
    assert "2020 (2)" in markdown

    graph = GraphDocument.model_validate_json(
        (dest / "graph" / "graph.json").read_text(encoding="utf-8")
    )
    links = {link.cell: link for link in graph.links}
    growth = links["V!B3"]
    assert growth.row_key == revenue.row_key
    assert growth.period_id == "2021"
    assert growth.formula_class == "cross_period"
    assert links["V!C4"].row_key == rate.row_key
    assert links["V!C4"].period_id == "2022"
    vertical_links = [link for link in links.values() if link.cell.startswith("V!")]
    assert all(link.row_key and link.period_id for link in vertical_links)

    observations = build_observations(published, graph.links, row_keys=[revenue.row_key])
    assert [item.source.cell for item in observations.observations] == ["B2", "B3", "B4", "B5"]

    memory_dir = tmp_path / "memory-graph"
    memory_dir.mkdir()
    build_formula_graph(
        memory_dir,
        job_id="memory",
        layout=layout,
        cells=cells,
        book_dir=dest,
        settings=Settings(
            data_dir=tmp_path / "data",
            _env_file=None,
            graph_stream=False,
            json_stream=False,
        ),
    )
    memory_graph = json.loads((memory_dir / "graph" / "graph.json").read_text(encoding="utf-8"))
    memory_links = {link["cell"]: link for link in memory_graph["links"]}
    assert memory_links["V!B3"]["row_key"] == growth.row_key
    assert memory_links["V!B3"]["period_id"] == "2021"
    assert memory_links["V!B3"]["formula_class"] == "cross_period"
