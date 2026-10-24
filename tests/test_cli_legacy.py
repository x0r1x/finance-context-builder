"""`finance-context build` on `.xls`, encrypted and broken books: a code, not a traceback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.helpers.ole2 import ENCRYPTED_STREAMS, XLS_STREAMS, write_ole2
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx
from typer.testing import CliRunner

from finance_context.cli import app


def _prepared(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "Revenue"],
    )


def _fake_soffice(tmp_path: Path, prepared: Path) -> Path:
    script = tmp_path / "soffice"
    script.write_text(
        "#!/bin/sh\n"
        'outdir=""\nlast=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--outdir" ]; then outdir="$2"; shift 2; continue; fi\n'
        '  last="$1"; shift\n'
        "done\n"
        f'cp \'{prepared}\' "$outdir/$(basename "$last" .xls).xlsx"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOFFICE_BIN", str(tmp_path / "no-soffice"))


def _build(tmp_path: Path, source: Path):
    dest = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        ["build", str(source), "-o", str(dest), "--data-dir", str(tmp_path / "data")],
    )
    meta_path = dest / "meta" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    return result, dest, meta


def test_xls_without_libreoffice_fails_with_a_code(tmp_path: Path) -> None:
    source = write_ole2(tmp_path / "model.xls", XLS_STREAMS)
    result, dest, meta = _build(tmp_path, source)
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "unsupported_media_type" in result.output
    assert "LibreOffice" in result.output
    assert meta["status"] == "failed"
    assert meta["error"] == "unsupported_media_type"
    assert (dest / "source.xls").is_file()
    assert not (dest / "source" / "source.xlsx").exists()


def _labeled(path: Path, label: str) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value=label, type="s"),
                    CellSpec(addr="B2", value="100"),
                    CellSpec(addr="C2", value="110"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", label],
    )


def test_stale_xlsx_is_not_reused_for_a_new_xls(tmp_path: Path, monkeypatch) -> None:
    first = _labeled(tmp_path / "first.xlsx", "Revenue")
    result, dest, _meta = _build(tmp_path, first)
    assert result.exit_code == 0, result.output
    assert "Revenue" in (dest / "context" / "context.json").read_text(encoding="utf-8")
    tariff = _labeled(tmp_path / "tariff.xlsx", "Tariff")
    monkeypatch.setenv("SOFFICE_BIN", str(_fake_soffice(tmp_path, tariff)))
    source = write_ole2(tmp_path / "model.xls", XLS_STREAMS)
    result, dest, meta = _build(tmp_path, source)
    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[-1] == "succeeded"
    assert meta["status"] == "succeeded"
    labels = (dest / "context" / "context.json").read_text(encoding="utf-8")
    assert "Tariff" in labels
    assert "Revenue" not in labels


def test_missing_owner_digest_does_not_keep_a_stale_xlsx(
    tmp_path: Path, monkeypatch
) -> None:
    first = _labeled(tmp_path / "first.xlsx", "Revenue")
    result, dest, _meta = _build(tmp_path, first)
    assert result.exit_code == 0, result.output
    owner_path = dest / "owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    del owner["content_sha256"]
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    tariff = _labeled(tmp_path / "tariff.xlsx", "Tariff")
    monkeypatch.setenv("SOFFICE_BIN", str(_fake_soffice(tmp_path, tariff)))
    source = write_ole2(tmp_path / "next.xls", XLS_STREAMS)
    result, dest, meta = _build(tmp_path, source)
    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[-1] == "succeeded"
    assert meta["status"] == "succeeded"
    labels = (dest / "context" / "context.json").read_text(encoding="utf-8")
    assert "Tariff" in labels
    assert "Revenue" not in labels


def test_xls_is_converted_by_soffice(tmp_path: Path, monkeypatch) -> None:
    soffice = _fake_soffice(tmp_path, _prepared(tmp_path / "prepared.xlsx"))
    monkeypatch.setenv("SOFFICE_BIN", str(soffice))
    source = write_ole2(tmp_path / "model.xls", XLS_STREAMS)
    result, dest, meta = _build(tmp_path, source)
    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines()[-1] == "succeeded"
    assert meta["status"] == "succeeded"
    assert (dest / "source" / "source.xlsx").is_file()
    assert (dest / "context" / "context.json").is_file()
    again, dest, _meta = _build(tmp_path, source)
    assert again.exit_code == 0, again.output
    assert again.output.strip().splitlines()[-1] == "reused"
    assert (dest / "source" / "source.xlsx").is_file()
    assert (dest / "source.xls").is_file()


def test_encrypted_book_fails_with_a_code(tmp_path: Path) -> None:
    source = write_ole2(tmp_path / "locked.xlsx", ENCRYPTED_STREAMS)
    result, _dest, meta = _build(tmp_path, source)
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert meta["status"] == "failed"
    assert meta["error"] == "encrypted_workbook"


def test_broken_xlsx_fails_with_a_code(tmp_path: Path) -> None:
    source = tmp_path / "broken.xlsx"
    source.write_bytes(b"this is not a workbook")
    result, _dest, meta = _build(tmp_path, source)
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "zip_rejected" in result.output
    assert meta["status"] == "failed"
    assert meta["error"] == "zip_rejected"
