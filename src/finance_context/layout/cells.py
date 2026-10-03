from __future__ import annotations

from finance_context.layout.periods import display_cell_text


def _cell_at(row_cells: list[dict], col: int) -> dict | None:
    for cell in row_cells:
        if int(cell["col"]) == col:
            return cell
    return None


def _text(cell: dict, date1904: bool = False) -> str | None:
    return display_cell_text(
        cell.get("cached_value") if cell.get("cached_value") is not None else None,
        cell.get("number_format"),
        date1904=date1904,
    )


def _is_number(text: str) -> bool:
    try:
        float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return True


def _is_int_text(text: str) -> bool:
    try:
        value = float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return value == int(value)
