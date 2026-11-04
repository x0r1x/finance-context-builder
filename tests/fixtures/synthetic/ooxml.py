"""Deterministic OOXML writer.

Zip timestamps, entry order, and compression level are fixed so the same
inputs produce the same bytes. The books are generated at test time; nothing
here is a checked-in workbook.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO
from xml.sax.saxutils import escape

from finance_context.excel.a1 import index_to_col

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_OD_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

# ZIP epoch. A current timestamp would change the bytes on every run.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_COMPRESS_LEVEL = 6

_LETTERS = [index_to_col(col) for col in range(0, 302)]


def col_letter(col: int) -> str:
    """1-based Excel column letter. Column 1 is ``A``."""
    return _LETTERS[col]


class StringTable:
    """Shared strings in first-seen order.

    ``add`` reuses an earlier index. ``add_unique`` always appends, which is
    how the text books build a shared-string table with no duplicates to find.
    """

    def __init__(self) -> None:
        self.values: list[str] = []
        self._index: dict[str, int] = {}

    def add(self, text: str) -> int:
        found = self._index.get(text)
        if found is not None:
            return found
        found = len(self.values)
        self.values.append(text)
        self._index[text] = found
        return found

    def add_unique(self, text: str) -> int:
        found = len(self.values)
        self.values.append(text)
        return found

    def __len__(self) -> int:
        return len(self.values)


class Row:
    """One worksheet row, flushed when the context exits."""

    def __init__(self, book: Book, number: int, *, outline: int) -> None:
        self.book = book
        self.row_number = number
        self.outline = outline
        self._cells: list[str] = []

    def string(self, col: int, text: str, *, style: int | None = None, unique: bool = False) -> int:
        index = self.book.strings.add_unique(text) if unique else self.book.strings.add(text)
        self._shared(col, index, style=style)
        return index

    def shared_index(self, col: int, index: int, *, style: int | None = None) -> None:
        self._shared(col, index, style=style)

    def number(self, col: int, value: int) -> None:
        self._cells.append(f'<c r="{col_letter(col)}{self.row_number}"><v>{value}</v></c>')

    def formula(self, col: int, formula: str, cached: int | None = None) -> None:
        ref = f"{col_letter(col)}{self.row_number}"
        value = f"<v>{cached}</v>" if cached is not None else ""
        self._cells.append(f'<c r="{ref}"><f>{escape(formula)}</f>{value}</c>')

    def _shared(self, col: int, index: int, *, style: int | None) -> None:
        style_attr = f' s="{style}"' if style else ""
        ref = f"{col_letter(col)}{self.row_number}"
        self._cells.append(f'<c r="{ref}" t="s"{style_attr}><v>{index}</v></c>')

    def close(self) -> None:
        outline = f' outlineLevel="{self.outline}"' if self.outline else ""
        body = "".join(self._cells)
        xml = f'<row r="{self.row_number}"{outline}>{body}</row>'
        self.book._sheet.write(xml.encode())
        self._cells.clear()


class Book:
    """A one-sheet workbook. Use it as a context manager."""

    def __init__(
        self,
        path: Path,
        sheet: str,
        *,
        styles_xml: str | None = None,
        summary_below: bool | None = None,
    ) -> None:
        self.path = path
        self.sheet = sheet
        self.styles_xml = styles_xml
        self.summary_below = summary_below
        self.strings = StringTable()
        self._zip: zipfile.ZipFile | None = None
        self._sheet: IO[bytes] | None = None

    def __enter__(self) -> Book:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._zip = zipfile.ZipFile(
            self.path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=_COMPRESS_LEVEL,
        )
        self._writestr("[Content_Types].xml", _content_types(styles=self.styles_xml is not None))
        self._writestr("_rels/.rels", _root_rels())
        self._writestr("xl/workbook.xml", _workbook_xml(self.sheet))
        self._writestr(
            "xl/_rels/workbook.xml.rels",
            _workbook_rels(styles=self.styles_xml is not None),
        )
        sheet_info = _zip_info("xl/worksheets/sheet1.xml")
        self._sheet = self._zip.open(sheet_info, "w")  # type: ignore[assignment]
        self._sheet.write(
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        )
        if self.summary_below is not None:
            flag = "1" if self.summary_below else "0"
            self._sheet.write(
                f'<sheetPr><outlinePr summaryBelow="{flag}"/></sheetPr>'.encode()
            )
        self._sheet.write(b"<sheetData>")
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def row(self, number: int, *, outline: int = 0) -> Iterator[Row]:
        built = Row(self, number, outline=outline)
        try:
            yield built
        finally:
            built.close()

    def close(self) -> None:
        if self._sheet is not None:
            self._sheet.write(b"</sheetData></worksheet>")
            self._sheet.close()
            self._sheet = None
        if self._zip is None:
            return
        self._write_sst()
        if self.styles_xml is not None:
            self._writestr("xl/styles.xml", self.styles_xml)
        self._zip.close()
        self._zip = None

    def _write_sst(self) -> None:
        assert self._zip is not None
        info = _zip_info("xl/sharedStrings.xml")
        count = len(self.strings)
        with self._zip.open(info, "w") as handle:
            handle.write(
                (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<sst xmlns="{_NS_MAIN}" count="{count}" uniqueCount="{count}">'
                ).encode()
            )
            for text in self.strings.values:
                handle.write(f"<si><t>{escape(text)}</t></si>".encode())
            handle.write(b"</sst>")

    def _writestr(self, name: str, content: str) -> None:
        assert self._zip is not None
        self._zip.writestr(_zip_info(name), content.encode())


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    return info


def _content_types(*, styles: bool) -> str:
    overrides = [
        (
            "/xl/workbook.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        ),
        (
            "/xl/worksheets/sheet1.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
        ),
        (
            "/xl/sharedStrings.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
        ),
    ]
    if styles:
        overrides.append(
            (
                "/xl/styles.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
            )
        )
    body = "".join(
        f'<Override PartName="{part}" ContentType="{content}"/>' for part, content in overrides
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_NS_CT}">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{body}</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{_NS_OD_REL}/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheet: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_OD_REL}">'
        f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )


def _workbook_rels(*, styles: bool) -> str:
    rels = [
        ("rId1", f"{_NS_OD_REL}/worksheet", "worksheets/sheet1.xml"),
        ("rId2", f"{_NS_OD_REL}/sharedStrings", "sharedStrings.xml"),
    ]
    if styles:
        rels.append(("rId3", f"{_NS_OD_REL}/styles", "styles.xml"))
    body = "".join(
        f'<Relationship Id="{rid}" Type="{rel_type}" Target="{target}"/>'
        for rid, rel_type, target in rels
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_NS_PKG_REL}">{body}</Relationships>'
    )
