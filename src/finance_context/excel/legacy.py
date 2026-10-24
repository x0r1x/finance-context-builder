"""Legacy `.xls` books: the port to an external converter and the shared-book source swap.

The upload is kept as `source.xls`. Parse converts it once into `source/source.xlsx`
and every later stage reads that file.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol

from finance_context.errors import ContextError
from finance_context.excel.ole2 import is_ole2, is_ole2_file, ole2_kind
from finance_context.excel.xls_cache import overlay_xls_cache
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.store.fs import atomic_replace

SOURCE = "source/source.xlsx"
LEGACY_SOURCE = "source.xls"
NO_CONVERTER = (
    "legacy .xls needs LibreOffice (soffice) to convert it to .xlsx; "
    "install it or set SOFFICE_BIN, or save the book as .xlsx"
)


class XlsConverter(Protocol):
    def available(self) -> bool:
        """True when the converter can run here."""
        ...

    def convert(self, source: Path, out_dir: Path) -> Path:
        """Write an .xlsx copy of `source` under `out_dir` and return its path."""
        ...


def source_name(head: bytes) -> str:
    """Shared-book file name for uploaded bytes. The content decides, not the suffix."""
    return LEGACY_SOURCE if is_ole2(head) else SOURCE


def has_source(book: Path) -> bool:
    return (book / SOURCE).is_file() or (book / LEGACY_SOURCE).is_file()


def check_legacy_workbook(path: Path, converter: XlsConverter) -> None:
    """An OLE2 upload is accepted only as a legacy `.xls` this host can convert."""
    kind = ole2_kind(path)
    if kind == "encrypted":
        raise ContextError("encrypted_workbook")
    if kind != "xls":
        raise ContextError("unsupported_media_type", "OLE2 file is not an Excel workbook")
    if not converter.available():
        raise ContextError("unsupported_media_type", NO_CONVERTER)


def ensure_xlsx(book: Path, converter: XlsConverter) -> Path:
    target = book / SOURCE
    legacy = book / LEGACY_SOURCE
    if target.is_file() and not is_ole2_file(target):
        # A converted book keeps both names, with source/source.xlsx written last.
        # A newer source.xls beside an older zip is a different workbook
        # (`build -o` used to leave the previous xlsx in place).
        if not legacy.is_file() or legacy.stat().st_mtime_ns <= target.stat().st_mtime_ns:
            return target
    elif target.is_file():
        # Older builds stored `.xls` bytes under the `.xlsx` name.
        atomic_replace(target, legacy)
    if not legacy.is_file():
        raise ContextError("empty_file", f"{SOURCE} missing")
    check_legacy_workbook(legacy, converter)
    with tempfile.TemporaryDirectory(dir=book, prefix=".convert-") as tmp:
        converted = converter.convert(legacy, Path(tmp))
        open_xlsx_zip(converted).close()
        # LibreOffice recalculates. Put Excel's cached values back before parse.
        overlay_xls_cache(legacy, converted)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace(converted, target)
    return target
