"""Large ranges are stored as bounds, and every reader agrees with per-formula expansion.

The reference is the same pipeline with range nodes switched off, which expands
every range per formula the way main does. The workbook mixes the shapes that
matter: whole columns, running totals, trailing windows, wide period rows,
blank and partly blank blocks, a whole row, a missing sheet, and cycles that run
through a range. Books stay under RANGE_EXPAND_CAP so the reference is exact.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.excel.a1 import col_to_index, index_to_col
from finance_context.formulas import csr
from finance_context.formulas.csr import (
    expand_cell_edges,
    expanded_edge_count,
    parse_range_node,
    range_node_for,
    sheet_usage,
)
from finance_context.formulas.models import Edge
from finance_context.graph import trace as trace_module
from finance_context.graph.ranges import (
    SEGMENT_PREFIX,
    CellGrid,
    CoverIndex,
    RangeHops,
    is_cell_id,
)
from finance_context.graph.refs import parse_node_id
from finance_context.graph.trace import trace_graph
from finance_context.layout import adjacency
from finance_context.memory import assess_raw, usage_for_book
from finance_context.render.graph import render_trace_markdown
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet

ROWS = 80
YEARS = [str(2001 + n) for n in range(80)]
WIDE_COLS = [index_to_col(2 + n) for n in range(80)]  # B .. CC


def _data_sheet() -> SheetSpec:
    cells = [CellSpec(addr="A1", value="Item", type="s")]
    cells += [
        CellSpec(addr=f"{col}1", value=year, type="s")
        for col, year in zip("BCDEFG", YEARS, strict=False)
    ]
    for row in range(2, ROWS + 2):
        cells.append(CellSpec(addr=f"A{row}", value=f"k{row % 5}", type="s"))
        for col in "BCDEG":  # column F stays blank under its header
            if col == "C" and row % 7 == 0:
                continue  # holes inside C
            cells.append(CellSpec(addr=f"{col}{row}", value=str(row)))
    return SheetSpec(name="Data", cells=cells)


def _calc_sheet() -> SheetSpec:
    cells = [CellSpec(addr="A1", value="Item", type="s")]
    cells += [
        CellSpec(addr=f"{col}1", value=year, type="s")
        for col, year in zip("BCDEFG", YEARS, strict=False)
    ]
    for row in range(2, ROWS + 2):
        cells.append(CellSpec(addr=f"A{row}", value=f"k{row % 5}", type="s"))
        for col in "BCF":
            # Whole columns, copied down and across. F holds only its header.
            formula = f"SUMIF(Data!$A:$A,$A{row},Data!{col}:{col})"
            cells.append(CellSpec(addr=f"{col}{row}", value="1", formula=formula))
        # Running total: a new, longer range on every row.
        cells.append(CellSpec(addr=f"H{row}", value="1", formula=f"SUM(Data!$B$2:B{row})"))
        if row > 71:
            # Trailing 70-row window: a different range on every row.
            window = f"SUM(Data!C{row - 69}:C{row})"
            cells.append(CellSpec(addr=f"I{row}", value="1", formula=window))
    extra = {
        "J2": "SUM(Data!$K$1:$K$200)",  # fully blank block
        "J3": "SUM(Data!C1:C150)",  # partly blank block
        "J4": "SUM(Data!B2:D100)",  # several columns
        "J5": "SUM(Data!3:3)",  # whole row
        "J6": "SUM(Ghost!A1:A100)",  # missing sheet
        "J7": "AVERAGE(Data!F2:F120)",  # blank period column
        "J8": "SUM(Data!K:K)+SUM(Data!M:N)",  # whole columns with no cells at all
    }
    cells += [CellSpec(addr=addr, value="1", formula=formula) for addr, formula in extra.items()]
    return SheetSpec(name="Calc", cells=cells)


def _wide_sheet() -> SheetSpec:
    cells = [CellSpec(addr="A1", value="Item", type="s")]
    cells += [
        CellSpec(addr=f"{col}1", value=year, type="s")
        for col, year in zip(WIDE_COLS, YEARS, strict=True)
    ]
    for row in range(2, 8):
        cells.append(CellSpec(addr=f"A{row}", value=f"line{row}", type="s"))
        for index, col in enumerate(WIDE_COLS):
            if row == 4 and index % 9 == 0:
                continue
            cells.append(CellSpec(addr=f"{col}{row}", value=str(index + row)))
    cells.append(CellSpec(addr="A10", value="npv", type="s"))
    cells.append(CellSpec(addr="A11", value="cumulative", type="s"))
    for col in WIDE_COLS:
        # Lag-sensitive, not an aggregation: formula_class comes from the lags.
        cells.append(CellSpec(addr=f"{col}10", value="1", formula=f"NPV(0.1,$B2:{col}2)"))
        cells.append(CellSpec(addr=f"{col}11", value="1", formula=f"SUM($B3:{col}3)"))
    # Iterative cycle through a vertical range: X15 reads Y16:Y100, Y16 reads X15.
    cells.append(CellSpec(addr="X15", value="1", formula="SUM(Y16:Y100)"))
    cells.append(CellSpec(addr="Y16", value="1", formula="X15*0.5"))
    # Cycle with no period shift: B30 reads B31:B120, B60 reads B30.
    cells.append(CellSpec(addr="B30", value="1", formula="SUM(B31:B120)"))
    cells.append(CellSpec(addr="B60", value="1", formula="B30+1"))
    # A formula that reads a range it sits in.
    cells.append(CellSpec(addr="D200", value="1", formula="SUM(D130:D260)"))
    return SheetSpec(name="Wide", cells=cells)


def _book(path: Path) -> Path:
    labels = ["Item", *YEARS, *(f"k{n}" for n in range(5)), "npv", "cumulative"]
    labels += [f"line{row}" for row in range(2, 8)]
    return build_xlsx(
        path,
        sheets=[_data_sheet(), _calc_sheet(), _wide_sheet()],
        shared_strings=labels,
        iterate=True,
    )


def _run(root: Path, name: str, **settings: object) -> Path:
    source = _book(root / f"{name}.xlsx")
    dest = root / name
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    chosen = Settings(data_dir=root / f"data-{name}", _env_file=None, **settings)
    Pipeline(chosen).run(dest, job_id=f"{name}-job", source_filename=f"{name}.xlsx")
    return dest


def _expanded_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every range expands per formula, as on main."""
    monkeypatch.setattr(csr, "range_node_for", lambda target, usage: None)
    monkeypatch.setattr(adjacency, "range_node_for", lambda target, usage: None)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("ranges")
    nodes = _run(root, "nodes")
    with pytest.MonkeyPatch.context() as patch:
        _expanded_mode(patch)
        expanded = _run(root, "expanded")
    return nodes, expanded


def _json(dest: Path, name: str) -> dict:
    return json.loads((dest / name).read_text(encoding="utf-8"))


def test_large_ranges_are_one_edge_with_no_member_rows(runs: tuple[Path, Path]) -> None:
    nodes, expanded = runs
    edges = read_parquet(nodes / "ir" / "cell_edges.parquet")
    range_edges = [edge for edge in edges if parse_range_node(str(edge["target"]))]
    assert range_edges
    # Nothing in the file is sourced from a range: no member lists anywhere.
    assert not any(parse_range_node(str(edge["source"])) for edge in edges)
    assert {edge["kind"] for edge in edges} <= {
        "ref",
        "cross_sheet",
        "range",
        "dynamic",
        "external",
    }
    by_formula = {(edge["source"], edge["target"]) for edge in range_edges}
    assert ("Calc!H81", "Data!B2:B81") in by_formula
    assert ("Calc!B2", "Data!A:A") in by_formula
    assert ("Calc!J5", "Data!3:3") in by_formula
    # A missing sheet still expands per formula and stays dangling.
    ghost = [edge for edge in edges if str(edge["target"]).startswith("Ghost!")]
    assert ghost and all(edge["dangling"] for edge in ghost)
    assert len(edges) < len(read_parquet(expanded / "ir" / "cell_edges.parquet")) / 5


def test_graph_links_and_cycles_match_expansion(runs: tuple[Path, Path]) -> None:
    nodes, expanded = runs
    new, old = _json(nodes, "graph.json"), _json(expanded, "graph.json")

    def links(doc: dict) -> dict:
        return {link["cell"]: (link["formula_class"], link["refs"]) for link in doc["links"]}

    assert links(new) == links(old)
    assert [(c["members"], c["class"]) for c in new["cycles"]] == [
        (c["members"], c["class"]) for c in old["cycles"]
    ]
    classes = {tuple(c["members"]): c["class"] for c in new["cycles"]}
    assert classes[("Wide!X15", "Wide!Y16")] == "iterative_ok"
    assert classes[("Wide!B30", "Wide!B60")] == "unexpected"
    assert classes[("Wide!D200",)] == "unexpected"
    assert new["schema_version"] == "1.7.0"
    assert new["dangling"]["count"] == old["dangling"]["count"]
    assert new["truncated"] == old["truncated"]
    assert new["kinds"].get("range", 0) < old["kinds"]["range"]


def _present_rows(dest: Path) -> dict[str, dict[int, set[int]]]:
    usage = usage_for_book(dest)
    return {
        sheet: {col: set(rows) for col, rows in cols.items()}
        for sheet, cols in usage.rows_by_col.items()
    }


def _member_rows(item: dict, present: dict[str, dict[int, set[int]]]) -> set[int]:
    """Rows a per-formula expansion of this range touches."""
    sheet = item["sheet"]
    columns = present.get(sheet, {})
    if item["row_start"] is None:  # whole columns: present cells only
        c1, c2 = col_to_index(item["col_start"]), col_to_index(item["col_end"])
        return {row for col, rows in columns.items() if c1 <= col <= c2 for row in rows}
    if item["col_start"] is None:  # whole rows: present cells only
        rows = {row for col_rows in columns.values() for row in col_rows}
        return {row for row in rows if item["row_start"] <= row <= item["row_end"]}
    return set(range(item["row_start"], item["row_end"] + 1))  # blanks included


def test_context_rows_match_expansion_and_nothing_is_cut(runs: tuple[Path, Path]) -> None:
    nodes, expanded = runs
    new, old = _json(nodes, "context.json"), _json(expanded, "context.json")
    assert "ranges" not in old
    ranges = {item["id"]: item for item in new["ranges"]}
    present = _present_rows(nodes)
    member_rows = {rid: _member_rows(item, present) for rid, item in ranges.items()}

    def rows(doc: dict) -> dict:
        return {row["row_key"]: row for block in doc["blocks"] for row in block["rows"]}

    new_rows, old_rows = rows(new), rows(old)
    assert new_rows.keys() == old_rows.keys()
    checked = 0
    for key, row in new_rows.items():
        before = old_rows[key]
        same = {k: v for k, v in row.items() if k not in {"neighbors", "neighbor_ranges"}}
        assert same == {k: v for k, v in before.items() if k != "neighbors"}, key
        sheet, number = row["sheet"], row["row"]
        own = f"{sheet}!{number}"
        rebuilt = set(row["neighbors"])
        for rid in row.get("neighbor_ranges", []):
            rebuilt |= {f"{ranges[rid]['sheet']}!{r}" for r in member_rows[rid]} - {own}
        for rid, item in ranges.items():
            if item["sheet"] == sheet and number in member_rows[rid]:
                rebuilt |= set(item["readers"]) - {own}
        assert rebuilt == set(before["neighbors"]), key
        checked += bool(row.get("neighbor_ranges"))
    assert checked > 0
    # Readers are listed once per range, never per member row.
    assert len(ranges["Data!A:A"]["readers"]) == ROWS


def _origins(dest: Path) -> list[str]:
    """Every formula cell, every third value cell, and blank cells inside ranges."""
    rows = read_parquet(dest / "ir" / "cells.parquet")
    formulas = [f"{c['sheet']}!{c['addr']}" for c in rows if c.get("formula_raw")]
    values = sorted(f"{c['sheet']}!{c['addr']}" for c in rows if not c.get("formula_raw"))
    blanks = ["Data!K5", "Data!C7", "Data!F30", "Wide!Y50"]
    return sorted(set(formulas) | set(values[::3]) | set(blanks))


def _trace(dest: Path, origin: str, direction: str) -> tuple:
    doc = trace_graph(dest, origin=origin, direction=direction, depth=3, range_members=0)
    nodes = sorted((n.node_id, n.depth, n.node_type, n.row_key, n.period_id) for n in doc.nodes)
    edges = sorted((e.source, e.target, e.kind, e.period_lag, e.col_offset) for e in doc.edges)
    return doc.stopped, nodes, edges


@pytest.mark.parametrize("direction", ["precedents", "dependents"])
def test_every_trace_matches_expansion(
    runs: tuple[Path, Path], direction: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodes, expanded = runs
    # Each trace call reads the job's parquet files. Read each file once here;
    # the walk itself still runs in full for every origin.
    loaded: dict[str, list[dict]] = {}

    def cached(path: Path) -> list[dict]:
        key = str(path)
        if key not in loaded:
            loaded[key] = read_parquet(path)
        return loaded[key]

    monkeypatch.setattr(trace_module, "read_parquet", cached)
    mismatched = []
    for origin in _origins(nodes):
        if _trace(nodes, origin, direction) != _trace(expanded, origin, direction):
            mismatched.append(origin)
    assert mismatched == []


def test_estimate_is_not_above_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_context.excel import parse_workbook

    source = _book(tmp_path / "book.xlsx")
    dest = tmp_path / "parsed"
    parse_workbook(source, dest)
    with_nodes = assess_raw(dest)
    _expanded_mode(monkeypatch)
    without = assess_raw(dest)
    assert with_nodes.formulas == without.formulas
    assert with_nodes.expanded_cells < without.expanded_cells
    assert with_nodes.estimate_bytes <= without.estimate_bytes


def test_trace_says_how_many_members_it_left_out(runs: tuple[Path, Path]) -> None:
    nodes, _expanded = runs
    full = trace_graph(nodes, origin="Calc!B2", direction="precedents", depth=1, range_members=0)
    cut = trace_graph(nodes, origin="Calc!B2", direction="precedents", depth=1, range_members=7)
    edge_full = next(e for e in full.edges if e.target == "Data!A:A")
    edge_cut = next(e for e in cut.edges if e.target == "Data!A:A")
    assert edge_full.members_total == edge_full.members_shown == ROWS + 1
    assert (edge_cut.members_total, edge_cut.members_shown) == (ROWS + 1, 7)
    data_a = [n for n in cut.nodes if n.node_id.startswith("Data!A")]
    assert len(data_a) == 7
    markdown = render_trace_markdown(cut)
    assert "Ranges not followed in full" in markdown
    assert "| Calc!B2 | Data!A:A | 81 | 7 |" in markdown
    # Small refs carry no member counts.
    assert all(e.members_total is None for e in full.edges if e.target == "Calc!A2")


def test_a_blank_cell_inside_a_range_has_dependents(runs: tuple[Path, Path]) -> None:
    nodes, _expanded = runs
    doc = trace_graph(nodes, origin="Data!K5", direction="dependents", depth=1)
    assert doc.nodes[0].node_type == "empty"
    # range_ref is the parsed reference, already without $.
    assert {(e.source, e.target) for e in doc.edges} == {("Calc!J2", "Data!K1:K200")}


def test_range_node_threshold_comes_from_settings(tmp_path: Path) -> None:
    dest = _run(tmp_path, "threshold", range_node_min_cells=500)
    edges = read_parquet(dest / "ir" / "cell_edges.parquet")
    targets = {str(edge["target"]) for edge in edges if edge["source"] == "Calc!J3"}
    # C1:C150 is 150 cells, under 500: it expands per formula again.
    assert "Data!C1:C150" not in targets
    assert "Data!C2" in targets


def test_a_new_threshold_recompiles_cached_ir(tmp_path: Path) -> None:
    # Two jobs on one shared book directory: the second reuses the parse, and
    # reuses the IR only when it was compiled under the same threshold.
    book = tmp_path / "book"
    book.mkdir()
    (book / "source.xlsx").write_bytes(_book(tmp_path / "shared.xlsx").read_bytes())

    def j3_targets() -> set[str]:
        edges = read_parquet(book / "ir" / "cell_edges.parquet")
        return {str(edge["target"]) for edge in edges if edge["source"] == "Calc!J3"}

    for job, threshold in (("first", 64), ("second", 500)):
        settings = Settings(
            data_dir=tmp_path / "data", range_node_min_cells=threshold, _env_file=None
        )
        Pipeline(settings).run(
            tmp_path / job, job_id=f"{job}-job", source_filename="shared.xlsx", shared_dir=book
        )
        if job == "first":
            assert "Data!C1:C150" in j3_targets()
    assert "Data!C1:C150" not in j3_targets()
    assert "Data!C2" in j3_targets()
    graph_edges = read_parquet(tmp_path / "second" / "ir" / "graph_edges.parquet")
    j3_graph = {str(edge["target"]) for edge in graph_edges if edge["source"] == "Calc!J3"}
    assert len(j3_graph) == 150


def test_cover_index_matches_a_scan() -> None:
    rng = random.Random(11)
    nodes = []
    for n in range(300):
        c1 = rng.randint(1, 120)
        c2 = c1 + rng.choice([0, 0, 1, 5, 90])
        r1 = rng.randint(1, 400)
        r2 = r1 + rng.randint(0, 300)
        nodes.append(csr.RangeNode(f"S!n{n}", "S", c1, c2, r1, r2))
    index = CoverIndex(nodes)
    for _ in range(2000):
        col, row = rng.randint(1, 220), rng.randint(1, 720)
        expected = {n.node_id for n in nodes if n.c1 <= col <= n.c2 and n.r1 <= row <= n.r2}
        assert {n.node_id for n in index.covering("S", col, row)} == expected


@pytest.mark.parametrize("sheet", ["#Loop", "#seg|Loop"])
@pytest.mark.parametrize("mode", ["nodes", "expanded"])
def test_cycles_on_a_sheet_named_with_a_hash_are_reported(
    tmp_path: Path, mode: str, sheet: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Excel allows "#" and "|" in a sheet name, even the segment prefix
    # "#seg|"; only RangeHops segment ids are not cells.
    cells = [CellSpec(addr=f"A{row}", value=str(row)) for row in range(2, 101)]
    cells = [cell for cell in cells if cell.addr != "A50"]
    cells += [
        CellSpec(addr="A1", value="1", formula="SUM(A2:A100)"),
        CellSpec(addr="A50", value="1", formula="A1+1"),
        CellSpec(addr="C1", value="1", formula="D1+1"),
        CellSpec(addr="D1", value="1", formula="C1+1"),
    ]
    source = build_xlsx(
        tmp_path / "hash.xlsx", sheets=[SheetSpec(name=sheet, cells=cells)], iterate=True
    )
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    if mode == "expanded":
        _expanded_mode(monkeypatch)
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    Pipeline(settings).run(dest, job_id="hash-job", source_filename="hash.xlsx")
    cycles = {tuple(c["members"]) for c in _json(dest, "graph.json")["cycles"]}
    assert (f"{sheet}!A1", f"{sheet}!A50") in cycles
    assert (f"{sheet}!C1", f"{sheet}!D1") in cycles


def test_first_present_cell_matches_a_scan() -> None:
    rng = random.Random(23)
    cells = {(rng.randint(1, 40), rng.randint(1, 500)) for _ in range(900)}
    usage = sheet_usage(f"S!{index_to_col(col)}{row}" for col, row in cells)
    for _ in range(1500):
        c1 = rng.randint(1, 45)
        c2 = c1 + rng.choice([0, 1, 3, 30, 200])
        r1 = rng.randint(1, 520)
        r2 = r1 + rng.choice([0, 2, 40, 600])
        node = csr.RangeNode("S!x", "S", c1, c2, r1, r2)
        inside = sorted((row, col) for col, row in cells if c1 <= col <= c2 and r1 <= row <= r2)
        assert csr.first_present(node, usage) == (inside[0] if inside else None)
    whole = csr.RangeNode("S!B:D", "S", 2, 4, 1, 1_048_576)
    expected = min((row, col) for col, row in cells if 2 <= col <= 4)
    assert CellGrid(usage).first(whole) == (expected[1], expected[0])


def test_range_hops_reach_exactly_the_formula_cells_inside() -> None:
    rng = random.Random(5)
    cells = {f"S!{index_to_col(rng.randint(1, 6))}{rng.randint(1, 300)}" for _ in range(500)}
    hops = RangeHops(cells)
    # One graph for all nodes, as the cycle pass builds it: a tree segment
    # shared by two nodes is written once and reached from both.
    graph: dict[str, list[str]] = {}
    nodes = []
    for n in range(60):
        c1 = rng.randint(1, 6)
        r1 = rng.randint(1, 300)
        node = csr.RangeNode(f"S!r{n}", "S", c1, rng.randint(c1, 6), r1, rng.randint(r1, 300))
        nodes.append(node)
        for parent, child in hops.edges(node):
            graph.setdefault(parent, []).append(child)
    for node in nodes:
        reached: set[str] = set()
        stack = [node.node_id]
        while stack:
            for child in graph.get(stack.pop(), []):
                if child.startswith("#seg"):
                    stack.append(child)
                else:
                    reached.add(child)
        inside = {
            cell
            for cell in cells
            if (p := parse_node_id(cell))
            and node.c1 <= p[1] <= node.c2
            and node.r1 <= p[2] <= node.r2
        }
        assert reached == inside
        assert set(hops.members(node)) == inside


@pytest.mark.parametrize("sheet", ["S", "#seg|S", "x!A1", "#seg|x!A1|3"])
def test_segments_are_never_cells_and_leaves_always_are(sheet: str) -> None:
    rng = random.Random(7)
    cells = {f"{sheet}!{index_to_col(rng.randint(1, 3))}{rng.randint(1, 80)}" for _ in range(120)}
    hops = RangeHops(cells)
    names: set[str] = set()
    for n in range(20):
        r1 = rng.randint(1, 80)
        node = csr.RangeNode(f"{sheet}!r{n}", sheet, 1, 3, r1, rng.randint(r1, 80))
        for parent, child in hops.edges(node):
            names.update((parent, child))
    segments = {name for name in names if name.startswith(SEGMENT_PREFIX + sheet + "|")}
    assert segments
    assert not any(is_cell_id(name) for name in segments)
    leaves = names - segments - {f"{sheet}!r{n}" for n in range(20)}
    assert leaves and leaves <= cells
    assert all(is_cell_id(name) for name in leaves)


def test_estimate_counts_a_node_as_one_edge() -> None:
    usage = sheet_usage([f"Data!A{row}" for row in range(1, 101)])
    edges = [Edge(kind="range", source=f"R!B{n}", target="Data!A:A") for n in range(50)]
    assert expanded_edge_count(edges, usage) == 50
    small = [Edge(kind="range", source="R!B1", target="Data!A1:A10")]
    assert expanded_edge_count(small, usage) == 10


def test_small_range_and_unknown_sheet_still_expand() -> None:
    usage = sheet_usage([f"Data!A{row}" for row in range(1, 101)])
    assert range_node_for("Data!A1:A64", usage) is None
    assert range_node_for("Data!A1:A65", usage) is not None
    assert range_node_for("Data!A1:A65", None) is None
    assert range_node_for("Ghost!A:A", usage) is None
    assert range_node_for("data!$A:$A", usage).node_id == "Data!A:A"
    rows = expand_cell_edges(
        [Edge(kind="range", source="R!B1", target="Ghost!A1:A100")],
        {"R!B1"},
        known_sheets={"Data", "R"},
        usage=usage,
    )
    assert rows and all(row.dangling_reason == "missing_sheet" for row in rows)
