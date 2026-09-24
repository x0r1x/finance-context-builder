from __future__ import annotations

from pathlib import Path

from finance_context.app.publisher import STAGE_ORDER

_PUBLISHED = (
    "context.json",
    "context.md",
    "context.md.renderer",
)
_GRAPH = (
    "graph.json",
    "graph.md",
    "graph-edges.json",
    "graph-dangling.json",
    "formulas.json",
)
_GRAPH_IR = ("graph_index.parquet", "graph_edges.parquet")
_FORMULA_IR = ("cells.parquet", "edges.parquet", "cell_edges.parquet", "compile.json")
_RAW = ("cells.parquet", "cell_presence.parquet", "workbook.json")


def clear_downstream_artifacts(dest: Path, stale_from: str | None = "layout") -> None:
    """Drop artifacts from the first stale stage downward. Parse bytes in source.xlsx stay.

    ``stale_from=None`` leaves the directory alone. A missing stage map uses ``layout``,
    which keeps formula IR. ``compile`` also drops the parsed raw sheets.
    """
    if stale_from is None:
        return
    if stale_from not in STAGE_ORDER:
        stale_from = "layout"
    rank = STAGE_ORDER.index(stale_from)

    def reached(stage: str) -> bool:
        return rank <= STAGE_ORDER.index(stage)

    if reached("publish"):
        for name in _PUBLISHED:
            (dest / name).unlink(missing_ok=True)
    if reached("graph"):
        for name in _GRAPH:
            (dest / name).unlink(missing_ok=True)
        ir = dest / "ir"
        for name in _GRAPH_IR:
            (ir / name).unlink(missing_ok=True)
    if reached("mapping"):
        (dest / "mapping.json").unlink(missing_ok=True)
    if reached("layout"):
        (dest / "layout.json").unlink(missing_ok=True)
    (dest / "meta.json").unlink(missing_ok=True)
    ir = dest / "ir"
    drop_formula = reached("compile") or not (dest / "source.xlsx").is_file()
    if drop_formula:
        for name in _FORMULA_IR:
            (ir / name).unlink(missing_ok=True)
    if reached("compile"):
        raw = dest / "raw"
        for name in _RAW:
            (raw / name).unlink(missing_ok=True)
