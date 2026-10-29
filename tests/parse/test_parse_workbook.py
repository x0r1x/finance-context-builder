from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.parquet import load_cells, load_workbook_json
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.errors import ContextError
from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.formulas.csr import present_cell_count
from finance_context.memory import load_presence, usage_for_book
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
        "style_indent",
        "outline_level",
        "col_outline_level",
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


def test_shared_formula_keeps_unquoted_cyrillic_sheet_name(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "shared_ru.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Модель",
                cells=[
                    CellSpec(
                        addr="B1",
                        value="1",
                        formula="Q1_план!A1+A1",
                        shared_si=0,
                        shared_ref="B1:B2",
                    ),
                    CellSpec(addr="B2", value="2", shared_si=0),
                ],
            ),
            SheetSpec(name="Q1_план", cells=[CellSpec(addr="A1", value="1")]),
        ],
    )
    parse_workbook(source, dest)
    by = _by_addr(load_cells(dest))
    assert by[("Модель", "B2")]["formula_raw"] == "Q1_план!A2+A2"


def test_shared_formula_child_keeps_function_and_name_text(
    tmp_path: Path, dest: Path
) -> None:
    source = tmp_path / "shared_names.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Sheet1",
                cells=[
                    CellSpec(
                        addr="B1",
                        value="0",
                        formula="LOG10(A1)*Rate1",
                        shared_si=0,
                        shared_ref="B1:B2",
                    ),
                    CellSpec(addr="B2", value="0", shared_si=0),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    by = _by_addr(load_cells(dest))
    assert by[("Sheet1", "B2")]["formula_raw"] == "LOG10(A2)*Rate1"


def test_hidden_sheet_does_not_hide_its_cells(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "hidden-sheet.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="IFRS",
                hidden=True,
                cells=[
                    CellSpec(addr="C2", value="Выручка от реализации", type="inlineStr"),
                    CellSpec(addr="D1", value="2 кв. 2009", type="inlineStr"),
                    CellSpec(addr="E1", value="3 кв. 2009", type="inlineStr"),
                    CellSpec(addr="F1", value="4 кв. 2009", type="inlineStr"),
                    CellSpec(addr="D2", value="10"),
                    CellSpec(addr="E2", value="11"),
                    CellSpec(addr="F2", value="12"),
                ],
            )
        ],
    )
    meta = parse_workbook(source, dest)
    by = _by_addr(load_cells(dest))
    assert meta.sheets[0].hidden is True
    assert by[("IFRS", "C2")]["hidden"] is False
    assert by[("IFRS", "D1")]["hidden"] is False


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
    meta = parse_workbook(source, dest)
    by = _by_addr(load_cells(dest))
    assert by[("Sheet1", "A1")]["hidden"] is False
    assert by[("Sheet1", "B1")]["hidden"] is True
    assert by[("Sheet1", "A2")]["hidden"] is True
    assert meta.sheets[0].hidden_rows == [2]
    assert meta.sheets[0].hidden_cols == [2]


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
                    CellSpec(addr="E5", value="2", formula="INDEX({1;2},2)"),
                ],
            )
        ],
        iterate=True,
        defined_names=[("Rate", "Inputs!$D$5")],
    )
    meta = parse_workbook(source, dest)
    assert meta.iterate is True
    rate = next(item for item in meta.defined_names if item.name == "Rate")
    assert rate.formula == "Inputs!$D$5"
    assert rate.sheet is None
    dumped = meta.model_dump(mode="json")
    rate = next(item for item in dumped["defined_names"] if item["name"] == "Rate")
    assert "sheet" not in rate
    # OOXML stores formulas in en-US; `;` in an array constant is a row break.
    assert meta.locale_hint == "en"


def test_sheet_scoped_defined_name_keeps_its_sheet(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "local-name.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(name="Inputs", cells=[CellSpec(addr="D3", value="1")]),
            SheetSpec(name="Other", cells=[CellSpec(addr="A1", value="0")]),
        ],
        defined_names=[("Pick", "$D$3", 0), ("Rate", "Inputs!$D$5")],
    )
    meta = parse_workbook(source, dest)
    pick = next(item for item in meta.defined_names if item.name == "Pick")
    rate = next(item for item in meta.defined_names if item.name == "Rate")
    assert pick.formula == "$D$3"
    assert pick.sheet == "Inputs"
    assert rate.sheet is None
    dumped = meta.model_dump(mode="json")
    stored = next(item for item in dumped["defined_names"] if item["name"] == "Pick")
    assert stored["sheet"] == "Inputs"


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


def test_value_cell_on_a_sheet_whose_name_starts_with_a_quote(tmp_path: Path) -> None:
    from finance_context.excel.stage import PRESENCE_COLUMNS
    from finance_context.formulas.csr import sheet_usage_spans
    from finance_context.store.fs import write_parquet

    path = tmp_path / "cell_presence.parquet"
    write_parquet(
        path,
        PRESENCE_COLUMNS,
        [
            ("'Data", "A1", "styled_blank", 1),
            ("'Data", "B1", "populated", 1),
            ("'Data", "B2", "populated", 2),
        ],
    )
    usage = sheet_usage_spans(load_presence(path).present_spans())
    assert usage.rows_by_col["'Data"][2] == ((1, 2),)


def test_blank_above_an_open_run_is_kept() -> None:
    from finance_context.excel.ooxml import _BlankRuns

    runs = _BlankRuns()
    assert runs.blank(1, 10) == []
    assert runs.blank(1, 3) == [(1, 10, 10)]
    assert runs.blank(1, 3) == []
    assert runs.finish() == [(1, 3, 3)]


def test_styled_blank_run_is_one_column_span(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "run.xlsx"
    blanks = [CellSpec(addr=f"A{row}", style=1) for row in range(1, 4)]
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    *blanks,
                    CellSpec(addr="B1", value="1"),
                    CellSpec(addr="A5", style=1),
                    CellSpec(addr="C1", style=1),
                    CellSpec(addr="C2", value="2"),
                    CellSpec(addr="C3", style=1),
                    CellSpec(addr="D1", value="1", formula="A2"),
                    CellSpec(addr="D2", value="1", formula="A4"),
                ],
            )
        ],
    )
    parse_workbook(source, dest)
    rows = read_parquet(dest / "raw" / "cell_presence.parquet")
    by_addr = {row["addr"]: row for row in rows}
    assert by_addr["A1"]["presence"] == "styled_blank"
    assert by_addr["A1"]["row_end"] == 3
    assert "A2" not in by_addr
    assert by_addr["A5"]["row_end"] == 5
    assert by_addr["C1"]["row_end"] == 1
    assert by_addr["C2"]["presence"] == "populated"
    assert by_addr["C3"]["presence"] == "styled_blank"
    presence = load_presence(dest / "raw" / "cell_presence.parquet")
    assert presence.get("P&L!A2") == "styled_blank"
    assert presence.get("P&L!A4") is None
    assert presence.get("P&L!C2") == "populated"
    assert present_cell_count(usage_for_book(dest)) == 10
    compile_workbook(dest)
    edges = {row["target"]: row for row in read_parquet(dest / "ir" / "cell_edges.parquet")}
    assert edges["P&L!A2"]["evidence"] == "styled_blank"
    assert edges["P&L!A2"]["reason"] == "actual_blank_cell"
    assert edges["P&L!A4"]["evidence"] == "omitted_by_excel"
    assert edges["P&L!A4"]["reason"] == "actual_blank_cell"


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
