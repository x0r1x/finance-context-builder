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
    "/v1/context-jobs/{job_id}/catalog",
    "/v1/context-jobs/{job_id}/summary",
    "/v1/context-jobs/{job_id}/observations",
)
_HTTP = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
_METHODS = {
    "/healthz": {"get"},
    "/readyz": {"get"},
    "/v1/context-jobs": {"get", "post"},
    "/v1/context-jobs/{job_id}": {"get"},
    "/v1/context-jobs/{job_id}/context.json": {"get"},
    "/v1/context-jobs/{job_id}/context.md": {"get"},
    "/v1/context-jobs/{job_id}/graph.json": {"get"},
    "/v1/context-jobs/{job_id}/graph.md": {"get"},
    "/v1/context-jobs/{job_id}/graph/trace": {"get"},
    "/v1/context-jobs/{job_id}/graph/trace.md": {"get"},
    "/v1/context-jobs/{job_id}/catalog": {"get"},
    "/v1/context-jobs/{job_id}/summary": {"get"},
    "/v1/context-jobs/{job_id}/observations": {"get"},
}


def _spec(tmp_path: Path) -> dict:
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            llm_base_url=None,
            embedding_base_url=None,
            embedding_model=None,
            _env_file=None,
        )
    )
    return app.openapi()


def _query_names(operation: dict) -> set[str]:
    return {item["name"] for item in operation.get("parameters", []) if item["in"] == "query"}


def _const(schema: dict, field: str) -> str | None:
    prop = schema["properties"][field]
    if "const" in prop:
        return prop["const"]
    enum = prop.get("enum")
    if enum:
        return enum[0]
    return None


def _schema_ref(operation: dict, status: str) -> str:
    content = operation["responses"][status]["content"]["application/json"]
    return content["schema"]["$ref"]


def test_openapi_paths_and_methods_are_exact(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    found = {
        path: {key for key in item if key in _HTTP}
        for path, item in spec["paths"].items()
    }
    assert found == _METHODS


def test_openapi_lists_routes_and_document_models(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    assert set(spec["paths"]) == set(_PATHS)
    schemas = spec["components"]["schemas"]
    for name in (
        "ContextDocument",
        "GraphDocument",
        "TraceDocument",
        "JobBody",
        "JobListItem",
        "ErrorBody",
        "CatalogDocument",
        "SummaryDocument",
        "ObservationDocument",
    ):
        assert name in schemas
    assert _const(schemas["CatalogDocument"], "schema_version") == "catalog-1"
    assert _const(schemas["SummaryDocument"], "schema_version") == "summary-1"
    assert _const(schemas["ObservationDocument"], "schema_version") == "observation-1"

    listing = spec["paths"]["/v1/context-jobs"]["get"]
    listed = listing["responses"]["200"]["content"]["application/json"]["schema"]
    assert listed["items"]["$ref"].endswith("/JobListItem")
    assert _query_names(listing) == {"status", "q"}

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
    assert _query_names(trace) == {"from", "direction", "depth"}
    direction = next(item for item in trace["parameters"] if item["name"] == "direction")
    assert direction["schema"]["enum"] == ["precedents", "dependents"]

    catalog = spec["paths"]["/v1/context-jobs/{job_id}/catalog"]["get"]
    assert _schema_ref(catalog, "200").endswith("/CatalogDocument")
    assert _query_names(catalog) == {
        "q",
        "concept_id",
        "sheet",
        "disposition",
        "limit",
        "offset",
    }
    described = {item["name"]: item.get("description") for item in catalog["parameters"]}
    assert described["q"]
    assert "label_path" not in described["q"]

    trace_described = {item["name"]: item.get("description") for item in trace["parameters"]}
    assert trace_described["from"]
    assert "concept_id" not in trace_described["from"]
    assert "label_path" not in trace_described["from"]

    summary = spec["paths"]["/v1/context-jobs/{job_id}/summary"]["get"]
    assert _schema_ref(summary, "200").endswith("/SummaryDocument")

    observations = spec["paths"]["/v1/context-jobs/{job_id}/observations"]["get"]
    assert _schema_ref(observations, "200").endswith("/ObservationDocument")
    assert _query_names(observations) == {
        "row_key",
        "concept_id",
        "q",
        "period_id",
        "phase",
        "precedent_depth",
        "limit",
    }
    observed = {item["name"]: item.get("description") for item in observations["parameters"]}
    assert observed["q"] == described["q"]
    assert "label_path" not in observed["q"]
