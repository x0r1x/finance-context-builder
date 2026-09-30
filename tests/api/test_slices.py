from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from tests.helpers.slice_book import REVENUE, write_slice_job

from finance_context.api.app import create_app
from finance_context.api.etag import etag_matches, strong_etag
from finance_context.settings import Settings

_JOB = "a" * 64
_OTHER = "b" * 64


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
    )
    return TestClient(create_app(settings))


def test_etag_match_is_strong(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    path.write_text("{}", encoding="utf-8")
    tag = strong_etag("catalog-1", "abc", path)
    assert etag_matches("*", tag)
    assert etag_matches(tag, tag)
    assert etag_matches(f" {tag} , \"other\"", tag)
    assert not etag_matches(f"W/{tag}", tag)
    assert not etag_matches(None, tag)


def test_list_finds_a_book_by_filename(tmp_path: Path) -> None:
    write_slice_job(tmp_path / "data", _JOB)
    other = write_slice_job(tmp_path / "data", _OTHER)
    meta = json.loads((other / "meta.json").read_text(encoding="utf-8"))
    meta["source_filename"] = "notes.xlsx"
    (other / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (other / "context.json").write_text("{not json", encoding="utf-8")
    with _client(tmp_path) as client:
        response = client.get("/v1/context-jobs", params={"q": "RVI"})
    assert response.status_code == 200
    body = response.json()
    assert [item["job_id"] for item in body] == [_JOB]
    assert body[0]["source_filename"] == "rvi-project-finance.xlsx"
    assert body[0]["schema_version"] == "1.13.0"
    assert "warnings" not in body[0]


def test_catalog_etag_head_and_rewrite(tmp_path: Path) -> None:
    dest = write_slice_job(tmp_path / "data", _JOB)
    with _client(tmp_path) as client:
        first = client.get(f"/v1/context-jobs/{_JOB}/catalog")
        assert first.status_code == 200
        etag = first.headers["etag"]
        assert etag.startswith('"catalog-1:')
        assert "values" not in first.text
        head = client.head(f"/v1/context-jobs/{_JOB}/catalog")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["etag"] == etag
        cached = client.get(
            f"/v1/context-jobs/{_JOB}/catalog",
            headers={"If-None-Match": etag},
        )
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers["etag"] == etag
        context = json.loads((dest / "context.json").read_text(encoding="utf-8"))
        context["warnings"] = ["rewritten catalog stamp for the etag test"]
        (dest / "context.json").write_text(json.dumps(context), encoding="utf-8")
        second = client.get(f"/v1/context-jobs/{_JOB}/catalog")
        assert second.status_code == 200
        assert second.headers["etag"] != etag


def test_observations_selector_limit_and_unknown_row(tmp_path: Path) -> None:
    write_slice_job(tmp_path / "data", _JOB)
    with _client(tmp_path) as client:
        missing = client.get(f"/v1/context-jobs/{_JOB}/observations")
        assert missing.status_code == 400
        assert missing.json()["error"] == "selector_required"
        assert "etag" not in missing.headers
        phase_only = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"phase": "operation"},
        )
        assert phase_only.status_code == 400
        unknown = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"row_key": "missing"},
        )
        assert unknown.status_code == 200
        assert unknown.json() == {
            "schema_version": "observation-1",
            "truncated": False,
            "observations": [],
        }
        limited = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params=[("row_key", REVENUE), ("row_key", "Operation|11|Operation!r8"), ("limit", "1")],
        )
        assert limited.status_code == 200
        assert limited.json()["truncated"] is True
        assert len(limited.json()["observations"]) == 1
        revenue = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"row_key": REVENUE},
        )
        observation = revenue.json()["observations"][0]
        assert observation["source"]["cell"] == "H10"
        assert observation["formula"]["class"] == "cross_period"
        assert observation["value"] == "1234.56"
        assert "scenario" not in observation
        invalid = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"row_key": REVENUE, "limit": 0},
        )
        assert invalid.status_code == 422
        deep = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"row_key": REVENUE, "precedent_depth": 4},
        )
        assert deep.status_code == 422


def test_summary_has_counters_without_links(tmp_path: Path) -> None:
    write_slice_job(tmp_path / "data", _JOB)
    with _client(tmp_path) as client:
        response = client.get(f"/v1/context-jobs/{_JOB}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "summary-1"
    assert body["missing_cached_values"] == 17
    assert body["unresolved"]["count"] == 2
    assert "links" not in body
    assert "1234.56" not in response.text
    assert response.headers["etag"].startswith('"summary-1:')


def test_missing_graph_and_unknown_job(tmp_path: Path) -> None:
    dest = write_slice_job(tmp_path / "data", _JOB)
    (dest / "graph.json").unlink()
    with _client(tmp_path) as client:
        not_ready = client.get(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"q": "revenue"},
        )
        assert not_ready.status_code == 409
        assert not_ready.json()["error"] == "report_not_ready"
        assert "etag" not in not_ready.headers
        summary = client.get(f"/v1/context-jobs/{_JOB}/summary")
        assert summary.status_code == 409
        assert "etag" not in summary.headers
        missing = client.get(f"/v1/context-jobs/{'c' * 64}/catalog")
        assert missing.status_code == 404
        assert client.get("/v1/context-jobs/not-a-hash/catalog").status_code == 404
