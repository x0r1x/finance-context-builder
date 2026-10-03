from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from tests.api.conftest import _app, _job_dir, _xlsx


def test_upload_and_download(client: TestClient, tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    created = client.post(
        "/v1/context-jobs",
        files={
            "file": (
                "model.xlsx",
                source.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    status = None
    for _ in range(200):
        status = client.get(f"/v1/context-jobs/{job_id}")
        if status.json().get("status") not in {"queued", "running"}:
            break
        time.sleep(0.1)
    assert status is not None
    body = status.json()
    assert body["status"] == "succeeded"
    json_doc = client.get(f"/v1/context-jobs/{job_id}/context.json")
    md_doc = client.get(f"/v1/context-jobs/{job_id}/context.md")
    assert json_doc.status_code == 200
    assert "schema_version" in json_doc.json()
    graph_doc = client.get(f"/v1/context-jobs/{job_id}/graph.json")
    assert graph_doc.status_code == 200
    assert "nodes" in graph_doc.json()
    assert body.get("context_json_url") == f"/v1/context-jobs/{job_id}/context.json"
    assert body.get("context_md_url") == f"/v1/context-jobs/{job_id}/context.md"
    assert body.get("graph_json_url") == f"/v1/context-jobs/{job_id}/graph.json"
    assert body.get("graph_md_url") == f"/v1/context-jobs/{job_id}/graph.md"
    assert "graph_edges_url" not in body
    assert "formulas_json_url" not in body
    graph_md = client.get(f"/v1/context-jobs/{job_id}/graph.md")
    assert graph_md.status_code == 200
    assert "Formula graph" in graph_md.text
    assert "links" in graph_doc.json()
    traced = client.get(f"/v1/context-jobs/{job_id}/graph/trace", params={"from": "P&L!C2"})
    assert traced.status_code == 200
    assert traced.json()["origin"] == "P&L!C2"
    assert "formula_ast" not in traced.text
    traced_md = client.get(
        f"/v1/context-jobs/{job_id}/graph/trace.md", params={"from": "P&L!C2"}
    )
    assert traced_md.status_code == 200
    assert "P&L!C2" in traced_md.text
    assert md_doc.status_code == 200
    assert "Financial context" in md_doc.text
    assert json_doc.json()["schema_version"] == "1.13.0"


def test_get_context_md_rewrites_a_finished_job(tmp_path: Path) -> None:
    job_id = "a" * 64
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    context = {
        "meta": {"job_id": job_id, "status": "succeeded", "stage": "done"},
        "workbook": {},
        "blocks": [
            {
                "block_id": "PF Model!r7",
                "sheet": "PF Model",
                "label_col": 1,
                "periods": [{"col": 27, "period_key": "2038", "text": "2038", "role": "forecast"}],
                "rows": [
                    {
                        "row_key": "PF Model|172|PF Model!r7",
                        "sheet": "PF Model",
                        "row": 172,
                        "kind": "fact",
                        "label": "CPI",
                        "values": ["1.3458683383241299"],
                        "value_statuses": ["cached"],
                    }
                ],
            }
        ],
    }
    (dest / "context.json").write_text(json.dumps(context), encoding="utf-8")
    (dest / "context.md").write_text("1.3458683383241299\n", encoding="utf-8")
    (dest / "graph.json").write_text("{}\n", encoding="utf-8")
    (dest / "graph.md").write_text("PF Model!AA172 | Z172*(1+AA165)\n", encoding="utf-8")
    with _app(tmp_path) as client:
        response = client.get(f"/v1/context-jobs/{job_id}/context.md")
    assert response.status_code == 200
    assert "1.3458683383241299 [PF Model!AA172]" in response.text
    assert "Z172*(1+AA165)" not in response.text
    stored = json.loads((dest / "context.json").read_text(encoding="utf-8"))
    assert stored["blocks"][0]["rows"][0]["values"] == ["1.3458683383241299"]
    assert (dest / "graph.md").read_text(encoding="utf-8") == "PF Model!AA172 | Z172*(1+AA165)\n"
