from __future__ import annotations

from pathlib import Path

from finance_context.api.app import create_app
from finance_context.settings import Settings

_PATHS = (
    "/healthz",
    "/readyz",
    "/v1/context-jobs",
    "/v1/context-jobs/{job_id}",
    "/v1/context-jobs/{job_id}/context.json",
    "/v1/context-jobs/{job_id}/context.md",
    "/v1/context-jobs/{job_id}/graph.json",
    "/v1/context-jobs/{job_id}/graph.md",
    "/v1/context-jobs/{job_id}/graph/trace",
    "/v1/context-jobs/{job_id}/graph/trace.md",
)


def _spec(tmp_path: Path) -> dict:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            llm_base_url=None,
            embedding_base_url=None,
            embedding_model=None,
        )
    )
    return app.openapi()


def _query_names(operation: dict) -> set[str]:
    return {item["name"] for item in operation.get("parameters", []) if item["in"] == "query"}


def _schema_ref(operation: dict, status: str) -> str:
    content = operation["responses"][status]["content"]["application/json"]
    return content["schema"]["$ref"]


def test_openapi_lists_routes_and_document_models(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    assert set(_PATHS) <= set(spec["paths"])
    schemas = spec["components"]["schemas"]
    for name in ("ContextDocument", "GraphDocument", "TraceDocument", "JobBody", "ErrorBody"):
        assert name in schemas

    post = spec["paths"]["/v1/context-jobs"]["post"]
    assert _schema_ref(post, "202").endswith("/JobBody")
    for code in ("400", "413", "422"):
        assert _schema_ref(post, code).endswith("/ErrorBody")

    job = spec["paths"]["/v1/context-jobs/{job_id}"]["get"]
    assert _schema_ref(job, "200").endswith("/JobBody")
    assert _schema_ref(job, "404").endswith("/ErrorBody")

    context = spec["paths"]["/v1/context-jobs/{job_id}/context.json"]["get"]
    assert _schema_ref(context, "200").endswith("/ContextDocument")
    graph = spec["paths"]["/v1/context-jobs/{job_id}/graph.json"]["get"]
    assert _schema_ref(graph, "200").endswith("/GraphDocument")

    for path in (
        "/v1/context-jobs/{job_id}/context.md",
        "/v1/context-jobs/{job_id}/graph.md",
        "/v1/context-jobs/{job_id}/graph/trace.md",
    ):
        content = spec["paths"][path]["get"]["responses"]["200"]["content"]
        assert "text/markdown" in content
        assert "application/json" not in content

    trace = spec["paths"]["/v1/context-jobs/{job_id}/graph/trace"]["get"]
    assert _schema_ref(trace, "200").endswith("/TraceDocument")
    assert {"from", "direction", "depth"} <= _query_names(trace)
    direction = next(item for item in trace["parameters"] if item["name"] == "direction")
    assert direction["schema"]["enum"] == ["precedents", "dependents"]
