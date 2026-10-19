from __future__ import annotations

import zipfile
from pathlib import Path

from finance_context.errors import ContextError
from finance_context.excel.ole2 import is_ole2_file, ole2_kind

MAX_PART_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 50.0
# Tiny members routinely compress >50x; zip bombs are large.
RATIO_MIN_UNCOMPRESSED = 4 * 1024


def check_zip_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("\\"):
        raise ContextError("zip_rejected", "absolute path")
    if ".." in normalized.split("/"):
        raise ContextError("zip_rejected", "zip-slip")


def check_zip_info(info: zipfile.ZipInfo, running_total: int) -> int:
    check_zip_name(info.filename)
    if info.file_size > MAX_PART_BYTES:
        raise ContextError("zip_rejected", "part too large")
    total = running_total + info.file_size
    if total > MAX_TOTAL_BYTES:
        raise ContextError("zip_rejected", "archive too large")
    if info.file_size >= RATIO_MIN_UNCOMPRESSED:
        compressed = info.compress_size
        if compressed <= 0 or info.file_size / compressed >= MAX_COMPRESSION_RATIO:
            raise ContextError("zip_rejected", "compression ratio")
    return total


def reject_ole2(path: Path) -> None:
    """An OLE2 file is either an encrypted OOXML book or a legacy `.xls`, never a zip."""
    if not is_ole2_file(path):
        return
    kind = ole2_kind(path)
    if kind == "encrypted":
        raise ContextError("encrypted_workbook")
    if kind == "xls":
        raise ContextError("unsupported_media_type", "legacy .xls has to be converted to .xlsx")
    raise ContextError("unsupported_media_type", "OLE2 file is not an Excel workbook")


def open_xlsx_zip(path: Path) -> zipfile.ZipFile:
    if not path.exists() or path.stat().st_size == 0:
        raise ContextError("empty_file")
    reject_ole2(path)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ContextError("zip_rejected", "not a zip") from exc
    names = {name.replace("\\", "/").rsplit("/", 1)[-1] for name in zf.namelist()}
    if "EncryptedPackage" in names or "EncryptionInfo" in names:
        zf.close()
        raise ContextError("encrypted_workbook")
    try:
        total = 0
        for info in zf.infolist():
            total = check_zip_info(info, total)
    except ContextError:
        zf.close()
        raise
    return zf


def read_part(zf: zipfile.ZipFile, name: str) -> bytes:
    with zf.open(name) as handle:
        data = handle.read(MAX_PART_BYTES + 1)
    if len(data) > MAX_PART_BYTES:
        raise ContextError("zip_rejected", "part too large")
    return data
