from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


def _c(sheet: str, addr: str, value: str | None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def _typed_table(name_header: str) -> list[dict]:
    """A type column sits left of the row name. Both cells are filled."""
    cells = [
        _c("Out", "A1", "Тип показателя"),
        _c("Out", "B1", name_header),
        _c("Out", "C1", "2016"),
        _c("Out", "D1", "2017"),
    ]
    for row, (kind, name, left, right) in enumerate(
        [
            ("Общий (не УФН)", "CFADS", "10", "11"),
            ("Коэффициент", "DSCR", "1.4", "1.5"),
            ("Расчетный показатель", "FCF", "3", "4"),
            ("Общий (не УФН)", "NCF", "5", "6"),
            ("Общий (не УФН)", "Debt Service", "7", "8"),
        ],
        start=2,
    ):
        cells += [
            _c("Out", f"A{row}", kind),
            _c("Out", f"B{row}", name),
            _c("Out", f"C{row}", left),
            _c("Out", f"D{row}", right),
        ]
    return cells


def test_name_header_is_the_row_label_not_the_type_column() -> None:
    block = detect_layout(_typed_table("Наименование")).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "CFADS",
        "DSCR",
        "FCF",
        "NCF",
        "Debt Service",
    ]
    assert block.label_col == 2
    assert {row.label_col for row in block.rows} == {2}


def test_two_text_columns_without_a_name_header_keep_the_left_label() -> None:
    block = detect_layout(_typed_table("Статья")).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "Общий (не УФН)",
        "Коэффициент",
        "Расчетный показатель",
        "Общий (не УФН)",
        "Общий (не УФН)",
    ]
    assert block.label_col == 1


def test_profile_name_header_selects_that_column() -> None:
    block = detect_layout(
        _typed_table("Статья"),
        name_headers=("статья",),
    ).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "CFADS",
        "DSCR",
        "FCF",
        "NCF",
        "Debt Service",
    ]
    assert block.label_col == 2


def test_a_translation_header_does_not_steal_the_name() -> None:
    """«Наименование по-англ.» is also a name header. The row name is the left one."""
    cells = [
        _c("IS", "A1", "Наименование показателя"),
        _c("IS", "B1", "Наименование по-англ."),
        _c("IS", "C1", "2016"),
        _c("IS", "D1", "2017"),
        _c("IS", "A2", "Чистая прибыль"),
        _c("IS", "B2", "Net income"),
        _c("IS", "C2", "10"),
        _c("IS", "D2", "11"),
        _c("IS", "A3", "Операционная прибыль (убыток)"),
        _c("IS", "B3", "Operating profit (loss)"),
        _c("IS", "C3", "4"),
        _c("IS", "D3", "5"),
    ]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert [row.label for row in block.rows] == [
        "Чистая прибыль",
        "Операционная прибыль (убыток)",
    ]
    assert block.label_col == 1


def test_code_column_between_type_and_name_reaches_role_cells() -> None:
    """B sits between the type and the name. It stays a row cell and a role cell."""
    from tests.helpers.published import published_context as build_context

    cells = [
        _c("Out", "A1", "Тип показателя"),
        _c("Out", "B1", "Код"),
        _c("Out", "C1", "Наименование"),
        _c("Out", "D1", "2023"),
        _c("Out", "E1", "2024"),
    ]
    for row, kind, code, name, left, right in (
        (2, "Коэффициент", "001", "DSCR", "1.4", "1.5"),
        (3, "Коэффициент", "002", "ICR", "2", "3"),
        (4, "Общий", "003", "CFADS", "10", "11"),
    ):
        cells += [
            _c("Out", f"A{row}", kind),
            _c("Out", f"B{row}", code),
            _c("Out", f"C{row}", name),
            _c("Out", f"D{row}", left),
            _c("Out", f"E{row}", right),
        ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    by_label = {row.label: row for row in block.rows}
    dscr = by_label["DSCR"]
    assert dscr.label_col == 3
    assert [(cell.col, cell.role) for cell in dscr.cells if cell.col == 2] == [(2, "index")]
    doc = build_context(
        job_id="code-col",
        workbook_meta={"sheets": [{"name": "Out"}]},
        cells=cells,
        layout=layout,
    )
    published = next(row for item in doc.blocks for row in item.rows if row.label == "DSCR")
    assert [(cell.col, cell.role) for cell in published.cells if cell.col == 2] == [(2, "index")]


def test_flat_type_and_name_columns_stay_one_level() -> None:
    cells = [
        _c("Out", "A1", "Тип показателя"),
        _c("Out", "B1", "Наименование"),
        _c("Out", "C1", "2023"),
        _c("Out", "D1", "2024"),
        _c("Out", "A2", "Коэффициент"),
        _c("Out", "B2", "DSCR"),
        _c("Out", "C2", "1.4"),
        _c("Out", "D2", "1.5"),
        _c("Out", "A3", "Общий"),
        _c("Out", "B3", "CFADS"),
        _c("Out", "C3", "10"),
        _c("Out", "D3", "11"),
    ]
    rows = {row.label: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    for label in ("DSCR", "CFADS"):
        row = rows[label]
        assert row.label == label
        assert row.label_col == 2
        assert row.indent == 0
        assert row.parent_row is None
        assert row.section_path == []
        assert row.kind == "fact"
        assert row.cells == []


def test_section_filled_only_in_the_left_column_parents_the_name() -> None:
    cells = [
        _c("Out", "A1", "Раздел"),
        _c("Out", "B1", "Наименование"),
        _c("Out", "C1", "2023"),
        _c("Out", "D1", "2024"),
        _c("Out", "A2", "Денежный поток"),
        _c("Out", "B3", "CFADS"),
        _c("Out", "C3", "10"),
        _c("Out", "D3", "11"),
        _c("Out", "B4", "DSCR"),
        _c("Out", "C4", "1.4"),
        _c("Out", "D4", "1.5"),
    ]
    rows = {row.label: row for row in detect_layout(cells).sheets[0].blocks[0].rows}
    section = rows["Денежный поток"]
    assert section.label_col == 1
    assert section.indent == 0
    assert section.parent_row is None
    assert section.section_path == []
    assert section.kind == "abstract"
    assert section.cells == []
    fact = rows["CFADS"]
    assert fact.label_col == 2
    assert fact.indent == 1
    assert fact.parent_row == section.row
    assert fact.section_path == ["Денежный поток"]
    assert fact.kind == "fact"
    assert fact.cells == []


def _code_table(caption: str) -> list[dict]:
    cells = [
        _c("Out", "A1", "Тип"),
        _c("Out", "B1", caption),
        _c("Out", "C1", "Наименование"),
        _c("Out", "D1", "2024"),
        _c("Out", "A2", "Коэффициент"),
        _c("Out", "B2", "001"),
        _c("Out", "C2", "DSCR"),
        _c("Out", "D2", "1.4"),
        _c("Out", "A3", "Коэффициент"),
        _c("Out", "B3", "002"),
        _c("Out", "C3", "ICR"),
        _c("Out", "D3", "2.1"),
    ]
    return cells


def _code_roles(caption: str) -> list[str]:
    """Roles of 001 and 002, the column between the type and the name."""
    return [
        cell.role
        for block in detect_layout(_code_table(caption)).sheets[0].blocks
        for row in block.rows
        for cell in row.cells
        if cell.col == 2
    ]


def test_code_phrase_headers_are_index_not_value() -> None:
    """001 and 002 under a listed code caption are an index, not a value."""
    for caption in (
        "Код показателя",
        "Код строки",
        "Код статьи",
        "Indicator code",
        "Line item code",
    ):
        assert _code_roles(caption) == ["index", "index"], caption


def test_an_unlisted_header_containing_code_is_not_index() -> None:
    """A closed list. The word code inside another caption stays a value."""
    for caption in ("Currency code", "Postal code", "Code of conduct"):
        assert _code_roles(caption) == ["value", "value"], caption
