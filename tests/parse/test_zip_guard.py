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


def test_sheet_xml_may_compress_past_the_ratio() -> None:
    """Thousands of identical styled cells compress well past 50x in a real model."""
    from finance_context.excel.zip_guard import check_zip_info

    for name in ("xl/worksheets/sheet1.xml", "xl/sharedStrings.xml", "XL/Worksheets/Sheet2.XML"):
        info = zipfile.ZipInfo(name)
        info.file_size = 300 * 1024 * 1024
        info.compress_size = 1024 * 1024
        assert check_zip_info(info, running_total=0) == info.file_size
    for name in ("xl/vbaProject.bin", "xl/media/image1.png", "docProps/app.xml"):
        info = zipfile.ZipInfo(name)
        info.file_size = 300 * 1024 * 1024
        info.compress_size = 1024 * 1024
        with pytest.raises(ContextError) as ei:
            check_zip_info(info, running_total=0)
        assert "compression ratio" in str(ei.value)


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
