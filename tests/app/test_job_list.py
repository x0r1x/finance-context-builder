from __future__ import annotations

import json
from pathlib import Path

from finance_context.app.jobs import list_session_jobs


def _meta(path: Path, **fields: str) -> None:
    path.mkdir(parents=True)
    body = {
        "job_id": path.name,
        "status": "succeeded",
        "stage": "done",
        "source_filename": "other.xlsx",
        "content_sha256": "a" * 64,
        "schema_version": "1.13.0",
    }
    body.update(fields)
    (path / "meta.json").write_text(json.dumps(body), encoding="utf-8")


def test_list_finds_a_book_by_filename_without_reading_context(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    first = "b" * 64
    second = "a" * 64
    _meta(jobs / first, source_filename="notes.xlsx")
    _meta(jobs / second, source_filename="RVI-Project-Finance.xlsx", status="needs_input")
    (jobs / second / "context.json").write_text("{not json", encoding="utf-8")
    (jobs / ("c" * 64)).mkdir()
    (jobs / ("d" * 64)).mkdir()
    (jobs / ("d" * 64) / "meta.json").write_text("not-json", encoding="utf-8")

    listed = list_session_jobs(jobs, q="rvi")
    assert [item["job_id"] for item in listed] == [second]
    assert listed[0]["source_filename"] == "RVI-Project-Finance.xlsx"
    assert listed[0]["schema_version"] == "1.13.0"
    assert "warnings" not in listed[0]

    ordered = list_session_jobs(jobs)
    assert [item["job_id"] for item in ordered] == [second, first]
    assert list_session_jobs(jobs, status="needs_input")[0]["job_id"] == second
    assert list_session_jobs(jobs, status="failed") == []
