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


def test_publish_change_keeps_graph(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(
        dest,
        "layout.json",
        "graph.json",
        "graph.md",
        "context.json",
        "context.md",
        "ir/cells.parquet",
        "ir/graph_edges.parquet",
        "meta.json",
    )
    clear_downstream_artifacts(dest, stale_from="publish")
    assert (dest / "layout.json").is_file()
    assert (dest / "graph.json").is_file()
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "ir" / "graph_edges.parquet").is_file()
    assert not (dest / "context.json").exists()
    assert not (dest / "context.md").exists()
    assert not (dest / "meta.json").exists()


def test_graph_change_keeps_layout_and_formula_ir(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(
        dest,
        "layout.json",
        "graph.json",
        "context.json",
        "ir/cells.parquet",
        "raw/workbook.json",
    )
    clear_downstream_artifacts(dest, stale_from="graph")
    assert (dest / "layout.json").is_file()
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "raw" / "workbook.json").is_file()
    assert not (dest / "graph.json").exists()
    assert not (dest / "context.json").exists()


def test_legacy_meta_clears_below_layout_and_keeps_ir(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(dest, "layout.json", "ir/cells.parquet", "raw/workbook.json")
    assert stale_from_meta(None) == "layout"
    assert stale_from_meta({}) == "layout"
    assert stale_from_meta({"publisher": "stale"}) == "layout"
    clear_downstream_artifacts(dest, stale_from=stale_from_meta({}))
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "raw" / "workbook.json").is_file()
    assert not (dest / "layout.json").exists()


def test_stage_map_selects_only_the_first_difference() -> None:
    current = stage_fingerprints()
    stamp = publisher_fingerprint()
    assert set(current) == {"compile", "layout", "graph", "publish"}
    assert stale_from_meta({"publisher": stamp, "stages": current}) is None
    assert stale_from_meta({"stages": current}) == "layout"
    publish = dict(current)
    publish["publish"] = "0" * 64
    assert stale_from_meta({"publisher": "stale", "stages": publish}) == "publish"
    graph = dict(current)
    graph["graph"] = "0" * 64
    graph["publish"] = "0" * 64
    assert stale_from_meta({"publisher": "stale", "stages": graph}) == "graph"


def test_compile_change_drops_raw_and_formula_ir(tmp_path) -> None:
    dest = tmp_path / "job"
    _touch_job(dest, "ir/cells.parquet", "raw/workbook.json", "layout.json")
    clear_downstream_artifacts(dest, stale_from="compile")
    assert (dest / "source.xlsx").is_file()
    assert not (dest / "ir" / "cells.parquet").exists()
    assert not (dest / "raw" / "workbook.json").exists()
    assert not (dest / "layout.json").exists()


def test_layout_code_edit_restales_the_layout_stage(tmp_path, monkeypatch) -> None:
    from finance_context.app import publisher

    (tmp_path / "layout").mkdir()
    rows = tmp_path / "layout" / "rows.py"
    rows.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(publisher, "_PACKAGE", tmp_path)
    before = publisher._compute_stages()
    rows.write_text("x = 2\n", encoding="utf-8")
    after = publisher._compute_stages()
    assert before["layout"] != after["layout"]
    assert before["compile"] == after["compile"]


def test_layout_stage_hashes_modules_it_imports(tmp_path, monkeypatch) -> None:
    from finance_context.app import publisher

    (tmp_path / "layout").mkdir()
    (tmp_path / "context").mkdir()
    (tmp_path / "layout" / "units.py").write_text(
        "from finance_context.context.measure import unit_caption\n",
        encoding="utf-8",
    )
    measure = tmp_path / "context" / "measure.py"
    measure.write_text("x = 1\n", encoding="utf-8")
    series = tmp_path / "context" / "series.py"
    series.write_text("y = 1\n", encoding="utf-8")
    monkeypatch.setattr(publisher, "_PACKAGE", tmp_path)
    before = publisher._compute_stages()
    series.write_text("y = 2\n", encoding="utf-8")
    untouched = publisher._compute_stages()
    assert before["layout"] == untouched["layout"]
    assert before["publish"] != untouched["publish"]
    measure.write_text("x = 2\n", encoding="utf-8")
    after = publisher._compute_stages()
    assert before["layout"] != after["layout"]
    assert untouched["publish"] != after["publish"]
    assert before["compile"] == after["compile"]


def test_layout_stage_hashes_parent_package_inits(tmp_path, monkeypatch) -> None:
    from finance_context.app import publisher

    (tmp_path / "layout").mkdir()
    (tmp_path / "context").mkdir()
    (tmp_path / "layout" / "units.py").write_text(
        "from finance_context.context.measure import unit_caption\n",
        encoding="utf-8",
    )
    (tmp_path / "context" / "measure.py").write_text("x = 1\n", encoding="utf-8")
    package_init = tmp_path / "__init__.py"
    package_init.write_text("x = 1\n", encoding="utf-8")
    context_init = tmp_path / "context" / "__init__.py"
    context_init.write_text("y = 1\n", encoding="utf-8")
    sibling = tmp_path / "context" / "other.py"
    sibling.write_text("z = 1\n", encoding="utf-8")
    monkeypatch.setattr(publisher, "_PACKAGE", tmp_path)
    before = publisher._compute_stages()
    sibling.write_text("z = 2\n", encoding="utf-8")
    untouched = publisher._compute_stages()
    assert before["layout"] == untouched["layout"]
    context_init.write_text("y = 2\n", encoding="utf-8")
    after_context = publisher._compute_stages()
    assert before["layout"] != after_context["layout"]
    assert before["compile"] == after_context["compile"]
    package_init.write_text("x = 2\n", encoding="utf-8")
    after_package = publisher._compute_stages()
    assert after_context["layout"] != after_package["layout"]


def test_context_only_module_does_not_restale_layout() -> None:
    import finance_context.context as context
    from finance_context.app import publisher
    from finance_context.context.build import build_context

    assert context.build_context is build_context
    buckets: dict[str, list] = {name: [] for name in publisher.STAGE_ORDER}
    for path in publisher._sources():
        stage = publisher._stage_of(path.relative_to(publisher._PACKAGE).as_posix())
        if stage is not None:
            buckets[stage].append(path)
    publisher._include_layout_imports(buckets)
    hashed = {path.relative_to(publisher._PACKAGE).as_posix() for path in buckets["layout"]}
    assert "context/measure.py" in hashed
    assert "context/assemble.py" not in hashed
    assert "context/build.py" not in hashed

    assemble = publisher._PACKAGE / "context" / "assemble.py"
    original = assemble.read_bytes()
    before = publisher._compute_stages()
    try:
        assemble.write_bytes(original + b"\n")
        after = publisher._compute_stages()
    finally:
        assemble.write_bytes(original)
    assert before["layout"] == after["layout"]
    assert before["publish"] != after["publish"]


def test_stray_package_json_does_not_restale_layout(tmp_path, monkeypatch) -> None:
    from finance_context.app import publisher

    (tmp_path / "layout").mkdir()
    (tmp_path / "layout" / "rows.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(publisher, "_PACKAGE", tmp_path)
    before = publisher._compute_stages()
    (tmp_path / "layout" / "notes.json").write_text("{}\n", encoding="utf-8")
    after = publisher._compute_stages()
    assert before == after
