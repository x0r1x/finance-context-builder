"""`.xls` uploads: converted by LibreOffice in the job process, or a clean refusal."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tests.api.conftest import _app, _book_dir, _wait_for_terminal, _xlsx
from tests.helpers.ole2 import ENCRYPTED_STREAMS, XLS_STREAMS, write_ole2

_XLS_TYPE = "application/vnd.ms-excel"


def _soffice(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "soffice"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


def _converting(tmp_path: Path) -> Path:
    prepared = _xlsx(tmp_path / "prepared.xlsx")
    return _soffice(
        tmp_path,
        'outdir=""\nlast=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--outdir" ]; then outdir="$2"; shift 2; continue; fi\n'
        '  last="$1"; shift\n'
        "done\n"
        f'cp \'{prepared}\' "$outdir/$(basename "$last" .xls).xlsx"\n',
    )


def _post(client: TestClient, path: Path, name: str):
    return client.post(
        "/v1/context-jobs", files={"file": (name, path.read_bytes(), _XLS_TYPE)}
    )


def test_xls_without_libreoffice_is_unsupported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOFFICE_BIN", str(tmp_path / "no-soffice"))
    source = write_ole2(tmp_path / "model.xls", XLS_STREAMS)
    with _app(tmp_path) as client:
        response = _post(client, source, "model.xls")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unsupported_media_type"
    assert "LibreOffice" in body["detail"]


def test_encrypted_ole2_book_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOFFICE_BIN", str(_converting(tmp_path)))
    source = write_ole2(tmp_path / "locked.xlsx", ENCRYPTED_STREAMS)
    with _app(tmp_path) as client:
        response = _post(client, source, "locked.xlsx")
    assert response.status_code == 422
    assert response.json()["error"] == "encrypted_workbook"


def test_ole2_that_is_not_a_workbook_is_unsupported(tmp_path: Path) -> None:
    source = write_ole2(tmp_path / "memo.xls", ["WordDocument"])
    with _app(tmp_path) as client:
        response = _post(client, source, "memo.xls")
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_media_type"


def test_xls_job_converts_and_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOFFICE_BIN", str(_converting(tmp_path)))
    source = write_ole2(tmp_path / "model.xls", XLS_STREAMS)
    with _app(tmp_path) as client:
        created = _post(client, source, "model.xls")
        assert created.status_code == 202, created.json()
        job_id = created.json()["job_id"]
        body = _wait_for_terminal(client, job_id)
    assert body["status"] == "succeeded", body
    book = _book_dir(tmp_path, job_id)
    assert (book / "source.xls").is_file()
    assert (book / "source.xlsx").is_file()


def test_failed_conversion_fails_the_job_with_its_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOFFICE_BIN", str(_soffice(tmp_path, "exit 7\n")))
    source = write_ole2(tmp_path / "model.xls", XLS_STREAMS)
    with _app(tmp_path) as client:
        created = _post(client, source, "model.xls")
        job_id = created.json()["job_id"]
        body = _wait_for_terminal(client, job_id)
    assert body["status"] == "failed"
    assert body["error"] == "conversion_failed"
    assert body["stage"] == "failed"
