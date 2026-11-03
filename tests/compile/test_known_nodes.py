"""KnownNodes matches a set of canonical ids, including published documents."""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.excel.stage import parse_workbook
from finance_context.formulas.known import KnownNodes
from finance_context.formulas.stage import canonical_node_id, compile_schema_id, compile_workbook
from finance_context.graph.models import GRAPH_SCHEMA_VERSION
from finance_context.models.context import SCHEMA_VERSION
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet

_SCHEMA_ID = "936073df4b8bed1cb1e707daa4eb361d4aa21301dfb8d91fca1a0d0169335341"


def test_compile_schema_id_is_unchanged() -> None:
    assert compile_schema_id() == _SCHEMA_ID
    assert SCHEMA_VERSION == "1.14.0"
    assert GRAPH_SCHEMA_VERSION == "1.7.0"


def test_explicit_addresses_match_the_canonical_set() -> None:
    cells = [
        ("S", "$a$1"),
        ("S", "A01"),
        ("S", "A1"),
        ("S", "b2"),
        ("S", "#REF!"),
        ("S", "not a cell"),
        ("S", "A1:B2"),
        ("Data!Q", "A1"),
        ("Data", "Q!A1"),
        ("O'Brien", "B2"),
        ("'Data", "C3"),
        ("Q1 2025", "A1"),
        ("", "A1"),
        ("PL", "A1"),
        ("pl", "A1"),
    ]
    old = {canonical_node_id(sheet, addr) for sheet, addr in cells}
    idx = KnownNodes()
    for sheet, addr in cells:
        idx.add(sheet, addr)
    assert len(idx) == len(old)
    assert bool(idx) is True
    for node in old:
        assert node in idx
    assert "S!A1" in idx
    assert "S!$A$1" not in idx
    assert "S!a1" not in idx
    assert "S!A01" not in idx
    assert "S!b2" not in idx
    assert "S!B2" in idx
    assert "S!#REF!" in idx
    assert "Data!Q!A1" in idx
    assert "O'Brien!B2" in idx
    assert "'O''Brien'!B2" not in idx
    assert "'Data!C3" in idx
    assert "!A1" in idx
    assert "PL!A1" in idx
    assert "pl!A1" in idx
    assert "no-bang" not in idx
    assert (KnownNodes() or {"kept"}) == {"kept"}
    assert (idx or set()) is idx
    assert "S!A1" in idx
    idx.add("S", "A3")
    assert "S!A2" not in idx
    assert "S!A3" in idx
    assert len(idx) == len(old) + 1


def test_run_past_excel_max_row_still_contains_its_start() -> None:
    far = KnownNodes()
    far.add("S", "A1048576")
    far.add("S", "A1048577")
    assert "S!A1048576" in far
    assert "S!A1048577" in far
    assert len(far) == 2

    block = KnownNodes()
    for row in range(1_048_574, 1_048_578):
        block.add("S", f"A{row}")
    assert len(block) == 4
    for row in range(1_048_574, 1_048_578):
        assert f"S!A{row}" in block

    mixed = KnownNodes()
    for row in (100, 101, 102, 1_048_577):
        mixed.add("S", f"A{row}")
    assert "S!A100" in mixed
    assert "S!A101" in mixed
    assert "S!A102" in mixed
    assert "S!A1048577" in mixed
    assert "S!A103" not in mixed
    assert "S!A1048576" not in mixed


def test_last_grid_row_stays_a_ref_when_the_next_row_is_present(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    source = dest / "book.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="S",
                cells=[
                    CellSpec(addr="A1048576", value="1"),
                    CellSpec(addr="A1048577", value="2"),
                    CellSpec(addr="B1", value="3", formula="=A1048576+A1048577"),
                ],
            )
        ],
    )
    settings = _settings(tmp_path)
    parse_workbook(source, dest, settings=settings)
    compile_workbook(dest, settings=settings)
    rows = [
        row
        for row in read_parquet(dest / "ir" / "cell_edges.parquet")
        if row["target"] == "S!A1048576"
    ]
    assert len(rows) == 1
    assert rows[0]["source"] == "S!B1"
    assert rows[0]["status"] is None
    assert rows[0]["evidence"] is None


def test_randomized_membership_matches_canonical_ids() -> None:
    rng = random.Random(0x194A)
    sheets = ["P&L", "Data!Q1", "'Data", "O'Brien", "Q1 2025", "", "PL", "pl", "A!B!C"]
    junk = ["#REF!", "not a cell", "A1:B2", "1A", "A", "ZZZ1", "XFE1", "", "Q!A1"]
    cells: list[tuple[str, str]] = []
    for _ in range(500):
        roll = rng.randrange(6)
        if roll == 0:
            addr = f"{rng.choice('abcXYZ')}{rng.randint(0, 40)}"
        elif roll == 1:
            addr = f"${rng.choice('ABab')}${rng.randint(1, 30)}"
        elif roll == 2:
            addr = f"A{rng.randint(0, 12):02d}"
        elif roll == 3:
            addr = rng.choice(junk)
        elif roll == 4:
            addr = f"{rng.choice(['A', 'AA', 'XFD'])}{rng.randint(1, 5000)}"
        else:
            addr = rng.choice(["$a$1", "a1", "A01", "A1", "B2", "AAA1"])
        cells.append((rng.choice(sheets), addr))
    cells.extend(cells[:30])
    old = {canonical_node_id(sheet, addr) for sheet, addr in cells}
    idx = KnownNodes()
    for sheet, addr in cells:
        idx.add(sheet, addr)
    assert len(idx) == len(old)
    assert bool(idx) is bool(old)
    probes = set(old)
    probes.update(f"{sheet}!{addr}" for sheet, addr in cells)
    probes.update(node.swapcase() for node in list(old)[:40])
    probes.update({"", "no-bang", "P&L!$A$1", "'O''Brien'!A1", "P&L!A01"})
    for probe in probes:
        assert (probe in idx) is (probe in old)


def _book(path: Path) -> None:
    build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="1", formula="=A1+A1"),
                    CellSpec(addr="B3", value="2", formula="=SUM(B2:B2)+B2"),
                    CellSpec(addr="B4", value="3", formula="='O''Brien'!A1+'Data!Q1'!A1"),
                ],
            ),
            SheetSpec(name="O'Brien", cells=[CellSpec(addr="A1", value="4")]),
            SheetSpec(name="Data!Q1", cells=[CellSpec(addr="A1", value="5")]),
        ],
        shared_strings=["Item", "2024", "Revenue"],
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None)


def _run(tmp_path: Path, name: str, source: Path) -> Path:
    dest = tmp_path / name / "job"
    dest.mkdir(parents=True)
    target = dest / "source" / "source.xlsx"
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    settings = Settings(data_dir=tmp_path / name / "data", _env_file=None)
    Pipeline(settings).run(dest, job_id="job", source_filename="book.xlsx")
    return dest


_PUBLISHED = (
    "ir/cells.parquet",
    "ir/edges.parquet",
    "ir/cell_edges.parquet",
    "graph/graph.json",
    "context/context.json",
    "context/context.md",
)


def test_published_bytes_match_set_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.xlsx"
    _book(source)
    probe = tmp_path / "probe"
    probe.mkdir()
    parse_workbook(source, probe, settings=_settings(tmp_path))
    old = {
        canonical_node_id(str(row["sheet"]), str(row["addr"]))
        for row in read_parquet(probe / "raw" / "cells.parquet")
    }
    plain = _run(tmp_path, "plain", source)
    ctx = json.loads((plain / "context" / "context.json").read_text(encoding="utf-8"))
    graph = json.loads((plain / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert ctx["schema_version"] == "1.14.0"
    assert graph["schema_version"] == "1.7.0"
    stamp = json.loads((plain / "ir" / "compile.json").read_text(encoding="utf-8"))
    assert stamp["schema_id"] == _SCHEMA_ID

    monkeypatch.setattr(KnownNodes, "__contains__", lambda self, item: item in old)
    patched = _run(tmp_path, "patched", source)
    for name in _PUBLISHED:
        assert (plain / name).read_bytes() == (patched / name).read_bytes(), name


def test_value_cells_do_not_build_canonical_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    source = dest / "book.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="A2", value="2"),
                    CellSpec(addr="B1", value="3", formula="=A1+A1"),
                ],
            )
        ],
    )
    parse_workbook(source, dest, settings=_settings(tmp_path))
    calls: list[tuple[str, str]] = []
    real = canonical_node_id

    def spy(sheet: str, addr: str) -> str:
        calls.append((sheet, addr))
        return real(sheet, addr)

    monkeypatch.setattr("finance_context.formulas.stage.canonical_node_id", spy)
    compile_workbook(dest, settings=_settings(tmp_path))
    assert calls == [("P&L", "B1")]
