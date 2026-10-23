from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from tests.helpers.xlsx import write_zip

from finance_context.errors import ContextError
from finance_context.excel import parse_workbook


def test_empty_file_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "empty.xlsx"
    source.write_bytes(b"")
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "empty_file"


def test_encrypted_workbook_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "enc.xlsx"
    write_zip(
        source,
        {
            "EncryptionInfo": b"ole-header",
            "EncryptedPackage": b"ciphertext",
        },
    )
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "encrypted_workbook"


def test_zip_slip_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "slip.xlsx"
    write_zip(
        source,
        {
            "[Content_Types].xml": "<Types/>",
            "../../tmp/evil.txt": "pwn",
        },
    )
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "zip_rejected"


def test_zip_bomb_ratio_rejected(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "bomb.xlsx"
    zeros = b"\x00" * (100 * 1024)
    write_zip(
        source,
        {
            "[Content_Types].xml": "<Types/>",
            "xl/media/image1.bin": zeros.decode("latin1"),
        },
    )
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "zip_rejected"
    assert "compression ratio" in str(ei.value)


def _info(name: str, ratio: float) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_size = 1024 * 1024
    info.file_size = int(info.compress_size * ratio)
    return info


@pytest.mark.parametrize(
    ("name", "allowed", "refused"),
    [
        # Streamed sheets: only the size caps apply.
        ("xl/worksheets/sheet1.xml", 400.0, None),
        ("XL/Worksheets/Sheet2.XML", 400.0, None),
        # Workbook XML parsed whole: 200x.
        ("xl/sharedStrings.xml", 199.0, 200.0),
        ("xl/styles.xml", 199.0, 200.0),
        ("xl/comments1.xml", 199.0, 200.0),
        # Anything else: 50x, as before.
        ("xl/vbaProject.bin", 49.0, 50.0),
        ("xl/media/image1.png", 49.0, 50.0),
        ("docProps/app.xml", 49.0, 50.0),
    ],
)
def test_compression_ratio_limit_depends_on_how_the_part_is_read(
    name: str, allowed: float, refused: float | None
) -> None:
    from finance_context.excel.zip_guard import check_zip_info

    info = _info(name, allowed)
    assert check_zip_info(info, running_total=0) == info.file_size
    if refused is not None:
        with pytest.raises(ContextError) as ei:
            check_zip_info(_info(name, refused), running_total=0)
        assert "compression ratio" in str(ei.value)


def test_a_sheet_part_past_50x_parses(tmp_path: Path, dest: Path) -> None:
    """A sheet whose XML deflates past 50x is parsed, not refused."""
    from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

    built = build_xlsx(
        tmp_path / "built.xlsx",
        sheets=[SheetSpec(name="Sheet1", cells=[CellSpec(addr="A1", value="1")])],
    )
    source = tmp_path / "padded.xlsx"
    with zipfile.ZipFile(built) as src, zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.startswith("xl/worksheets/"):
                # Whitespace between elements is what long runs of identical
                # styled cells look like to deflate: almost free to store.
                data = data.replace(b"</sheetData>", b" " * (2 * 1024 * 1024) + b"</sheetData>")
            out.writestr(info.filename, data)
    with zipfile.ZipFile(source) as zf:
        sheet = next(i for i in zf.infolist() if i.filename.startswith("xl/worksheets/"))
        assert sheet.file_size / sheet.compress_size >= 50
    parse_workbook(source, dest)
    assert (dest / "raw" / "cells.parquet").is_file()


def test_sheet_xml_still_has_the_size_caps() -> None:
    from finance_context.excel.zip_guard import check_zip_info

    info = zipfile.ZipInfo("xl/worksheets/sheet1.xml")
    info.file_size = 512 * 1024 * 1024 + 1
    info.compress_size = 1024
    with pytest.raises(ContextError) as ei:
        check_zip_info(info, running_total=0)
    assert "part too large" in str(ei.value)
    info.file_size = 400 * 1024 * 1024
    with pytest.raises(ContextError) as ei:
        check_zip_info(info, running_total=1800 * 1024 * 1024)
    assert "archive too large" in str(ei.value)


def test_member_size_cap() -> None:
    from finance_context.excel.zip_guard import check_zip_info

    info = zipfile.ZipInfo("xl/workbook.xml")
    info.file_size = 512 * 1024 * 1024 + 1
    info.compress_size = 512 * 1024 * 1024 + 1
    with pytest.raises(ContextError) as ei:
        check_zip_info(info, running_total=0)
    assert ei.value.code == "zip_rejected"
