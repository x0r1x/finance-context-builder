from __future__ import annotations

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
