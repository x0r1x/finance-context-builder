from __future__ import annotations

from finance_context.app.artifacts import clear_downstream_artifacts
from finance_context.app.publisher import (
    publisher_changed,
    publisher_fingerprint,
    publisher_matches,
    stage_fingerprints,
    stale_from_meta,
)


def test_fingerprint_is_a_stable_sha256() -> None:
    first = publisher_fingerprint()
    assert first == publisher_fingerprint()
    assert len(first) == 64
    assert first.isalnum()
    assert first == first.lower()


def test_snapshot_matches_only_the_fingerprint_that_wrote_it() -> None:
    current = publisher_fingerprint()
    assert publisher_matches({"publisher": current})
    assert not publisher_matches({"publisher": "stale"})
    assert not publisher_matches({})
    assert not publisher_matches(None)
    assert not publisher_changed({})
    assert not publisher_changed(None)


def _touch_job(dest, *names: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "ir").mkdir(exist_ok=True)
    (dest / "raw").mkdir(exist_ok=True)
    (dest / "source.xlsx").write_bytes(b"xlsx")
    for name in names:
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def test_publish_change_keeps_mapping_and_graph(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(
        dest,
        "layout.json",
        "mapping.json",
        "graph.json",
        "graph.md",
        "context.json",
        "context.md",
        "ir/cells.parquet",
        "ir/graph_edges.parquet",
        "meta.json",
    )
    clear_downstream_artifacts(dest, stale_from="publish")
    assert (dest / "mapping.json").is_file()
    assert (dest / "layout.json").is_file()
    assert (dest / "graph.json").is_file()
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "ir" / "graph_edges.parquet").is_file()
    assert not (dest / "context.json").exists()
    assert not (dest / "context.md").exists()
    assert not (dest / "meta.json").exists()


def test_mapping_change_keeps_formula_ir(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(
        dest,
        "layout.json",
        "mapping.json",
        "graph.json",
        "context.json",
        "ir/cells.parquet",
        "raw/workbook.json",
    )
    clear_downstream_artifacts(dest, stale_from="mapping")
    assert (dest / "layout.json").is_file()
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "raw" / "workbook.json").is_file()
    assert not (dest / "mapping.json").exists()
    assert not (dest / "graph.json").exists()
    assert not (dest / "context.json").exists()


def test_legacy_meta_clears_below_layout_and_keeps_ir(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(dest, "layout.json", "mapping.json", "ir/cells.parquet", "raw/workbook.json")
    assert stale_from_meta(None) == "layout"
    assert stale_from_meta({}) == "layout"
    assert stale_from_meta({"publisher": "stale"}) == "layout"
    clear_downstream_artifacts(dest, stale_from=stale_from_meta({}))
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "raw" / "workbook.json").is_file()
    assert not (dest / "layout.json").exists()
    assert not (dest / "mapping.json").exists()


def test_stage_map_selects_only_the_first_difference() -> None:
    current = stage_fingerprints()
    stamp = publisher_fingerprint()
    assert set(current) == {"compile", "layout", "mapping", "graph", "publish"}
    assert stale_from_meta({"publisher": stamp, "stages": current}) is None
    assert stale_from_meta({"stages": current}) == "layout"
    publish = dict(current)
    publish["publish"] = "0" * 64
    assert stale_from_meta({"publisher": "stale", "stages": publish}) == "publish"
    mapping = dict(current)
    mapping["mapping"] = "0" * 64
    mapping["publish"] = "0" * 64
    assert stale_from_meta({"publisher": "stale", "stages": mapping}) == "mapping"


def test_compile_change_drops_raw_and_formula_ir(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(dest, "ir/cells.parquet", "raw/workbook.json", "layout.json")
    clear_downstream_artifacts(dest, stale_from="compile")
    assert (dest / "source.xlsx").is_file()
    assert not (dest / "ir" / "cells.parquet").exists()
    assert not (dest / "raw" / "workbook.json").exists()
    assert not (dest / "layout.json").exists()
