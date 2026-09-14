"""Synthetic OOXML workbooks for tests. Cached values only, no client data."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_OD_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def col_row(addr: str) -> tuple[int, int]:
    col_s = "".join(ch for ch in addr if ch.isalpha())
    row_s = "".join(ch for ch in addr if ch.isdigit())
    col = 0
    for ch in col_s.upper():
        col = col * 26 + (ord(ch) - 64)
    return col, int(row_s)


@dataclass
class CellSpec:
    addr: str
    value: str | None = None
    formula: str | None = None
    type: str | None = None  # s, n, b, e, str, inlineStr
    shared_si: int | None = None
    shared_ref: str | None = None
    style: int | None = None


@dataclass
class SheetSpec:
    name: str
    cells: list[CellSpec] = field(default_factory=list)
    hidden: bool = False
    hidden_cols: list[int] = field(default_factory=list)
    hidden_rows: list[int] = field(default_factory=list)
    comments: dict[str, str] = field(default_factory=dict)
    macrosheet: bool = False


def write_zip(path: Path, members: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def build_xlsx(
    path: Path,
    *,
    sheets: list[SheetSpec],
    shared_strings: list[str] | None = None,
    has_vba: bool = False,
    externals: list[str] | None = None,
    iterate: bool = False,
    defined_names: list[tuple[str, str]] | None = None,
    num_formats: dict[int, str] | None = None,
    cell_xfs: list[int] | None = None,
    date1904: bool = False,
) -> Path:
    """Write a minimal xlsx/xlsm. cell_xfs is a list of numFmtId per xf index."""
    shared_strings = list(shared_strings or [])
    externals = list(externals or [])
    defined_names = list(defined_names or [])
    members: dict[str, str | bytes] = {}
    overrides: list[str] = [
        _override("/xl/workbook.xml", _wb_content_type(has_vba)),
    ]

    sheet_rels: list[str] = []
    sheet_els: list[str] = []
    rid = 1
    for i, sheet in enumerate(sheets, start=1):
        rel_id = f"rId{rid}"
        rid += 1
        folder = "macrosheets" if sheet.macrosheet else "worksheets"
        part = f"xl/{folder}/sheet{i}.xml"
        rel_type = (
            f"{NS_OD_REL}/xlMacrosheet" if sheet.macrosheet else f"{NS_OD_REL}/worksheet"
        )
        ct = (
            "application/vnd.ms-excel.macrosheet+xml"
            if sheet.macrosheet
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
        )
        members[part] = _sheet_xml(sheet, shared_strings)
        overrides.append(_override(f"/{part}", ct))
        sheet_rels.append(_relationship(rel_id, rel_type, f"{folder}/sheet{i}.xml"))
        state = ' state="hidden"' if sheet.hidden else ""
        sheet_els.append(
            f'<sheet name="{escape(sheet.name)}" sheetId="{i}" r:id="{rel_id}"{state}/>'
        )

        if sheet.comments:
            cpart = f"xl/comments{i}.xml"
            members[cpart] = _comments_xml(sheet.comments)
            overrides.append(
                _override(
                    f"/{cpart}",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml",
                )
            )
            members[f"xl/{folder}/_rels/sheet{i}.xml.rels"] = _rels_xml(
                [
                    _relationship(
                        "rId1",
                        f"{NS_OD_REL}/comments",
                        f"../comments{i}.xml",
                    )
                ]
            )

    extra_wb_rels: list[str] = []
    extra_wb_els: list[str] = []
    if shared_strings:
        members["xl/sharedStrings.xml"] = _sst_xml(shared_strings)
        overrides.append(
            _override(
                "/xl/sharedStrings.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
            )
        )
        extra_wb_rels.append(
            _relationship(f"rId{rid}", f"{NS_OD_REL}/sharedStrings", "sharedStrings.xml")
        )
        rid += 1

    if num_formats is not None or cell_xfs is not None:
        members["xl/styles.xml"] = _styles_xml(num_formats or {}, cell_xfs or [0])
        overrides.append(
            _override(
                "/xl/styles.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
            )
        )
        extra_wb_rels.append(
            _relationship(f"rId{rid}", f"{NS_OD_REL}/styles", "styles.xml")
        )
        rid += 1

    if externals:
        ext_refs = []
        for n, target in enumerate(externals, start=1):
            erid = f"rId{rid}"
            rid += 1
            members[f"xl/externalLinks/externalLink{n}.xml"] = _external_link_xml()
            members[f"xl/externalLinks/_rels/externalLink{n}.xml.rels"] = _rels_xml(
                [
                    _relationship(
                        "rId1",
                        f"{NS_OD_REL}/externalLinkPath",
                        target,
                        target_mode="External",
                    )
                ]
            )
            overrides.append(
                _override(
                    f"/xl/externalLinks/externalLink{n}.xml",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml",
                )
            )
            extra_wb_rels.append(
                _relationship(
                    erid, f"{NS_OD_REL}/externalLink", f"externalLinks/externalLink{n}.xml"
                )
            )
            ext_refs.append(f'<externalReference r:id="{erid}"/>')
        extra_wb_els.append(
            "<externalReferences>" + "".join(ext_refs) + "</externalReferences>"
        )

    if has_vba:
        members["xl/vbaProject.bin"] = b"not-a-real-vba-project"
        overrides.append(
            _override("/xl/vbaProject.bin", "application/vnd.ms-office.vbaProject")
        )
        extra_wb_rels.append(
            _relationship(f"rId{rid}", f"{NS_OD_REL}/vbaProject", "vbaProject.bin")
        )
        rid += 1

    members["xl/_rels/workbook.xml.rels"] = _rels_xml(sheet_rels + extra_wb_rels)
    members["xl/workbook.xml"] = _workbook_xml(
        sheet_els,
        extra_wb_els,
        iterate=iterate,
        defined_names=defined_names,
        date1904=date1904,
    )
    members["[Content_Types].xml"] = _content_types(overrides)
    members["_rels/.rels"] = _rels_xml(
        [
            _relationship(
                "rId1",
                f"{NS_OD_REL}/officeDocument",
                "xl/workbook.xml",
            )
        ]
    )
    return write_zip(path, members)


def _wb_content_type(has_vba: bool) -> str:
    if has_vba:
        return "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def _override(part: str, content_type: str) -> str:
    return f'<Override PartName="{part}" ContentType="{content_type}"/>'


def _relationship(rid: str, rel_type: str, target: str, target_mode: str | None = None) -> str:
    mode = f' TargetMode="{target_mode}"' if target_mode else ""
    safe_target = escape(target, {chr(34): "&quot;"})
    return (
        f'<Relationship Id="{rid}" Type="{rel_type}" '
        f'Target="{safe_target}"{mode}/>'
    )


def _rels_xml(rels: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG_REL}">'
        + "".join(rels)
        + "</Relationships>"
    )


def _content_types(overrides: list[str]) -> str:
    rels_ct = "application/vnd.openxmlformats-package.relationships+xml"
    defaults = (
        f'<Default Extension="rels" ContentType="{rels_ct}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>'
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{NS_CT}">{defaults}{"".join(overrides)}</Types>'
    )


def _workbook_xml(
    sheet_els: list[str],
    extra_els: list[str],
    *,
    iterate: bool,
    defined_names: list[tuple[str, str]],
    date1904: bool = False,
) -> str:
    calc = '<calcPr iterate="1"/>' if iterate else ""
    names = ""
    if defined_names:
        body = "".join(
            f'<definedName name="{escape(n)}">{escape(f)}</definedName>'
            for n, f in defined_names
        )
        names = f"<definedNames>{body}</definedNames>"
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_OD_REL}">'
        f"{pr}"
        f'<sheets>{"".join(sheet_els)}</sheets>'
        f'{"".join(extra_els)}{names}{calc}'
        "</workbook>"
    )


def _sheet_xml(sheet: SheetSpec, shared_strings: list[str]) -> str:
    by_row: dict[int, list[CellSpec]] = {}
    for cell in sheet.cells:
        _, row = col_row(cell.addr)
        by_row.setdefault(row, []).append(cell)
    col_xml = ""
    if sheet.hidden_cols:
        bits = "".join(
            f'<col min="{c}" max="{c}" hidden="1"/>' for c in sheet.hidden_cols
        )
        col_xml = f"<cols>{bits}</cols>"
    rows_xml = []
    for row in sorted(by_row):
        hidden = ' hidden="1"' if row in sheet.hidden_rows else ""
        cells_xml = "".join(_cell_xml(c, shared_strings) for c in by_row[row])
        rows_xml.append(f'<row r="{row}"{hidden}>{cells_xml}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS_MAIN}">'
        f"{col_xml}<sheetData>{''.join(rows_xml)}</sheetData>"
        "</worksheet>"
    )


def _cell_xml(cell: CellSpec, shared_strings: list[str]) -> str:
    attrs = [f'r="{cell.addr}"']
    cell_type = cell.type
    v_xml = ""
    f_xml = ""
    if cell.formula is not None or cell.shared_si is not None:
        f_attrs = []
        if cell.shared_si is not None:
            f_attrs.append('t="shared"')
            f_attrs.append(f'si="{cell.shared_si}"')
            if cell.shared_ref:
                f_attrs.append(f'ref="{cell.shared_ref}"')
        attr = (" " + " ".join(f_attrs)) if f_attrs else ""
        body = escape(cell.formula) if cell.formula is not None else ""
        f_xml = f"<f{attr}>{body}</f>"
    if cell.type == "inlineStr" and cell.value is not None:
        attrs.append('t="inlineStr"')
        v_xml = f"<is><t>{escape(cell.value)}</t></is>"
    elif cell.value is not None:
        if cell_type is None:
            cell_type = "s" if not _is_number(cell.value) else None
        if cell_type == "s":
            if cell.value not in shared_strings:
                raise ValueError(f"shared string {cell.value!r} missing from sst")
            attrs.append('t="s"')
            v_xml = f"<v>{shared_strings.index(cell.value)}</v>"
        else:
            if cell_type:
                attrs.append(f't="{cell_type}"')
            v_xml = f"<v>{escape(cell.value)}</v>"
    if cell.style is not None:
        attrs.append(f's="{cell.style}"')
    return f"<c {' '.join(attrs)}>{f_xml}{v_xml}</c>"


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _sst_xml(strings: list[str]) -> str:
    items = "".join(f'<si><t>{escape(s)}</t></si>' for s in strings)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{NS_MAIN}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        f"{items}</sst>"
    )


def _comments_xml(comments: dict[str, str]) -> str:
    texts = "".join(
        f'<comment ref="{addr}" authorId="0"><text><t>{escape(text)}</t></text></comment>'
        for addr, text in comments.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<comments xmlns="{NS_MAIN}">'
        "<authors><author>tester</author></authors>"
        f"<commentList>{texts}</commentList>"
        "</comments>"
    )


def _styles_xml(num_formats: dict[int, str], cell_xfs: list[int]) -> str:
    nf = ""
    if num_formats:
        body = "".join(
            f'<numFmt numFmtId="{fid}" formatCode="{escape(code)}"/>'
            for fid, code in num_formats.items()
        )
        nf = f'<numFmts count="{len(num_formats)}">{body}</numFmts>'
    xfs = "".join(f'<xf numFmtId="{fid}"/>' for fid in cell_xfs)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{NS_MAIN}">'
        f"{nf}<cellXfs count=\"{len(cell_xfs)}\">{xfs}</cellXfs>"
        "</styleSheet>"
    )


def _external_link_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<externalLink xmlns="{NS_MAIN}" xmlns:r="{NS_OD_REL}">'
        '<externalBook r:id="rId1">'
        "<sheetNames><sheetName val=\"Sheet1\"/></sheetNames>"
        "</externalBook></externalLink>"
    )
