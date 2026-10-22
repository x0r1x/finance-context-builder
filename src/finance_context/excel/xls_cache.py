"""Copy cached values from a legacy ``.xls`` onto the converted ``.xlsx``.

LibreOffice recalculates on load. Volatile formulas, circular iteration and
``TODAY()`` then disagree with the cache Excel saved, and numbers lose two
significant digits. ``xlrd`` reads the BIFF cache; this module writes those
values back into the OOXML cells. Formula text stays. A sheet whose size or
name does not line up is logged and skipped where the cells do not overlap.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import xlrd
from lxml import etree

from finance_context.excel.a1 import parse_addr
from finance_context.excel.ooxml import (
    as_bool,
    children,
    find_child,
    iter_local,
    local_name,
    parse_rels,
    parse_xml,
    rel_id,
    zip_member,
)
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.store.fs import atomic_replace

_LOGGER = logging.getLogger("finance_context.excel")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_CACHE_TYPES = frozenset({"s", "str", "b", "e", "n", "inlineStr"})


@dataclass(frozen=True)
class _Cache:
    kind: str  # number, text, bool, error, empty
    text: str


def overlay_xls_cache(xls_path: Path, xlsx_path: Path) -> None:
    """Write the ``.xls`` cache onto ``xlsx_path``. Unreadable BIFF is logged and left."""
    try:
        book = xlrd.open_workbook(file_contents=xls_path.read_bytes())
    except xlrd.XLRDError as exc:
        _LOGGER.warning("xls cache overlay skipped for %s: %s", xls_path.name, exc)
        return
    with open_xlsx_zip(xlsx_path) as zf:
        sheets, date1904 = _xlsx_sheets(zf)
        sst = _shared_strings(zf)
        replacements: dict[str, bytes] = {}
        for xls_index, xlsx_index, by_order in _pairs(book, sheets):
            xls_sheet = book.sheet_by_index(xls_index)
            name, part = sheets[xlsx_index]
            if by_order:
                _LOGGER.warning(
                    "xls cache overlay: sheet %r paired with xlsx sheet %r by order",
                    xls_sheet.name,
                    name,
                )
            member = zip_member(zf, part)
            if member is None:
                _LOGGER.warning("xls cache overlay: xlsx sheet %r has no worksheet part", name)
                continue
            root = parse_xml(zf.read(member))
            _overlay_sheet(xls_sheet, root, sst, book.datemode, date1904, name)
            replacements[member] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
    if replacements:
        _rewrite(xlsx_path, replacements)


def _pairs(
    book: xlrd.Book, sheets: list[tuple[str, str]]
) -> list[tuple[int, int, bool]]:
    """``(xls index, xlsx index, paired by order)``. Names win, then leftover order."""
    by_name: dict[str, int] = {}
    for index, (name, _part) in enumerate(sheets):
        by_name.setdefault(name, index)
    used_xlsx: set[int] = set()
    pairs: list[tuple[int, int, bool]] = []
    unmatched: list[int] = []
    for index in range(book.nsheets):
        name = book.sheet_by_index(index).name
        xlsx_index = by_name.get(name)
        if xlsx_index is None or xlsx_index in used_xlsx:
            unmatched.append(index)
            continue
        used_xlsx.add(xlsx_index)
        pairs.append((index, xlsx_index, False))
    leftover = [index for index in range(len(sheets)) if index not in used_xlsx]
    for xls_index, xlsx_index in zip(unmatched, leftover, strict=False):
        pairs.append((xls_index, xlsx_index, True))
    if len(unmatched) != len(leftover):
        _LOGGER.warning(
            "xls cache overlay: %s xls sheets and %s xlsx sheets did not pair",
            len(unmatched),
            len(leftover),
        )
    return pairs


def _overlay_sheet(
    xls_sheet: xlrd.sheet.Sheet,
    root: etree._Element,
    sst: list[str],
    xls_datemode: int,
    xlsx_1904: bool,
    sheet_name: str,
) -> None:
    cells = _cell_map(root)
    xlsx_rows, xlsx_cols = _used_size(cells)
    if xls_sheet.nrows != xlsx_rows or xls_sheet.ncols != xlsx_cols:
        _LOGGER.warning(
            "xls cache overlay: sheet %r dimensions xls %sx%s xlsx %sx%s",
            sheet_name,
            xls_sheet.nrows,
            xls_sheet.ncols,
            xlsx_rows,
            xlsx_cols,
        )
    missing = 0
    for row in range(xls_sheet.nrows):
        for col in range(xls_sheet.ncols):
            desired = _from_xlrd(xls_sheet.cell(row, col), xls_datemode, xlsx_1904)
            element = cells.get((row + 1, col + 1))
            if element is None:
                if desired.kind != "empty":
                    missing += 1
                continue
            if _same(element, desired, sst):
                continue
            _write(element, desired)
    if missing:
        _LOGGER.warning(
            "xls cache overlay: sheet %r skipped %s cells missing from the xlsx",
            sheet_name,
            missing,
        )


def _from_xlrd(cell: xlrd.sheet.Cell, xls_datemode: int, xlsx_1904: bool) -> _Cache:
    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
        return _Cache("empty", "")
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return _Cache("text", str(cell.value))
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return _Cache("bool", "1" if cell.value else "0")
    if cell.ctype == xlrd.XL_CELL_ERROR:
        code = int(cell.value)
        return _Cache("error", xlrd.error_text_from_code.get(code, "#N/A"))
    if cell.ctype in {xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE}:
        value = float(cell.value)
        if cell.ctype == xlrd.XL_CELL_DATE and (xls_datemode == 1) != xlsx_1904:
            value = _serial_in_system(value, xls_1904=xls_datemode == 1, xlsx_1904=xlsx_1904)
        return _Cache("number", _number_text(value))
    return _Cache("empty", "")


def _number_text(value: float) -> str:
    """17 significant digits when 16 do not round-trip the BIFF float."""
    text = format(value, ".16g")
    if float(text) != value:
        text = format(value, ".17g")
    return text


def _serial_in_system(value: float, *, xls_1904: bool, xlsx_1904: bool) -> float:
    if xls_1904:
        moment = datetime(1904, 1, 1) + timedelta(days=value)
    else:
        moment = datetime(1899, 12, 30) + timedelta(days=value)
    origin = datetime(1904, 1, 1) if xlsx_1904 else datetime(1899, 12, 30)
    delta = moment - origin
    return delta.days + delta.seconds / 86400 + delta.microseconds / 86400_000_000


def _same(element: etree._Element, desired: _Cache, sst: list[str]) -> bool:
    current = _current(element, sst)
    if current.kind != desired.kind:
        return False
    if desired.kind == "number":
        try:
            return float(current.text) == float(desired.text)
        except ValueError:
            return False
    return current.text == desired.text


def _current(element: etree._Element, sst: list[str]) -> _Cache:
    cell_type = element.get("t")
    if cell_type == "inlineStr":
        is_el = find_child(element, "is")
        text = ""
        if is_el is not None:
            text = "".join(node.text or "" for node in iter_local(is_el, "t"))
        return _Cache("text", text) if text else _Cache("empty", "")
    value = find_child(element, "v")
    if value is None or value.text is None:
        return _Cache("empty", "")
    raw = value.text
    if cell_type == "s":
        try:
            index = int(raw)
        except ValueError:
            return _Cache("text", raw)
        if 0 <= index < len(sst):
            return _Cache("text", sst[index])
        return _Cache("text", raw)
    if cell_type == "b":
        return _Cache("bool", "1" if raw in {"1", "true", "TRUE"} else "0")
    if cell_type == "e":
        return _Cache("error", raw)
    if cell_type == "str":
        return _Cache("text", raw)
    return _Cache("number", raw)


def _write(element: etree._Element, desired: _Cache) -> None:
    formula = find_child(element, "f") is not None
    _clear_cache(element)
    if desired.kind == "empty":
        return
    if desired.kind == "text" and not formula:
        _write_inline(element, desired.text)
        return
    if desired.kind == "text":
        element.set("t", "str")
    elif desired.kind == "bool":
        element.set("t", "b")
    elif desired.kind == "error":
        element.set("t", "e")
    value = etree.Element(_qn(element, "v"))
    value.text = desired.text
    formula_el = find_child(element, "f")
    if formula_el is not None:
        formula_el.addnext(value)
    else:
        element.append(value)


def _write_inline(element: etree._Element, text: str) -> None:
    element.set("t", "inlineStr")
    inline = etree.Element(_qn(element, "is"))
    node = etree.Element(_qn(element, "t"))
    if text[:1].isspace() or text[-1:].isspace() or "\n" in text:
        node.set(_XML_SPACE, "preserve")
    node.text = text
    inline.append(node)
    element.append(inline)


def _clear_cache(element: etree._Element) -> None:
    for child in list(element):
        if local_name(child.tag) in {"v", "is"}:
            element.remove(child)
    if element.get("t") in _CACHE_TYPES:
        del element.attrib["t"]


def _qn(element: etree._Element, name: str) -> str:
    tag = element.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.rsplit("}", 1)[0] + "}" + name
    return name


def _cell_map(root: etree._Element) -> dict[tuple[int, int], etree._Element]:
    found: dict[tuple[int, int], etree._Element] = {}
    for element in iter_local(root, "c"):
        ref = element.get("r")
        if not ref:
            continue
        try:
            col, row = parse_addr(ref)
        except ValueError:
            continue
        found[(row, col)] = element
    return found


def _used_size(cells: dict[tuple[int, int], etree._Element]) -> tuple[int, int]:
    if not cells:
        return 0, 0
    return max(row for row, _col in cells), max(col for _row, col in cells)


def _xlsx_sheets(zf: zipfile.ZipFile) -> tuple[list[tuple[str, str]], bool]:
    root_rels = parse_rels(zf, "_rels/.rels")
    workbook = next(
        (rel.target for rel in root_rels.values() if rel.type.endswith("/officeDocument")),
        "xl/workbook.xml",
    )
    member = zip_member(zf, workbook)
    if member is None:
        return [], False
    root = parse_xml(zf.read(member))
    date1904 = False
    props = find_child(root, "workbookPr")
    if props is not None:
        date1904 = as_bool(props.get("date1904"))
    rels = parse_rels(zf, _rels_for(workbook))
    sheets: list[tuple[str, str]] = []
    parent = find_child(root, "sheets")
    if parent is None:
        return sheets, date1904
    for sheet in children(parent, "sheet"):
        rid = rel_id(sheet)
        if rid is None or rid not in rels:
            continue
        sheets.append((sheet.get("name") or "Sheet", rels[rid].target))
    return sheets, date1904


def _rels_for(part: str) -> str:
    parent, name = part.rsplit("/", 1) if "/" in part else ("", part)
    rels = f"{parent}/_rels/{name}.rels" if parent else f"_rels/{name}.rels"
    return rels


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    root_rels = parse_rels(zf, "_rels/.rels")
    workbook = next(
        (rel.target for rel in root_rels.values() if rel.type.endswith("/officeDocument")),
        "xl/workbook.xml",
    )
    rels = parse_rels(zf, _rels_for(workbook))
    part = next((rel.target for rel in rels.values() if rel.type.endswith("/sharedStrings")), None)
    if part is None:
        return []
    member = zip_member(zf, part)
    if member is None:
        return []
    root = parse_xml(zf.read(member))
    return [
        "".join(node.text or "" for node in iter_local(item, "t"))
        for item in children(root, "si")
    ]


def _rewrite(path: Path, replacements: dict[str, bytes]) -> None:
    temporary = path.with_name(f".{path.name}.overlay")
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as target:
            for info in source.infolist():
                data = replacements.get(info.filename, source.read(info.filename))
                clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                target.writestr(clone, data)
        atomic_replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
