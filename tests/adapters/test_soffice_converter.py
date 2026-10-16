"""The LibreOffice adapter against fake `soffice` scripts: the real binary is never run."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from tests.helpers.ole2 import XLS_STREAMS, write_ole2
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.adapters.soffice import SofficeConverter
from finance_context.errors import ContextError
from finance_context.settings import Settings


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _converting_soffice(tmp_path: Path) -> Path:
    prepared = build_xlsx(
        tmp_path / "prepared.xlsx",
        sheets=[SheetSpec(name="P&L", cells=[CellSpec(addr="A1", value="1")])],
    )
    log = tmp_path / "soffice.log"
    return _script(
        tmp_path / "soffice",
        f"""printf '%s\\n' "$@" > '{log}'
env > '{log}.env'
outdir=""
last=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--outdir" ]; then outdir="$2"; shift 2; continue; fi
  last="$1"; shift
done
cp '{prepared}' "$outdir/$(basename "$last" .xls).xlsx"
""",
    )


def _xls(tmp_path: Path) -> Path:
    return write_ole2(tmp_path / "model.xls", XLS_STREAMS)


def test_missing_binary_is_unavailable(tmp_path: Path) -> None:
    converter = SofficeConverter(str(tmp_path / "no-soffice"))
    assert not converter.available()
    with pytest.raises(ContextError) as ei:
        converter.convert(_xls(tmp_path), tmp_path)
    assert ei.value.code == "unsupported_media_type"
    assert "LibreOffice" in ei.value.detail


def test_settings_pick_the_binary_and_the_timeout(tmp_path: Path) -> None:
    binary = _converting_soffice(tmp_path)
    settings = Settings(
        _env_file=None, soffice_bin=str(binary), xls_convert_timeout_sec=7
    )
    converter = SofficeConverter.from_settings(settings)
    assert converter.available()
    assert converter.timeout_sec == 7


def test_headless_conversion_writes_an_xlsx(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    binary = _converting_soffice(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    produced = SofficeConverter(str(binary)).convert(_xls(tmp_path), work)
    assert produced.is_file()
    assert produced.read_bytes() == (tmp_path / "prepared.xlsx").read_bytes()
    assert work in produced.parents
    args = (tmp_path / "soffice.log").read_text(encoding="utf-8").splitlines()
    assert "--headless" in args
    assert args[args.index("--convert-to") + 1] == "xlsx"
    profile = next(arg for arg in args if arg.startswith("-env:UserInstallation="))
    assert profile.startswith(f"-env:UserInstallation={work.as_uri()}")
    env = (tmp_path / "soffice.log.env").read_text(encoding="utf-8")
    assert "proxy.invalid" not in env
    assert f"HOME={work}" in env


def test_nonzero_exit_is_a_conversion_failure(tmp_path: Path) -> None:
    binary = _script(tmp_path / "soffice", "echo 'source file could not be loaded' >&2\nexit 3\n")
    with pytest.raises(ContextError) as ei:
        SofficeConverter(str(binary)).convert(_xls(tmp_path), tmp_path)
    assert ei.value.code == "conversion_failed"
    assert "3" in ei.value.detail
    assert "could not be loaded" in ei.value.detail


def test_no_output_file_is_a_conversion_failure(tmp_path: Path) -> None:
    binary = _script(tmp_path / "soffice", "exit 0\n")
    with pytest.raises(ContextError) as ei:
        SofficeConverter(str(binary)).convert(_xls(tmp_path), tmp_path)
    assert ei.value.code == "conversion_failed"


def test_timeout_kills_soffice_and_its_children(tmp_path: Path) -> None:
    pids = tmp_path / "pids"
    binary = _script(
        tmp_path / "soffice",
        f"sleep 30 &\necho $$ $! > '{pids}'\nwait\n",
    )
    started = time.monotonic()
    with pytest.raises(ContextError) as ei:
        SofficeConverter(str(binary), timeout_sec=0.5).convert(_xls(tmp_path), tmp_path)
    assert ei.value.code == "conversion_failed"
    assert "0.5" in ei.value.detail
    assert time.monotonic() - started < 10
    for pid in (int(item) for item in pids.read_text(encoding="utf-8").split()):
        assert not _alive(pid)


def _alive(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.05)
    return True
