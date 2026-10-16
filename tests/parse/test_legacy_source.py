from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.converter import FakeConverter
from tests.helpers.ole2 import ENCRYPTED_STREAMS, SIGNATURE, XLS_STREAMS, write_ole2
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.errors import ContextError
from finance_context.excel.legacy import ensure_xlsx, source_name


def _xlsx(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[SheetSpec(name="P&L", cells=[CellSpec(addr="A1", value="1")])],
    )


def test_source_name_follows_the_bytes_not_the_suffix() -> None:
    assert source_name(SIGNATURE + b"rest") == "source.xls"
    assert source_name(b"PK\x03\x04rest") == "source.xlsx"


def test_existing_xlsx_is_used_as_is(tmp_path: Path) -> None:
    _xlsx(tmp_path / "source.xlsx")
    converter = FakeConverter(available=False)
    assert ensure_xlsx(tmp_path, converter) == tmp_path / "source.xlsx"
    assert converter.calls == []


def test_legacy_xls_is_converted_once(tmp_path: Path) -> None:
    write_ole2(tmp_path / "source.xls", XLS_STREAMS)
    converter = FakeConverter(_xlsx(tmp_path / "prepared.xlsx"))
    target = ensure_xlsx(tmp_path, converter)
    assert target == tmp_path / "source.xlsx"
    assert target.read_bytes() == (tmp_path / "prepared.xlsx").read_bytes()
    assert converter.calls == [tmp_path / "source.xls"]
    ensure_xlsx(tmp_path, converter)
    assert len(converter.calls) == 1
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".convert")]


def test_ole2_bytes_stored_as_xlsx_are_converted(tmp_path: Path) -> None:
    write_ole2(tmp_path / "source.xlsx", XLS_STREAMS)
    converter = FakeConverter(_xlsx(tmp_path / "prepared.xlsx"))
    target = ensure_xlsx(tmp_path, converter)
    assert target.read_bytes() == (tmp_path / "prepared.xlsx").read_bytes()
    assert len(converter.calls) == 1


def test_encrypted_ooxml_is_not_sent_to_the_converter(tmp_path: Path) -> None:
    write_ole2(tmp_path / "source.xls", ENCRYPTED_STREAMS)
    converter = FakeConverter(_xlsx(tmp_path / "prepared.xlsx"))
    with pytest.raises(ContextError) as ei:
        ensure_xlsx(tmp_path, converter)
    assert ei.value.code == "encrypted_workbook"
    assert converter.calls == []


def test_missing_libreoffice_is_an_unsupported_media_type(tmp_path: Path) -> None:
    write_ole2(tmp_path / "source.xls", XLS_STREAMS)
    with pytest.raises(ContextError) as ei:
        ensure_xlsx(tmp_path, FakeConverter(available=False))
    assert ei.value.code == "unsupported_media_type"
    assert "LibreOffice" in ei.value.detail
    assert not (tmp_path / "source.xlsx").exists()


def test_converter_output_that_is_not_a_zip_is_rejected(tmp_path: Path) -> None:
    write_ole2(tmp_path / "source.xls", XLS_STREAMS)
    garbage = tmp_path / "garbage.xlsx"
    garbage.write_bytes(b"not a zip")
    with pytest.raises(ContextError) as ei:
        ensure_xlsx(tmp_path, FakeConverter(garbage))
    assert ei.value.code == "zip_rejected"
    assert not (tmp_path / "source.xlsx").exists()


def test_no_source_at_all_is_an_empty_file(tmp_path: Path) -> None:
    with pytest.raises(ContextError) as ei:
        ensure_xlsx(tmp_path, FakeConverter())
    assert ei.value.code == "empty_file"
