from __future__ import annotations

import re
from collections import Counter, defaultdict

from finance_context.excel.a1 import parse_addr
from finance_context.layout.models import Axis, AxisHeader, Block, LayoutRow, RowCell
from finance_context.layout.periods import display_cell_text
from finance_context.models.context import DisplayUnit

_HEADER_ROLES: dict[str, str] = {
    "parameter": "label",
    "parameters": "label",
    "item": "label",
    "check": "label",
    "assumption": "label",
    "assumptions": "label",
    "value": "value",
    "values": "value",
    "active": "value",
    "result": "value",
    "chosen": "value",
    "unit": "unit",
    "units": "unit",
    "note": "note",
    "notes": "note",
}
_UNIT_TEXT = re.compile(
    r"^(k?£|k?\$|€|₽|%|years?|year|months?|month|days?|day|"
    r"veh/?year|per year|of margin|mw|mwh|£/year|\$/year|text|date|"
    r"1/2/3|k£)$",
    re.IGNORECASE,
)
_PROSE_MIN = 80
_MIN_VALUE_ROWS = 3
_CHECK = re.compile(r"check|проверк|контроль|tie[- ]?out", re.IGNORECASE)
_CAPS_SECTION = re.compile(r"^[A-Z0-9][A-Z0-9 &/,'-]{2,}$")


def detect_params_block(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
    edges: list[dict] | None = None,
) -> Block | None:
    if _looks_like_grid(by_row, date1904):
        return _grid_title_block(sheet, by_row, date1904)
    roles = _column_roles(by_row, date1904, sheet, edges or [])
    label_col = _label_column(by_row, date1904, roles)
    value_cols = [col for col, role in roles.items() if role in {"value", "scenario"}]
    if label_col is None or not value_cols:
        return None
    header_rows, header_row = _header_rows(by_row, date1904, roles)
    check_table = _is_check_table(by_row, header_rows, date1904)
    data_rows = _params_data_rows(
        by_row, date1904, label_col, roles, header_rows, value_cols, check_table
    )
    content_rows = [row for row in data_rows if row.kind in {"fact", "helper"}]
    if len(content_rows) < _MIN_VALUE_ROWS:
        return None
    headers = _axis_headers(roles, by_row, date1904, header_rows, header_row)
    if not headers:
        return None
    return Block(
        block_id=f"{sheet}!r{header_row}",
        label_col=label_col,
        axis=Axis(id=f"{sheet}!r{header_row}", row=header_row, headers=headers),
        rows=data_rows,
        kind="params",
    )


def attach_stub_cells(
    row: LayoutRow,
    row_cells: list[dict],
    *,
    label_span: list[int],
    period_cols: set[int],
    date1904: bool,
) -> LayoutRow:
    if not period_cols:
        return row
    left_edge = min(period_cols)
    span_max = max(label_span) if label_span else 0
    tagged: list[RowCell] = []
    for cell in row_cells:
        col = int(cell["col"])
        if col <= span_max or col >= left_edge or col in period_cols:
            continue
        role = _stub_role(cell, row.row, period_cols, date1904)
        if role is None:
            continue
        tagged.append(RowCell(col=col, role=role))  # type: ignore[arg-type]
    if tagged:
        row.cells = tagged
    return row


def unit_kind_from_text(text: str | None) -> str | None:
    blob = (text or "").strip().casefold()
    if not blob:
        return None
    if "%" in blob or blob in {"per year", "of margin"}:
        return "rate"
    if any(token in blob for token in ("£", "$", "€", "₽")):
        return "money"
    if any(token in blob for token in ("year", "month", "day", "veh", "mw", "count")):
        return "count"
    if blob in {"text", "date"}:
        return None
    if _UNIT_TEXT.fullmatch(blob):
        return "count"
    return None


def parse_display_unit(text: str | None) -> DisplayUnit | None:
    raw = (text or "").strip()
    if not raw:
        return None
    folded = raw.casefold()
    compact = folded.replace(" ", "")
    dimension = unit_kind_from_text(raw)
    per = _period_unit(folded, compact)
    currency = _currency_code(raw, compact)
    scale: float | None = None
    if currency is not None:
        scale = 1000.0 if _thousands_scale(compact) else 1.0
    kind = dimension if dimension in {"money", "rate", "ratio", "count"} else None
    return DisplayUnit(raw=raw, dimension=kind, currency=currency, scale=scale, per=per)


def _period_unit(folded: str, compact: str) -> str | None:
    if compact in {"year", "years", "month", "months", "day", "days"}:
        return None
    if compact.endswith("/year") or "peryear" in compact or folded.strip() == "per year":
        return "year"
    if compact.endswith("/month") or "permonth" in compact:
        return "month"
    if compact.endswith("/day") or "perday" in compact:
        return "day"
    return None


def _currency_code(raw: str, compact: str) -> str | None:
    if "£" in raw or "gbp" in compact:
        return "GBP"
    if "$" in raw or "usd" in compact:
        return "USD"
    if "€" in raw or "eur" in compact:
        return "EUR"
    if "₽" in raw or "rub" in compact:
        return "RUB"
    return None


def _thousands_scale(compact: str) -> bool:
    return bool(
        compact.startswith("k")
        or "k£" in compact
        or "k$" in compact
        or "k€" in compact
        or "£k" in compact
        or "$k" in compact
        or "'000" in compact
        or "000" in compact and compact.startswith("£")
    )


def _looks_like_grid(by_row: dict[int, list[dict]], date1904: bool) -> bool:
    numeric_labels = 0
    numeric_bodies = 0
    for _row_n, cells in by_row.items():
        label = _left_text(cells, date1904)
        if not label or not _is_number(label):
            continue
        label_col = min(
            int(cell["col"]) for cell in cells if _text(cell, date1904) == label
        )
        right = [
            cell
            for cell in cells
            if _is_number(_text(cell, date1904) or "") and int(cell["col"]) > label_col
        ]
        if len(right) >= 3:
            numeric_labels += 1
            numeric_bodies += 1
    return numeric_labels >= 3 and numeric_bodies >= 3


def _grid_title_block(
    sheet: str, by_row: dict[int, list[dict]], date1904: bool
) -> Block | None:
    title_row = None
    title = None
    for row_n in sorted(by_row):
        text = _left_text(by_row[row_n], date1904)
        if not text or _is_number(text):
            continue
        if len(text) < 8:
            continue
        title_row = row_n
        title = text
        break
    if title_row is None or title is None:
        return None
    return Block(
        block_id=f"{sheet}!r{title_row}",
        label_col=1,
        axis=Axis(id=f"{sheet}!r{title_row}", row=title_row, headers=[]),
        rows=[
            LayoutRow(
                row=title_row,
                label=title,
                kind="abstract",
                label_col=1,
            )
        ],
        kind="params",
    )


def _column_roles(
    by_row: dict[int, list[dict]],
    date1904: bool,
    sheet: str,
    edges: list[dict],
) -> dict[int, str]:
    header_votes: dict[int, Counter[str]] = defaultdict(Counter)
    numeric_counts: Counter[int] = Counter()
    text_counts: Counter[int] = Counter()
    for _row_n, cells in by_row.items():
        for cell in cells:
            col = int(cell["col"])
            text = _text(cell, date1904)
            if not text:
                continue
            role = _header_role(text)
            if role:
                header_votes[col][role] += 1
            if _is_number(text) or cell.get("formula_raw"):
                numeric_counts[col] += 1
            elif len(text) <= 24:
                text_counts[col] += 1
    roles: dict[int, str] = {}
    for col, votes in header_votes.items():
        roles[col] = votes.most_common(1)[0][0]
    for cells in by_row.values():
        if not any(_header_role(_text(cell, date1904) or "") for cell in cells):
            continue
        for cell in cells:
            col = int(cell["col"])
            if col in roles:
                continue
            text = _text(cell, date1904)
            if text and not _is_number(text) and len(text) <= 24:
                roles[col] = "scenario"
    graph_col = _active_value_col(edges, sheet)
    if graph_col is not None:
        roles[graph_col] = "value"
        for col, role in list(roles.items()):
            if role == "value" and col != graph_col:
                roles[col] = "scenario"
    for col, count in numeric_counts.items():
        if col in roles:
            continue
        if count >= _MIN_VALUE_ROWS:
            roles[col] = "scenario"
    return roles


def _label_column(
    by_row: dict[int, list[dict]], date1904: bool, roles: dict[int, str]
) -> int | None:
    explicit = [col for col, role in roles.items() if role == "label"]
    if explicit:
        return min(explicit)
    counts: Counter[int] = Counter()
    for cells in by_row.values():
        for cell in sorted(cells, key=lambda item: int(item["col"])):
            if cell.get("hidden"):
                continue
            text = _text(cell, date1904)
            col = int(cell["col"])
            if roles.get(col) in {"value", "unit", "scenario", "note"}:
                continue
            if not text or _is_number(text) or len(text) > _PROSE_MIN:
                continue
            counts[col] += 1
            break
    if not counts:
        return None
    return min(counts, key=lambda col: (-counts[col], col))


def _header_rows(
    by_row: dict[int, list[dict]], date1904: bool, roles: dict[int, str]
) -> tuple[set[int], int]:
    hits: list[int] = []
    for row_n in sorted(by_row):
        named = 0
        scenario_names = 0
        for cell in by_row[row_n]:
            text = _text(cell, date1904)
            if not text:
                continue
            if _header_role(text):
                named += 1
            elif roles.get(int(cell["col"])) in {"scenario", "value"} and not _is_number(text):
                scenario_names += 1
        if named >= 1 or scenario_names >= 2:
            hits.append(row_n)
    if not hits:
        first = min(by_row)
        return {first}, first
    return set(hits), max(hits)


def _params_data_rows(
    by_row: dict[int, list[dict]],
    date1904: bool,
    label_col: int,
    roles: dict[int, str],
    header_rows: set[int],
    value_cols: list[int],
    check_table: bool = False,
) -> list[LayoutRow]:
    out: list[LayoutRow] = []
    section_stack: list[LayoutRow] = []
    for row_n in sorted(by_row):
        if row_n in header_rows:
            continue
        cells = by_row[row_n]
        label_cell = _cell_at(cells, label_col)
        label = _text(label_cell, date1904) if label_cell else _left_text(cells, date1904)
        if not label:
            continue
        if _is_number(label):
            continue
        tagged = _row_cells(cells, roles, label_col, date1904)
        numeric_value = False
        for item in tagged:
            if item.role not in {"value", "scenario"}:
                continue
            cell = _cell_at(cells, item.col)
            text = _text(cell, date1904) if cell else None
            if cell and cell.get("formula_raw"):
                numeric_value = True
                break
            if text and _is_number(text):
                numeric_value = True
                break
        check_row = bool(_CHECK.search(label)) or check_table
        if _is_section_label(label, numeric_value):
            kind = "abstract"
        elif not numeric_value:
            if len(label) >= _PROSE_MIN:
                continue
            kind = "abstract"
        else:
            kind = "helper" if check_row else "fact"
        indent = 1 if kind != "abstract" and section_stack else 0
        if kind == "abstract":
            section_stack = [LayoutRow(row=row_n, label=label.strip(), kind="abstract")]
            indent = 0
        parent_row = section_stack[-1].row if section_stack and kind != "abstract" else None
        item = LayoutRow(
            row=row_n,
            label=label.strip(),
            parent_row=parent_row,
            indent=indent,
            check_row=check_row and kind != "abstract",
            kind=kind,
            section_path=[section_stack[-1].label] if section_stack and kind != "abstract" else [],
            label_col=int(label_cell["col"]) if label_cell else label_col,
            cells=tagged,
        )
        out.append(item)
    return out


def _row_cells(
    cells: list[dict], roles: dict[int, str], label_col: int, date1904: bool
) -> list[RowCell]:
    tagged: list[RowCell] = []
    for cell in cells:
        col = int(cell["col"])
        if col == label_col:
            continue
        role = roles.get(col)
        text = _text(cell, date1904)
        formula = cell.get("formula_raw")
        if role is None:
            if not text and not formula:
                continue
            if text and len(text) >= _PROSE_MIN:
                role = "note"
            elif text and _UNIT_TEXT.fullmatch(text.strip()):
                role = "unit"
            elif formula or (text and (_is_number(text) or len(text) <= 24)):
                role = "value"
            else:
                continue
        if role == "note" and text and len(text) < 4:
            continue
        tagged.append(RowCell(col=col, role=role))  # type: ignore[arg-type]
    return tagged


def _axis_headers(
    roles: dict[int, str],
    by_row: dict[int, list[dict]],
    date1904: bool,
    header_rows: set[int],
    header_row: int,
) -> list[AxisHeader]:
    names: dict[int, str] = {}
    for row_n in sorted(header_rows):
        for cell in by_row[row_n]:
            col = int(cell["col"])
            text = _text(cell, date1904)
            if not text:
                continue
            if _header_role(text) in {None, "label"} and roles.get(col) not in {
                "scenario",
                "value",
                "unit",
                "note",
            }:
                continue
            names[col] = text
    headers: list[AxisHeader] = []
    for col, role in sorted(roles.items()):
        if role in {"label"}:
            continue
        text = names.get(col) or role
        headers.append(
            AxisHeader(
                col=col,
                text=text,
                role=role,  # type: ignore[arg-type]
                period_key=_slug(text) if role == "scenario" else role,
            )
        )
    return headers


def _stub_role(
    cell: dict, row: int, period_cols: set[int], date1904: bool
) -> str | None:
    text = _text(cell, date1904)
    formula = str(cell.get("formula_raw") or "")
    template = str(cell.get("formula_template") or "")
    if _is_same_row_total(formula, template, row, period_cols):
        return "total"
    if text and _UNIT_TEXT.fullmatch(text.strip()):
        return "unit"
    if text and len(text) <= 8 and not _is_number(text) and not formula:
        if any(ch in text for ch in "%£$€₽/"):
            return "unit"
    if formula or (text and _is_number(text)):
        return "value"
    if text and len(text) >= _PROSE_MIN:
        return "note"
    return None


def _is_same_row_total(
    formula: str, template: str, row: int, period_cols: set[int]
) -> bool:
    blob = f"{formula} {template}".upper()
    if "SUM(" not in blob:
        return False
    if re.search(rf"SUM\([A-Z]+\${row}:[A-Z]+\${row}\)", formula, re.I):
        return True
    if re.search(rf"SUM\([A-Z]+{row}:[A-Z]+{row}\)", formula, re.I):
        return True
    if "SUM(" in template.upper() and "R[" not in template.upper().replace("RC", "C"):
        return True
    return False


def _active_value_col(edges: list[dict], sheet: str) -> int | None:
    counts: Counter[int] = Counter()
    prefix = sheet.casefold()
    for edge in edges:
        target = str(edge.get("target") or "")
        source = str(edge.get("source") or "")
        parsed_t = _parse_sheet_addr(target)
        parsed_s = _parse_sheet_addr(source)
        if parsed_t is None or parsed_s is None:
            continue
        t_sheet, col, _row = parsed_t
        s_sheet, _s_col, _s_row = parsed_s
        if t_sheet.casefold() != prefix:
            continue
        if s_sheet.casefold() == prefix:
            continue
        counts[col] += 1
    if not counts:
        return None
    col, n = counts.most_common(1)[0]
    return col if n >= 2 else None


def _parse_sheet_addr(ref: str) -> tuple[str, int, int] | None:
    if "!" not in ref:
        return None
    sheet, addr = ref.rsplit("!", 1)
    sheet = sheet.strip().strip("'").replace("''", "'")
    addr = addr.split(":")[0].replace("$", "")
    try:
        col, row = parse_addr(addr)
    except ValueError:
        return None
    return sheet, col, row


def _is_check_table(
    by_row: dict[int, list[dict]], header_rows: set[int], date1904: bool
) -> bool:
    for row_n in header_rows:
        for cell in by_row[row_n]:
            text = (_text(cell, date1904) or "").casefold().strip()
            if text in {"check", "result"}:
                return True
    return False


def _header_role(text: str) -> str | None:
    n = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    if n in _HEADER_ROLES:
        return _HEADER_ROLES[n]
    if n.startswith("scenario") and "chosen" in n:
        return "value"
    return None


def _is_section_label(label: str, has_value: bool) -> bool:
    if has_value:
        return False
    stripped = label.strip()
    if re.match(r"^[A-Z]\.\s", stripped) and len(stripped) <= 48:
        return True
    if _CAPS_SECTION.match(stripped) and not _is_number(stripped):
        return True
    if stripped.endswith(":") and len(stripped) <= 40:
        return True
    return stripped.isupper() and len(stripped.split()) <= 8


def _left_text(cells: list[dict], date1904: bool) -> str | None:
    for cell in sorted(cells, key=lambda item: int(item["col"])):
        if cell.get("hidden"):
            continue
        text = _text(cell, date1904)
        if text:
            return text
    return None


def _cell_at(cells: list[dict], col: int) -> dict | None:
    for cell in cells:
        if int(cell["col"]) == col:
            return cell
    return None


def _text(cell: dict | None, date1904: bool) -> str | None:
    if cell is None:
        return None
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


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug or "scenario"
