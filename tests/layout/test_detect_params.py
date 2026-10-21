from __future__ import annotations

import re

import pytest

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


def _value_key(text: str, _col: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    if not slug or not any(char.isalpha() for char in slug):
        return "value"
    return slug


def _c(
    sheet: str,
    addr: str,
    value: str | None,
    *,
    hidden: bool = False,
    formula: str | None = None,
    formula_template: str | None = None,
    number_format: str | None = None,
) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": hidden,
        "formula_raw": formula,
        "formula_template": formula_template,
        "unparsed": False,
        "number_format": number_format,
        "comment": None,
    }


def test_params_block_from_scenario_matrix() -> None:
    cells = [
        _c("Input Assumptions", "C5", "Units"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "F2", "Base Case"),
        _c("Input Assumptions", "G2", "Scenario 2"),
        _c("Input Assumptions", "B3", "Scenario Chosen"),
        _c("Input Assumptions", "D3", "1"),
        _c("Input Assumptions", "F3", "1"),
        _c("Input Assumptions", "G3", "2"),
        _c("Input Assumptions", "B7", "TIME ASSUMPTIONS"),
        _c("Input Assumptions", "B8", "Concession Duration"),
        _c("Input Assumptions", "C8", "years"),
        _c("Input Assumptions", "D8", "40", formula="=INDEX(F8:G8,$D$3)"),
        _c("Input Assumptions", "F8", "40"),
        _c("Input Assumptions", "G8", "36.5"),
        _c("Input Assumptions", "B9", "Tax Rate"),
        _c("Input Assumptions", "C9", "%"),
        _c("Input Assumptions", "D9", "0.3"),
        _c("Input Assumptions", "F9", "0.3"),
        _c("Input Assumptions", "G9", "0.3"),
        _c("Input Assumptions", "B10", "Toll Rate"),
        _c("Input Assumptions", "C10", "£"),
        _c("Input Assumptions", "D10", "3.56"),
        _c("Input Assumptions", "F10", "3.56"),
        _c("Input Assumptions", "G10", "3.56"),
        _c("Input Assumptions", "B11", "Gearing"),
        _c("Input Assumptions", "C11", "%"),
        _c("Input Assumptions", "D11", "0.65"),
        _c("Input Assumptions", "F11", "0.65"),
        _c("Input Assumptions", "G11", "0.8"),
        _c("Input Assumptions", "B12", "TRAFFIC & REVENUE ASSUMPTIONS"),
        _c("Input Assumptions", "F12", "TRAFFIC & REVENUE ASSUMPTIONS"),
        _c("Input Assumptions", "B13", "Maintenance (including heavy maintenance & SPV costs)"),
        _c("Input Assumptions", "C13", "£/year"),
        _c("Input Assumptions", "D13", "8900000"),
        _c("Input Assumptions", "F13", "8900000"),
        _c("Input Assumptions", "G13", "8900000"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert sheet.blocks
    block = sheet.blocks[0]
    assert block.kind == "params"
    labels = {row.label: row for row in block.rows}
    assert labels["TIME ASSUMPTIONS"].kind == "abstract"
    assert labels["TRAFFIC & REVENUE ASSUMPTIONS"].kind == "abstract"
    assert labels["Concession Duration"].kind == "fact"
    selector = labels["Scenario Chosen"]
    assert selector.kind == "flag"
    assert selector.row == 3
    assert selector.label_col == 2
    assert {cell.col: cell.role for cell in selector.cells}[4] == "value"
    assert all(cell.role != "scenario" for cell in selector.cells)
    by_col = {header.col: header for header in block.axis.headers}
    assert by_col[6].text == "Base Case"
    assert by_col[6].role == "value"
    assert by_col[7].text == "Scenario 2"
    assert block.label_col == 2
    unit_cols = {cell.role for cell in labels["Tax Rate"].cells}
    assert "unit" in unit_cols
    assert "value" in unit_cols
    from finance_context.layout.params import unit_kind_from_text

    assert unit_kind_from_text("£/year") == "money"
    assert unit_kind_from_text("руб") == "money"
    assert unit_kind_from_text("РУБ") == "money"
    assert unit_kind_from_text("gbp") == "money"
    assert unit_kind_from_text("EUR") == "money"
    assert unit_kind_from_text("долл") == "money"
    assert unit_kind_from_text("years") == "count"
    assert unit_kind_from_text("veh/year") == "count"
    assert unit_kind_from_text("MWh p.a.") == "count"
    assert unit_kind_from_text("Index") == "ratio"


def test_params_block_from_parameter_table() -> None:
    cells = [
        _c("Assumptions", "B6", "Parameter"),
        _c("Assumptions", "C6", "Base"),
        _c("Assumptions", "D6", "Bull"),
        _c("Assumptions", "E6", "Bear"),
        _c("Assumptions", "F6", "Active"),
        _c("Assumptions", "G6", "Unit"),
        _c("Assumptions", "H6", "Notes"),
        _c("Assumptions", "B8", "Company"),
        _c("Assumptions", "C8", "Acme Corp"),
        _c("Assumptions", "F8", "Acme Corp"),
        _c("Assumptions", "G8", "text"),
        _c("Assumptions", "H8", "Legal name of the SPV"),
        _c("Assumptions", "B9", "Model version"),
        _c("Assumptions", "C9", "v3"),
        _c("Assumptions", "F9", "v3"),
        _c("Assumptions", "G9", "text"),
        _c("Assumptions", "B10", "Tax Rate"),
        _c("Assumptions", "C10", "0.25"),
        _c("Assumptions", "D10", "0.2"),
        _c("Assumptions", "E10", "0.3"),
        _c("Assumptions", "F10", "0.25"),
        _c("Assumptions", "G10", "%"),
        _c("Assumptions", "B11", "Start date"),
        _c("Assumptions", "C11", "44927"),
        _c("Assumptions", "F11", "44927"),
        _c("Assumptions", "G11", "date"),
        _c("Assumptions", "B12", "Gearing"),
        _c("Assumptions", "C12", "0.65"),
        _c("Assumptions", "D12", "0.7"),
        _c("Assumptions", "E12", "0.5"),
        _c("Assumptions", "F12", "0.65"),
        _c("Assumptions", "G12", "%"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.kind == "params"
    tax = next(row for row in block.rows if row.label == "Tax Rate")
    roles = {cell.role for cell in tax.cells}
    assert "unit" in roles
    assert "value" in roles
    assert "scenario" not in roles


def test_russian_param_headers_name_value_unit_and_note_columns() -> None:
    note = "источник: бизнес-план компании на 2025 год, утверждён советом директоров"
    heads = ["Параметр", "Значение", "Ед. изм.", "Примечание"]
    cells = [_c("Допущения", f"{col}2", head) for col, head in zip("BCDE", heads, strict=True)]
    for r, (label, value, unit) in enumerate(
        [("Цена", "12", "тыс. руб."), ("Доля расходов", "0.4", "%"), ("Ставка налога", "0.2", "%")],
        start=3,
    ):
        cells += [
            _c("Допущения", f"B{r}", label),
            _c("Допущения", f"C{r}", value),
            _c("Допущения", f"D{r}", unit),
            _c("Допущения", f"E{r}", note),
        ]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert block.kind == "params"
    assert [(h.col, h.role, h.text) for h in block.axis.headers] == [
        (3, "value", "Значение"),
        (4, "unit", "Ед. изм."),
        (5, "note", "Примечание"),
    ]
    assert [(cell.col, cell.role) for cell in block.rows[0].cells] == [
        (3, "value"),
        (4, "unit"),
        (5, "note"),
    ]


def _param_table(heads: list[str]) -> list[dict]:
    cells = [_c("Допущения", f"{col}1", head) for col, head in zip("ABCD", heads, strict=True)]
    note = "источник: бизнес-план компании на 2025 год, утверждён советом"
    for r, label in enumerate(["Цена", "Доля расходов", "Ставка налога"], start=2):
        cells += [
            _c("Допущения", f"A{r}", label),
            _c("Допущения", f"B{r}", "12"),
            _c("Допущения", f"C{r}", "руб."),
            _c("Допущения", f"D{r}", note),
        ]
    return cells


@pytest.mark.parametrize(
    ("heads", "roles"),
    [
        (
            ["Наименование показателя", "Значение, руб.", "Ед. измерения", "Источник"],
            [("value", "Значение, руб."), ("unit", "Ед. измерения"), ("note", "Источник")],
        ),
        (
            ["Статья затрат", "Значение (руб.)", "Единица изм.", "Описание"],
            [("value", "Значение (руб.)"), ("unit", "Единица изм."), ("note", "Описание")],
        ),
        (
            ["Параметры проекта", "Значение", "Размерность", "Source"],
            [("value", "Значение"), ("unit", "Размерность"), ("note", "Source")],
        ),
        (
            ["Исходные данные", "Значение", "UoM", "Comment"],
            [("value", "Значение"), ("unit", "UoM"), ("note", "Comment")],
        ),
        (
            ["Параметр", "Значение", "Unit of measure", "Comments"],
            [("value", "Значение"), ("unit", "Unit of measure"), ("note", "Comments")],
        ),
        (
            ["Показатель", "Значение", "Ед. изм.", "Comment(s)"],
            [("value", "Значение"), ("unit", "Ед. изм."), ("note", "Comment(s)")],
        ),
    ],
)
def test_param_header_spellings_keep_label_value_unit_and_note(
    heads: list[str], roles: list[tuple[str, str]]
) -> None:
    block = detect_layout(_param_table(heads)).sheets[0].blocks[0]
    assert block.kind == "params"
    assert block.label_col == 1
    assert [(header.role, header.text) for header in block.axis.headers] == roles


def test_yo_in_a_value_header_matches_the_lexicon_spelling() -> None:
    heads = ["Параметр", "Значениё", "Ед. изм.", "Примечание"]
    block = detect_layout(_param_table(heads)).sheets[0].blocks[0]
    assert [(header.role, header.col) for header in block.axis.headers][0] == ("value", 2)


def test_numeric_first_row_stays_data_and_amount_is_a_value_column() -> None:
    cells = []
    for row, (name, amount) in enumerate(
        [
            ("RINISPA", "13012.32"),
            ("ВТБ", "3000"),
            ("Сбербанк", "2001.27"),
            ("ИММО-ПУШ", "216.48"),
        ],
        start=1,
    ):
        cells.append(_c("Portfolio", f"A{row}", name))
        cells.append(_c("Portfolio", f"B{row}", amount))
    block = detect_layout(cells).sheets[0].blocks[0]
    assert block.kind == "params"
    assert [row.label for row in block.rows] == ["RINISPA", "ВТБ", "Сбербанк", "ИММО-ПУШ"]
    assert [(header.col, header.role, header.text) for header in block.axis.headers] == [
        (2, "value", "")
    ]
    assert {cell.role for cell in block.rows[0].cells} == {"value"}


def test_hidden_param_row_joins_the_table() -> None:
    cells = [
        _c("Inputs", "A1", "Параметр"),
        _c("Inputs", "B1", "Значение"),
        _c("Inputs", "A2", "Цена"),
        _c("Inputs", "B2", "10"),
        _c("Inputs", "A3", "Доля", hidden=True),
        _c("Inputs", "B3", "0.2", hidden=True),
        _c("Inputs", "A4", "Ставка"),
        _c("Inputs", "B4", "5"),
        _c("Inputs", "A5", "Налог"),
        _c("Inputs", "B5", "20"),
    ]
    block = detect_layout(cells).sheets[0].blocks[0]
    by_label = {row.label: row for row in block.rows}
    assert by_label["Доля"].hidden is True
    assert by_label["Доля"].kind == "fact"
    assert by_label["Цена"].hidden is False
    assert {cell.role for cell in by_label["Доля"].cells} == {"value"}


def test_cover_with_hidden_option_list_is_not_a_params_block() -> None:
    cells = [
        _c("Cover", "J4", "СБЕРБАНК РОССИИ"),
        _c("Cover", "S4", "41610"),
        _c("Cover", "J5", "МОДЕЛЬ ДЕНЕЖНЫХ ПОТОКОВ"),
        _c("Cover", "B14", "Структура модели"),
        _c("Cover", "D16", "Вход в модель и настройка основных опций"),
        _c("Cover", "D17", "Ввод данных"),
        _c("Cover", "D18", "Дополнительные показатели"),
        _c("Cover", "D19", "Денежные потоки"),
        _c("Cover", "D22", "Для начала работы"),
        _c("Cover", "W3", "В среднем", hidden=True),
        _c("Cover", "Y3", "В среднем", hidden=True),
        _c("Cover", "W18", "Maximum products", hidden=True),
        _c("Cover", "Y18", "10", hidden=True),
        _c("Cover", "Y19", "5", hidden=True),
        _c("Cover", "Y20", "5", hidden=True),
        _c("Cover", "Y21", "20", hidden=True),
        _c("Cover", "Y22", "10", hidden=True),
    ]
    edges = [
        {"source": "Other!B19", "target": "Cover!E35"},
        {"source": "Other!J19", "target": "Cover!E36"},
    ]
    layout = detect_layout(cells, edges=edges)
    assert layout.sheets[0].blocks == []


def test_unknown_short_param_header_is_not_a_scenario_column() -> None:
    heads = ["Параметр", "Значение", "Прочее"]
    cells = [_c("Допущения", f"{col}1", head) for col, head in zip("ABC", heads, strict=True)]
    for r, label in enumerate(["Цена", "Доля", "Ставка"], start=2):
        cells += [
            _c("Допущения", f"A{r}", label),
            _c("Допущения", f"B{r}", "12"),
            _c("Допущения", f"C{r}", "альфа"),
        ]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert [(header.role, header.text) for header in block.axis.headers] == [
        ("value", "Значение"),
        ("unknown", "Прочее"),
    ]


@pytest.mark.parametrize(
    "heads",
    [
        ["Параметр", "Базовый", "Консервативный", "Оптимистичный"],
        ["Параметр", "Сценарий 1", "Сценарий 2", "Стресс"],
        ["Параметр", "Базовый сценарий", "Пессимистичный", "2 сценарий"],
        ["Parameter", "Base case", "Upside", "Downside"],
    ],
)
def test_case_names_are_value_columns_with_their_own_keys(heads: list[str]) -> None:
    cells = [_c("Cases", f"{col}1", head) for col, head in zip("ABCD", heads, strict=True)]
    for r, label in enumerate(["Цена", "Доля", "Ставка"], start=2):
        cells.append(_c("Cases", f"A{r}", label))
        for col, amount in zip("BCD", ["10", "11", "12"], strict=True):
            cells.append(_c("Cases", f"{col}{r}", amount))
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [header.role for header in headers] == ["value", "value", "value"]
    assert [header.text for header in headers] == heads[1:]
    assert [header.period_key for header in headers] == [
        _value_key(text, col) for text, col in zip(heads[1:], (2, 3, 4), strict=True)
    ]


def test_a_digit_only_slug_does_not_split_shared_value_columns() -> None:
    """Long captions whose letters do not survive the slug keep the key ``value``."""
    heads = [
        "Параметр",
        "альфа бета гамма 11112222",
        "дельта эпсилон зета 33334444",
    ]
    cells = [_c("Cases", f"{col}1", head) for col, head in zip("ABC", heads, strict=True)]
    for row, label in enumerate(["Цена", "Доля", "Ставка"], start=2):
        cells += [
            _c("Cases", f"A{row}", label),
            _c("Cases", f"B{row}", "10"),
            _c("Cases", f"C{row}", "11"),
        ]
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [(header.role, header.period_key) for header in headers] == [
        ("value", "value"),
        ("value", "value"),
    ]
    assert [header.text for header in headers] == heads[1:]


def test_case_run_name_comes_from_the_row_label() -> None:
    """The 1..n cells are values. The header is the row label plus the place."""
    years = ["2020", "2021", "2022", "2023", "2024", "2025"]
    cells = [_c("Model", "A1", "Item")]
    cells += [_c("Model", f"{col}1", year) for col, year in zip("BCDEFG", years, strict=True)]
    cells += [
        _c("Model", "A2", "Case Number"),
        _c("Model", "B2", "1"),
        _c("Model", "C2", "2"),
        _c("Model", "D2", "3"),
        _c("Model", "A3", "Price"),
        _c("Model", "B3", "10"),
        _c("Model", "C3", "11"),
        _c("Model", "D3", "12"),
    ]
    blocks = detect_layout(cells).sheets[0].blocks
    case = next(block for block in blocks if block.kind == "params")
    assert [(header.text, header.role, header.period_key) for header in case.axis.headers] == [
        ("Case 1", "value", "case-1"),
        ("Case 2", "value", "case-2"),
        ("Case 3", "value", "case-3"),
    ]


def test_case_run_with_a_cyrillic_label_does_not_use_the_cell_digit() -> None:
    years = ["2020", "2021", "2022", "2023", "2024", "2025"]
    cells = [_c("Model", "A1", "Item")]
    cells += [_c("Model", f"{col}1", year) for col, year in zip("BCDEFG", years, strict=True)]
    cells += [
        _c("Model", "A2", "Кейс №"),
        _c("Model", "B2", "1"),
        _c("Model", "C2", "2"),
        _c("Model", "D2", "3"),
        _c("Model", "A3", "Цена"),
        _c("Model", "B3", "10"),
        _c("Model", "C3", "11"),
        _c("Model", "D3", "12"),
    ]
    blocks = detect_layout(cells).sheets[0].blocks
    case = next(block for block in blocks if block.kind == "params")
    headers = case.axis.headers
    assert [header.text for header in headers] == ["Кейс 1", "Кейс 2", "Кейс 3"]
    assert [header.period_key for header in headers] == ["value-b", "value-c", "value-d"]


def test_a_value_caption_keeps_the_value_key_beside_case_columns() -> None:
    heads = ["Параметр", "Значение", "Base", "Upside"]
    cells = [_c("Cases", f"{col}1", head) for col, head in zip("ABCD", heads, strict=True)]
    for row, label in enumerate(["Цена", "Доля", "Ставка"], start=2):
        cells.append(_c("Cases", f"A{row}", label))
        for col, amount in zip("BCD", ["10", "11", "12"], strict=True):
            cells.append(_c("Cases", f"{col}{row}", amount))
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [(header.text, header.role, header.period_key) for header in headers] == [
        ("Значение", "value", "value"),
        ("Base", "value", "base"),
        ("Upside", "value", "upside"),
    ]


def test_numeric_case_columns_are_values_and_a_short_caption_stays_unknown() -> None:
    heads = ["Параметр", "Base", "Bull", "Отрасль"]
    cells = [_c("Cases", f"{col}1", head) for col, head in zip("ABCD", heads, strict=True)]
    body = [
        ("Цена", "10", "12", "Все"),
        ("Доля", "0.2", "0.3", "Все"),
        ("Ставка", "0.1", "0.2", "Все"),
    ]
    for r, (label, base, bull, industry) in enumerate(body, start=2):
        cells += [
            _c("Cases", f"A{r}", label),
            _c("Cases", f"B{r}", base),
            _c("Cases", f"C{r}", bull),
            _c("Cases", f"D{r}", industry),
        ]
    block = detect_layout(cells).sheets[0].blocks[0]
    headers = {header.text: header.role for header in block.axis.headers}
    assert headers["Base"] == "value"
    assert headers["Bull"] == "value"
    assert headers["Отрасль"] == "unknown"
    keys = {header.text: header.period_key for header in block.axis.headers}
    assert keys["Base"] == "base"
    assert keys["Bull"] == "bull"


@pytest.mark.parametrize("caption", ["№ п/п", "№", "#", "No."])
def test_row_number_caption_is_an_index_column(caption: str) -> None:
    cells = [
        _c("FLM", "A1", caption),
        _c("FLM", "B1", "Показатель"),
        _c("FLM", "C1", "Значение"),
    ]
    for r, (number, name, amount) in enumerate(
        [("1", "Инфляция", "0.05"), ("2", "Доходы", "0.04"), ("3", "EBITDA", "0.03")],
        start=2,
    ):
        cells += [
            _c("FLM", f"A{r}", number),
            _c("FLM", f"B{r}", name),
            _c("FLM", f"C{r}", amount),
        ]
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [(header.role, header.text) for header in headers] == [
        ("index", caption),
        ("value", "Значение"),
    ]


def test_note_head_word_keeps_a_short_qualifier() -> None:
    from finance_context.layout.params import _header_role

    assert _header_role("Комментарии КП") == "note"
    assert _header_role("Комментарий руководителя") == "note"
    assert _header_role("Источник информации") == "note"
    assert _header_role("Примечания ПМ") == "note"
    assert _header_role("Note(s)") == "note"
    assert _header_role("Source of information") == "note"
    assert _header_role("Sources of information") == "note"
    assert _header_role("Comments (KP)") == "note"
    assert _header_role("Выводы ПМ") == "note"
    assert _header_role("Мероприятия") == "note"
    assert _header_role("Отрасль") is None
    assert _header_role("Периодичность") is None
    assert _header_role("Значение, руб.") == "value"
    assert _header_role("Источник информации для всей модели и всех приложений") is None
    assert _header_role("Значение базовое") == "value"
    assert _header_role("Value upside") == "value"


def test_qualified_note_columns_stay_notes_and_scenario_captions_stay_scenarios() -> None:
    heads = ["Параметр", "Комментарии КП", "Источник информации", "Значение базовое", "Отрасль"]
    cells = [_c("FLM", f"{col}1", head) for col, head in zip("ABCDE", heads, strict=True)]
    for row, (label, comment, source, amount, industry) in enumerate(
        [
            ("Инфляция", "прогноз ЦМИ", "СМИ", "0.05", "Все"),
            ("Доходы", "прогноз ЦМИ", "СМИ", "0.04", "Все"),
            ("EBITDA", "отчётность", "РСБУ", "0.03", "Все"),
        ],
        start=2,
    ):
        cells += [
            _c("FLM", f"A{row}", label),
            _c("FLM", f"B{row}", comment),
            _c("FLM", f"C{row}", source),
            _c("FLM", f"D{row}", amount),
            _c("FLM", f"E{row}", industry),
        ]
    headers = {
        header.text: header.role
        for header in detect_layout(cells).sheets[0].blocks[0].axis.headers
    }
    assert headers["Комментарии КП"] == "note"
    assert headers["Источник информации"] == "note"
    assert headers["Значение базовое"] == "value"
    assert headers["Отрасль"] == "unknown"


def test_unit_and_note_columns_follow_the_values_under_an_unknown_header() -> None:
    heads = ["Параметр", "Base", "Bull", "Мера", "Пояснение"]
    cells = [_c("FLM", f"{col}1", head) for col, head in zip("ABCDE", heads, strict=True)]
    notes = [
        "допущение взято из бизнес-плана",
        "смотри приложение к этой модели",
        "пояснение для инвестиционного комитета",
    ]
    for row, (label, base, bull, unit, note) in enumerate(
        zip(
            ["Цена", "Объём", "Ставка"],
            ["10", "11", "12"],
            ["20", "21", "22"],
            ["руб.", "тыс. руб.", "EUR"],
            notes,
            strict=True,
        ),
        start=2,
    ):
        cells += [
            _c("FLM", f"A{row}", label),
            _c("FLM", f"B{row}", base),
            _c("FLM", f"C{row}", bull),
            _c("FLM", f"D{row}", unit),
            _c("FLM", f"E{row}", note),
        ]
    headers = {
        header.text: header.role
        for header in detect_layout(cells).sheets[0].blocks[0].axis.headers
    }
    assert headers["Base"] == "value"
    assert headers["Bull"] == "value"
    assert headers["Мера"] == "unit"
    assert headers["Пояснение"] == "note"
