from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.parquet import load_cells, load_workbook_json
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.errors import ContextError
from finance_context.excel import parse_workbook
from finance_context.store.fs import read_parquet


def _by_addr(cells: list[dict]) -> dict[tuple[str, str], dict]:
    return {(c["sheet"], c["addr"]): c for c in cells}


def test_raw_cell_schema_formula_and_cached_value(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "simple.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Revenue", type="s"),
                    CellSpec(addr="B1", value="100", formula="A1"),
                ],
            )
        ],
        shared_strings=["Revenue"],
    )
    meta = parse_workbook(source, dest)
    cells = load_cells(dest)
    cols = set(cells[0])
    assert cols == {
        "sheet",
        "row",
        "col",
        "addr",
        "formula_raw",
        "cached_value",
        "hidden",
        "number_format",
        "comment",
        "shared_si",
        "shared_master",
    }
    assert cells[0]["shared_si"] is None
    assert cells[0]["shared_master"] is False
    by = _by_addr(cells)
    label = by[("P&L", "A1")]
    assert label["row"] == 1
    assert label["col"] == 1
    assert label["formula_raw"] is None
    assert label["cached_value"] == "Revenue"
    formula = by[("P&L", "B1")]
    assert formula["formula_raw"] == "A1"
    assert formula["cached_value"] == "100"
    assert meta.sheets[0].name == "P&L"


def test_shared_formula_expanded_to_a1(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "shared.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="A2", value="2"),
                    CellSpec(
                        addr="B1",
                        value="1",
                        formula="A1",
                        shared_si=0,
                        shared_ref="B1:B2",
                    ),
                    CellSpec(addr="B2", value="2", shared_si=0),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    by = _by_addr(load_cells(dest))
    assert by[("Sheet1", "B1")]["formula_raw"] == "A1"
    assert by[("Sheet1", "B2")]["formula_raw"] == "A2"


def test_hidden_row_and_column(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "hidden.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="B1", value="2"),
                    CellSpec(addr="A2", value="3"),
                ],
                hidden_cols=[2],
                hidden_rows=[2],
            )
        ],
    )
    parse_workbook(source, dest)
    by = _by_addr(load_cells(dest))
    assert by[("Sheet1", "A1")]["hidden"] is False
    assert by[("Sheet1", "B1")]["hidden"] is True
    assert by[("Sheet1", "A2")]["hidden"] is True


def test_excel_error_cached_as_token(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "err.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[CellSpec(addr="A1", value="#REF!", type="e", formula="B1")],
            )
        ],
    )
    parse_workbook(source, dest)
    cell = _by_addr(load_cells(dest))[("Sheet1", "A1")]
    assert cell["cached_value"] == "#REF!"
    assert cell["formula_raw"] == "B1"


def test_comment_and_number_format(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "fmt.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[CellSpec(addr="A1", value="0.2", style=1)],
                comments={"A1": "growth rate"},
            )
        ],
        num_formats={164: "0%"},
        cell_xfs=[0, 164],
    )
    parse_workbook(source, dest)
    cell = _by_addr(load_cells(dest))[("Sheet1", "A1")]
    assert cell["comment"] == "growth rate"
    assert cell["number_format"] == "0%"


def test_vba_flag_without_executing_bin(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "macro.xlsm"
    build_xlsx(
        source,
        sheets=[SheetSpec(name="Sheet1", cells=[CellSpec(addr="A1", value="1")])],
        has_vba=True,
    )
    meta = parse_workbook(source, dest)
    assert meta.has_vba is True
    assert meta.has_xlm is False


def test_xlm_macrosheet_flag(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "xlm.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(name="Sheet1", cells=[CellSpec(addr="A1", value="1")]),
            SheetSpec(
                name="Macro1",
                cells=[CellSpec(addr="A1", value="2")],
                macrosheet=True,
            ),
        ],
    )
    meta = parse_workbook(source, dest)
    assert meta.has_xlm is True
    sheets = {s.name for s in meta.sheets}
    assert "Macro1" in sheets


def test_external_link_recorded_without_opening_target(
    tmp_path: Path, dest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "missing-other.xlsx"
    source = tmp_path / "ext.xlsx"
    build_xlsx(
        source,
        sheets=[SheetSpec(name="Sheet1", cells=[CellSpec(addr="A1", value="1")])],
        externals=[str(target)],
    )
    opened: list[str] = []
    real_open = Path.open

    def wrapped(self: Path, *args: object, **kwargs: object):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", wrapped)
    meta = parse_workbook(source, dest)
    assert str(target) in meta.externals
    assert str(target) not in opened


def test_defined_names_iterate_and_locale_hint(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "names.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Inputs",
                cells=[
                    CellSpec(addr="D5", value="0.2"),
                    CellSpec(addr="E5", value="1", formula="СУММ(D5;D5)"),
                ],
            )
        ],
        iterate=True,
        defined_names=[("Rate", "Inputs!$D$5")],
    )
    meta = parse_workbook(source, dest)
    assert meta.iterate is True
    assert any(n.name == "Rate" and n.formula == "Inputs!$D$5" for n in meta.defined_names)
    assert meta.locale_hint == "ru"


def test_date1904_flag_does_not_decode_cached_value(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "dates.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="B1", value="44743", style=1),
                ],
            )
        ],
        cell_xfs=[0, 14],
        date1904=True,
    )
    meta = parse_workbook(source, dest)
    assert meta.date1904 is True
    cells = load_cells(dest)
    by = _by_addr(cells)
    assert by[("P&L", "B1")]["cached_value"] == "44743"
    assert by[("P&L", "B1")]["number_format"] == "mm-dd-yy"


def test_parse_writes_only_raw_artifacts_no_findings(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "plain.xlsx"
    build_xlsx(
        source,
        sheets=[SheetSpec(name="Sheet1", cells=[CellSpec(addr="A1", value="1")])],
    )
    parse_workbook(source, dest)
    written = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert written == {
        "raw/cells.parquet",
        "raw/cell_presence.parquet",
        "raw/workbook.json",
    }
    assert "findings" not in load_workbook_json(dest)


def test_success_leaves_no_tmp_files(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "ok.xlsx"
    build_xlsx(
        source,
        sheets=[SheetSpec(name="Sheet1", cells=[CellSpec(addr="A1", value="1")])],
    )
    parse_workbook(source, dest)
    tmps = [p for p in dest.rglob("*") if p.name.endswith(".tmp") or ".tmp." in p.name]
    assert tmps == []


def test_cell_presence_splits_populated_and_styled_blank(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "presence.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="B1", style=1),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    cells = {(row["sheet"], row["addr"]) for row in load_cells(dest)}
    presence = {
        (row["sheet"], row["addr"]): row["presence"]
        for row in read_parquet(dest / "raw" / "cell_presence.parquet")
    }
    assert ("P&L", "A1") in cells
    assert ("P&L", "B1") not in cells
    assert presence[("P&L", "A1")] == "populated"
    assert presence[("P&L", "B1")] == "styled_blank"


def test_not_a_zip_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "nope.xlsx"
    source.write_bytes(b"this is not zip")
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "zip_rejected"
