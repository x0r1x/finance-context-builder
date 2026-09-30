from __future__ import annotations

from tests.helpers.slice_book import sample_context, sample_graph, sample_links

from finance_context.context.summary import build_summary


def _keys(value: object):
    if isinstance(value, dict):
        yield from value
        for item in value.values():
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_summary_keeps_counters_and_drops_links_and_caches() -> None:
    context = sample_context()
    graph = sample_graph("job", sample_links())
    context_payload = context.model_dump(mode="json")
    context_payload["blocks"] = [{"not": "a block"}]
    graph_payload = graph.model_dump(mode="json", by_alias=True)
    graph_payload["links"] = [{"not": "a link"}]
    document = build_summary(context_payload, graph_payload, meta=context.meta.model_dump())
    payload = document.model_dump(mode="json", by_alias=True)
    assert document.schema_version == "summary-1"
    assert document.context_schema_version == "1.13.0"
    assert document.graph_schema_version == "1.7.0"
    assert document.missing_cached_values == 17
    assert document.unresolved.count == 2
    assert document.defined_name_count == 1
    assert document.cycles[0].members == 4
    assert document.cycles[0].cycle_class == "unexpected"
    assert payload["cycles"][0]["class"] == "unexpected"
    assert document.circularity_hints[0].label == "Total"
    assert document.warnings == ["cache missing inside the phase"]
    assert "mapping_quality" not in payload["mapping_stats"]
    forbidden = {"links", "values", "series", "normalized_values", "cell_ids", "defined_names"}
    assert forbidden.isdisjoint(_keys(payload))
    text = str(payload)
    assert "1234.56" not in text
    assert "=SUM(H10:H11)" not in text
    assert "Growth" not in text
    assert "Operation!H12" not in text
