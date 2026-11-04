"""File-order cell check. One warning per sheet, none on an ordered book."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tests.helpers.parquet import load_cells, load_workbook_json
from tests.helpers.published import published_context as build_context
from tests.helpers.xlsx import NS_MAIN, CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.excel.stage import parse_workbook
from finance_context.layout.models import Layout
from finance_context.settings import Settings

_ORDER_WARNING = (
    "4 cell(s) out of order or repeated on sheet S "
    "(e.g. S!A2, S!A1, S!A1); duplicate formula edges possible"
)
_KEPT = ["B2", "A2", "A1", "A1", "C1", "B1"]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None, **overrides)  # type: ignore[arg-type]


def _ordered(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="S",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="B1", value="2", formula="=A1+1"),
                    CellSpec(addr="A2", value="3"),
                ],
            )
        ],
    )


def _malformed(path: Path) -> Path:
    """B2, then A2, then A1 twice, then C1, then B1. Sheet T stays in order."""
    build_xlsx(
        path,
        sheets=[
            SheetSpec(name="S", cells=[CellSpec(addr="A1", value="1")]),
            SheetSpec(
                name="T",
                cells=[CellSpec(addr="A1", value="1"), CellSpec(addr="B2", value="2")],
            ),
        ],
    )
    sheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{NS_MAIN}">
<sheetData>
  <row r="2"><c r="B2"><v>1</v></c></row>
  <row r="2"><c r="A2"><v>2</v></c></row>
  <row r="1"><c r="A1"><f>=A2</f><v>2</v></c></row>
  <row r="1"><c r="A1"><f>=A2+1</f><v>3</v></c></row>
  <row r="1"><c r="C1"><v>4</v></c></row>
  <row r="1"><c r="B1"><v>5</v></c></row>
</sheetData>
</worksheet>
"""
    with zipfile.ZipFile(path, "r") as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    members["xl/worksheets/sheet1.xml"] = sheet.encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def _pipeline(tmp_path: Path, name: str, source: Path, **overrides: object) -> Path:
    dest = tmp_path / name
    dest.mkdir()
    target = dest / "source" / "source.xlsx"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    Pipeline(_settings(tmp_path, **overrides)).run(dest, job_id="job", source_filename="book.xlsx")
    return dest


def test_ordered_book_has_no_warning_and_matching_bytes(tmp_path: Path) -> None:
    source = _ordered(tmp_path / "ordered.xlsx")
    streamed = _pipeline(tmp_path, "stream", source, parse_streaming=True)
    dom = _pipeline(tmp_path, "dom", source, parse_streaming=False)
    published = (
        "raw/workbook.json",
        "context/context.json",
        "context/context.md",
        "graph/graph.json",
        "meta/meta.json",
    )
    for name in published:
        assert (streamed / name).read_bytes() == (dom / name).read_bytes(), name
    workbook = (streamed / "raw" / "workbook.json").read_text(encoding="utf-8")
    context = (streamed / "context" / "context.json").read_text(encoding="utf-8")
    markdown = (streamed / "context" / "context.md").read_text(encoding="utf-8")
    assert "cell_order_warnings" not in workbook
    assert "out of order or repeated" not in context
    assert "out of order or repeated" not in markdown
    document = json.loads(context)
    assert document["warnings"] == []
    assert document["meta"]["warnings"] == []


def test_repeated_and_out_of_order_cells_warn_once(tmp_path: Path) -> None:
    source = _malformed(tmp_path / "bad.xlsx")
    streamed = tmp_path / "stream"
    dom = tmp_path / "dom"
    streamed.mkdir()
    dom.mkdir()
    meta_stream = parse_workbook(
        source, streamed, settings=_settings(tmp_path, parse_streaming=True)
    )
    meta_dom = parse_workbook(source, dom, settings=_settings(tmp_path, parse_streaming=False))
    assert meta_stream.cell_order_warnings == [_ORDER_WARNING]
    assert meta_dom.cell_order_warnings == [_ORDER_WARNING]
    assert (streamed / "raw" / "workbook.json").read_bytes() == (
        dom / "raw" / "workbook.json"
    ).read_bytes()
    stored = load_workbook_json(streamed)
    assert stored["cell_order_warnings"] == [_ORDER_WARNING]
    assert [row["addr"] for row in load_cells(streamed) if row["sheet"] == "S"] == _KEPT
    assert [row["addr"] for row in load_cells(dom) if row["sheet"] == "S"] == _KEPT
    assert [row["addr"] for row in load_cells(streamed) if row["sheet"] == "T"] == ["A1", "B2"]

    dest = _pipeline(tmp_path, "job", source)
    document = json.loads((dest / "context" / "context.json").read_text(encoding="utf-8"))
    assert document["warnings"] == [_ORDER_WARNING]
    assert document["meta"]["warnings"] == [_ORDER_WARNING]
    markdown = (dest / "context" / "context.md").read_text(encoding="utf-8")
    assert markdown.count(_ORDER_WARNING) == 1
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["warnings"] == [_ORDER_WARNING]


def test_order_warning_stays_inside_the_cap() -> None:
    warning = (
        "1 cell(s) out of order or repeated on sheet S "
        "(e.g. S!A1); duplicate formula edges possible"
    )
    sheets = [{"name": f"H{index}", "hidden": True} for index in range(60)]
    doc = build_context(
        job_id="job",
        workbook_meta={"sheets": sheets, "cell_order_warnings": [warning]},
        cells=[],
        layout=Layout(sheets=[]),
    )
    assert doc.warnings[0] == warning
    assert len(doc.warnings) == 50
    assert doc.meta.warnings[0] == warning
