from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from finance_context.errors import ContextError
from finance_context.excel.a1 import formula_uses_semicolon, parse_addr, shift_formula
from finance_context.excel.models import DefinedName, RawCell, SheetInfo, WorkbookMeta
from finance_context.excel.zip_guard import read_part

NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_R_STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships"

PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    dtd_validation=False,
    load_dtd=False,
    huge_tree=False,
)

BUILTIN_FORMATS: dict[int, str] = {
    0: "General",
    1: "0",
    2: "0.00",
    3: "#,##0",
    4: "#,##0.00",
    9: "0%",
    10: "0.00%",
    11: "0.00E+00",
    14: "mm-dd-yy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM",
    20: "h:mm",
    21: "h:mm:ss",
    22: "m/d/yy h:mm",
    37: "#,##0 ;(#,##0)",
    38: "#,##0 ;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)",
    40: "#,##0.00;[Red](#,##0.00)",
    45: "mm:ss",
    46: "[h]:mm:ss",
    47: "mmss.0",
    49: "@",
}


@dataclass
class Rel:
    rid: str
    type: str
    target: str
    external: bool = False


@dataclass
class _PendingCell:
    addr: str
    col: int
    row: int
    formula: str | None
    shared_si: int | None
    cached: str | None
    hidden: bool
    number_format: str | None
    comment: str | None


@dataclass
class _SheetParse:
    info: SheetInfo
    part: str
    macrosheet: bool
    cells: list[RawCell] = field(default_factory=list)


def local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_xml(data: bytes) -> etree._Element:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return etree.fromstring(data, PARSER)
    except etree.XMLSyntaxError as exc:
        raise ContextError("zip_rejected", "invalid xml") from exc


def children(el: etree._Element, name: str) -> list[etree._Element]:
    return [child for child in el if local_name(child.tag) == name]


def find_child(el: etree._Element, name: str) -> etree._Element | None:
    for child in el:
        if local_name(child.tag) == name:
            return child
    return None


def iter_local(el: etree._Element, name: str) -> list[etree._Element]:
    return [node for node in el.iter() if local_name(node.tag) == name]


def rel_id(el: etree._Element) -> str | None:
    return (
        el.get(f"{{{NS_R}}}id")
        or el.get(f"{{{NS_R_STRICT}}}id")
        or el.get("id")
    )


def as_bool(value: str | None) -> bool:
    return value in {"1", "true", "True"}


def resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        out = posixpath.normpath(target.lstrip("/"))
    else:
        base = posixpath.dirname(source_part)
        out = posixpath.normpath(posixpath.join(base, target) if base else target)
    if ".." in out.split("/"):
        raise ContextError("zip_rejected", "zip-slip")
    return out


def source_part_for_rels(rels_part: str) -> str:
    dirname, name = posixpath.split(rels_part)
    if posixpath.basename(dirname) != "_rels":
        return posixpath.dirname(rels_part)
    parent = posixpath.dirname(dirname)
    source_name = name.removesuffix(".rels")
    if source_name in {"", "."}:
        return parent
    return posixpath.join(parent, source_name) if parent else source_name


def rels_path_for(part: str) -> str:
    dirname, name = posixpath.split(part)
    if dirname:
        return f"{dirname}/_rels/{name}.rels"
    return f"_rels/{name}.rels"


def zip_member(zf: zipfile.ZipFile, posix: str) -> str | None:
    wanted = posix.replace("\\", "/")
    for name in zf.namelist():
        if name.replace("\\", "/") == wanted:
            return name
    return None


def parse_rels(zf: zipfile.ZipFile, rels_part: str) -> dict[str, Rel]:
    member = zip_member(zf, rels_part)
    if member is None:
        return {}
    root = parse_xml(read_part(zf, member))
    source = source_part_for_rels(rels_part)
    out: dict[str, Rel] = {}
    for node in children(root, "Relationship"):
        rid = node.get("Id")
        typ = node.get("Type") or ""
        target = node.get("Target") or ""
        if not rid or not target:
            continue
        external = (node.get("TargetMode") or "") == "External"
        out[rid] = Rel(
            rid=rid,
            type=typ,
            target=target if external else resolve_target(source, target),
            external=external,
        )
    return out


def text_of(el: etree._Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext())


def parse_ooxml(zf: zipfile.ZipFile) -> tuple[list[RawCell], WorkbookMeta]:
    root_rels = parse_rels(zf, "_rels/.rels")
    wb_part = next(
        (rel.target for rel in root_rels.values() if rel.type.endswith("/officeDocument")),
        "xl/workbook.xml",
    )
    wb_member = zip_member(zf, wb_part)
    if wb_member is None:
        raise ContextError("zip_rejected", "missing workbook")
    wb_root = parse_xml(read_part(zf, wb_member))
    wb_rels = parse_rels(zf, rels_path_for(wb_part))

    sst = _load_sst(zf, wb_rels)
    formats = _load_formats(zf, wb_rels)
    externals = _load_externals(zf, wb_rels, wb_root)
    names, iterate = _load_names_and_iterate(wb_root)
    date1904 = False
    workbook_pr = find_child(wb_root, "workbookPr")
    if workbook_pr is not None:
        date1904 = as_bool(workbook_pr.get("date1904"))

    nameset = {name.replace("\\", "/") for name in zf.namelist()}
    has_vba = any(path.endswith("vbaProject.bin") for path in nameset)

    sheets: list[_SheetParse] = []
    has_xlm = False
    sheets_el = find_child(wb_root, "sheets")
    if sheets_el is not None:
        for sheet_el in children(sheets_el, "sheet"):
            name = sheet_el.get("name") or "Sheet"
            sheet_id = int(sheet_el.get("sheetId") or "0")
            hidden = (sheet_el.get("state") or "visible") != "visible"
            rid = rel_id(sheet_el)
            if rid is None or rid not in wb_rels:
                continue
            rel = wb_rels[rid]
            macrosheet = "macrosheet" in rel.type.lower() or "macrosheets/" in rel.target
            has_xlm = has_xlm or macrosheet
            sheets.append(
                _SheetParse(
                    info=SheetInfo(name=name, sheet_id=sheet_id, hidden=hidden),
                    part=rel.target,
                    macrosheet=macrosheet,
                )
            )

    has_xlm = has_xlm or any("/macrosheets/" in path for path in nameset)

    cells: list[RawCell] = []
    locale_ru = False
    for sheet in sheets:
        parsed, ru = _parse_sheet(zf, sheet, sst, formats)
        cells.extend(parsed)
        locale_ru = locale_ru or ru

    meta = WorkbookMeta(
        sheets=[sheet.info for sheet in sheets],
        has_vba=has_vba,
        has_xlm=has_xlm,
        externals=externals,
        locale_hint="ru" if locale_ru else "en",
        iterate=iterate,
        date1904=date1904,
        defined_names=names,
    )
    return cells, meta


def _load_sst(zf: zipfile.ZipFile, wb_rels: dict[str, Rel]) -> list[str]:
    part = next(
        (rel.target for rel in wb_rels.values() if rel.type.endswith("/sharedStrings")),
        "xl/sharedStrings.xml",
    )
    member = zip_member(zf, part)
    if member is None:
        return []
    root = parse_xml(read_part(zf, member))
    out: list[str] = []
    for si in children(root, "si"):
        out.append("".join(node.text or "" for node in iter_local(si, "t")))
    return out


def _load_formats(zf: zipfile.ZipFile, wb_rels: dict[str, Rel]) -> list[str | None]:
    part = next(
        (rel.target for rel in wb_rels.values() if rel.type.endswith("/styles")),
        "xl/styles.xml",
    )
    member = zip_member(zf, part)
    if member is None:
        return []
    root = parse_xml(read_part(zf, member))
    custom = dict(BUILTIN_FORMATS)
    num_fmts = find_child(root, "numFmts")
    if num_fmts is not None:
        for node in children(num_fmts, "numFmt"):
            fid = int(node.get("numFmtId") or "0")
            custom[fid] = node.get("formatCode") or custom.get(fid, "")
    xfs_parent = find_child(root, "cellXfs")
    if xfs_parent is None:
        return []
    out: list[str | None] = []
    for xf in children(xfs_parent, "xf"):
        fid = int(xf.get("numFmtId") or "0")
        code = custom.get(fid)
        out.append(None if not code or code == "General" else code)
    return out


def _load_externals(
    zf: zipfile.ZipFile, wb_rels: dict[str, Rel], wb_root: etree._Element
) -> list[str]:
    targets: list[str] = []
    for rel in wb_rels.values():
        if not rel.type.endswith("/externalLink"):
            continue
        link_rels = parse_rels(zf, rels_path_for(rel.target))
        for child in link_rels.values():
            if child.external:
                targets.append(child.target)
    # Fall back to raw r:id order if the link part is missing.
    refs = find_child(wb_root, "externalReferences")
    if refs is not None and not targets:
        for ref in children(refs, "externalReference"):
            rid = rel_id(ref)
            if rid and rid in wb_rels:
                targets.append(wb_rels[rid].target)
    return targets


def _load_names_and_iterate(wb_root: etree._Element) -> tuple[list[DefinedName], bool]:
    names: list[DefinedName] = []
    defined = find_child(wb_root, "definedNames")
    if defined is not None:
        for node in children(defined, "definedName"):
            name = node.get("name")
            if not name:
                continue
            names.append(
                DefinedName(
                    name=name,
                    formula=(node.text or "").strip(),
                    hidden=as_bool(node.get("hidden")),
                )
            )
    iterate = False
    calc = find_child(wb_root, "calcPr")
    if calc is not None:
        iterate = as_bool(calc.get("iterate")) or as_bool(calc.get("iterateEnabled"))
    return names, iterate


def _parse_sheet(
    zf: zipfile.ZipFile,
    sheet: _SheetParse,
    sst: list[str],
    formats: list[str | None],
) -> tuple[list[RawCell], bool]:
    member = zip_member(zf, sheet.part)
    if member is None:
        raise ContextError("zip_rejected", f"missing sheet {sheet.part}")
    root = parse_xml(read_part(zf, member))
    comments = _load_comments(zf, sheet.part)
    hidden_cols = _hidden_cols(root)
    pending: list[_PendingCell] = []
    masters: dict[int, tuple[int, int, str]] = {}

    sheet_data = find_child(root, "sheetData")
    if sheet_data is not None:
        for row_el in children(sheet_data, "row"):
            row_hidden = as_bool(row_el.get("hidden"))
            for cell_el in children(row_el, "c"):
                built = _pending_from_cell(
                    cell_el,
                    sst=sst,
                    formats=formats,
                    comments=comments,
                    hidden_cols=hidden_cols,
                    row_hidden=row_hidden,
                    sheet_hidden=sheet.info.hidden,
                )
                if built is None:
                    continue
                pending.append(built)
                if built.shared_si is not None and built.formula:
                    masters[built.shared_si] = (built.col, built.row, built.formula)

    locale_ru = False
    cells: list[RawCell] = []
    for item in pending:
        formula = item.formula
        if item.shared_si is not None and not formula and item.shared_si in masters:
            mcol, mrow, mformula = masters[item.shared_si]
            formula = shift_formula(mformula, mcol, mrow, item.col, item.row)
        if formula and formula_uses_semicolon(formula):
            locale_ru = True
        cells.append(
            RawCell(
                sheet=sheet.info.name,
                row=item.row,
                col=item.col,
                addr=item.addr,
                formula_raw=formula or None,
                cached_value=item.cached,
                hidden=item.hidden,
                number_format=item.number_format,
                comment=item.comment,
            )
        )
    return cells, locale_ru


def _hidden_cols(root: etree._Element) -> set[int]:
    hidden: set[int] = set()
    cols = find_child(root, "cols")
    if cols is None:
        return hidden
    for col in children(cols, "col"):
        if not as_bool(col.get("hidden")):
            continue
        start = int(col.get("min") or "1")
        end = int(col.get("max") or start)
        hidden.update(range(start, end + 1))
    return hidden


def _load_comments(zf: zipfile.ZipFile, sheet_part: str) -> dict[str, str]:
    rels = parse_rels(zf, rels_path_for(sheet_part))
    part = next((rel.target for rel in rels.values() if rel.type.endswith("/comments")), None)
    if part is None:
        return {}
    member = zip_member(zf, part)
    if member is None:
        return {}
    root = parse_xml(read_part(zf, member))
    out: dict[str, str] = {}
    for node in iter_local(root, "comment"):
        ref = node.get("ref")
        if not ref:
            continue
        out[ref] = "".join(t.text or "" for t in iter_local(node, "t"))
    return out


def _pending_from_cell(
    cell_el: etree._Element,
    *,
    sst: list[str],
    formats: list[str | None],
    comments: dict[str, str],
    hidden_cols: set[int],
    row_hidden: bool,
    sheet_hidden: bool,
) -> _PendingCell | None:
    addr = cell_el.get("r")
    if not addr:
        return None
    try:
        col, row = parse_addr(addr)
    except ValueError:
        return None
    f_el = find_child(cell_el, "f")
    formula: str | None = None
    shared_si: int | None = None
    if f_el is not None:
        f_text = "".join(f_el.itertext())
        formula = f_text if f_text else None
        if f_el.get("t") == "shared":
            si_raw = f_el.get("si")
            if si_raw is not None:
                shared_si = int(si_raw)
    cached = _cached_value(cell_el, sst)
    comment = comments.get(addr)
    if formula is None and cached is None and comment is None and shared_si is None:
        return None
    style = cell_el.get("s")
    number_format = None
    if style is not None:
        idx = int(style)
        if 0 <= idx < len(formats):
            number_format = formats[idx]
    hidden = sheet_hidden or row_hidden or col in hidden_cols
    return _PendingCell(
        addr=addr,
        col=col,
        row=row,
        formula=formula,
        shared_si=shared_si,
        cached=cached,
        hidden=hidden,
        number_format=number_format,
        comment=comment,
    )


def _cached_value(cell_el: etree._Element, sst: list[str]) -> str | None:
    cell_type = cell_el.get("t")
    if cell_type == "inlineStr":
        is_el = find_child(cell_el, "is")
        if is_el is None:
            return None
        text = "".join(node.text or "" for node in iter_local(is_el, "t"))
        return text or None
    v_el = find_child(cell_el, "v")
    if v_el is None or v_el.text is None:
        return None
    raw = v_el.text
    if cell_type == "s":
        try:
            idx = int(raw)
        except ValueError:
            return raw
        if 0 <= idx < len(sst):
            return sst[idx]
        return raw
    return raw
