from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest
from tests.helpers.ole2 import ENCRYPTED_STREAMS, SIGNATURE, XLS_STREAMS, write_ole2

from finance_context.errors import ContextError
from finance_context.excel import parse_workbook
from finance_context.excel.ole2 import is_ole2, ole2_kind


def test_signature_marks_an_ole2_container() -> None:
    assert is_ole2(SIGNATURE + b"rest")
    assert not is_ole2(b"PK\x03\x04")
    assert not is_ole2(b"")


def test_workbook_stream_is_a_legacy_xls(tmp_path: Path) -> None:
    assert ole2_kind(write_ole2(tmp_path / "book.xls", XLS_STREAMS)) == "xls"


def test_encryption_streams_are_an_encrypted_ooxml(tmp_path: Path) -> None:
    assert ole2_kind(write_ole2(tmp_path / "book.xlsx", ENCRYPTED_STREAMS)) == "encrypted"


def test_entries_past_the_first_directory_sector_are_read(tmp_path: Path) -> None:
    streams = ["a", "b", "c", "d", "e", "f", "Workbook"]
    assert ole2_kind(write_ole2(tmp_path / "book.xls", streams)) == "xls"


def test_other_ole2_document_is_not_a_workbook(tmp_path: Path) -> None:
    assert ole2_kind(write_ole2(tmp_path / "memo.doc", ["WordDocument"])) == "other"


def test_cyclic_difat_with_a_huge_count_is_not_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "loop.xls"
    path.write_bytes(_cyclic_difat())
    started = time.monotonic()
    assert ole2_kind(path) == "other"
    assert time.monotonic() - started < 1


def _cyclic_difat() -> bytes:
    """One sector that points at itself, with FAT and DIFAT counts at 0x7FFFFFFF."""
    header = bytearray(512)
    header[0:8] = SIGNATURE
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 44, 0x7FFFFFFF)
    struct.pack_into("<II", header, 68, 0, 0x7FFFFFFF)
    struct.pack_into("<109I", header, 76, *([0xFFFFFFFF] * 109))
    # In-range FAT ids so an uncapped walk would also grow `found` without bound.
    body = struct.pack("<128I", *([0] * 127 + [0]))
    return bytes(header) + body


def test_truncated_ole2_is_not_a_workbook(tmp_path: Path) -> None:
    path = tmp_path / "cut.xls"
    path.write_bytes(SIGNATURE + b"\x00" * 100)
    assert ole2_kind(path) == "other"


def test_parse_rejects_an_encrypted_ooxml_container(tmp_path: Path, dest: Path) -> None:
    source = write_ole2(tmp_path / "enc.xlsx", ENCRYPTED_STREAMS)
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "encrypted_workbook"


def test_parse_names_a_legacy_xls_instead_of_a_bad_zip(tmp_path: Path, dest: Path) -> None:
    source = write_ole2(tmp_path / "book.xlsx", XLS_STREAMS)
    with pytest.raises(ContextError) as ei:
        parse_workbook(source, dest)
    assert ei.value.code == "unsupported_media_type"
    assert ".xls" in ei.value.detail
