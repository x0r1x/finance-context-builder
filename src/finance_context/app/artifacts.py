from __future__ import annotations

from pathlib import Path


def clear_downstream_artifacts(dest: Path) -> None:
    """Drop layout, mapping, and published documents. Parse and formula IR stay."""
    for name in (
        "layout.json",
        "mapping.json",
        "context.json",
        "context.md",
        "context.md.renderer",
        "meta.json",
        "graph.json",
        "graph.md",
        "graph-edges.json",
        "graph-dangling.json",
        "formulas.json",
    ):
        (dest / name).unlink(missing_ok=True)
    ir = dest / "ir"
    # Formula IR depends only on the workbook bytes. A publisher change keeps it.
    if not (dest / "source.xlsx").is_file():
        for name in ("cells.parquet", "edges.parquet", "cell_edges.parquet"):
            (ir / name).unlink(missing_ok=True)
    (ir / "graph_index.parquet").unlink(missing_ok=True)
    (ir / "graph_edges.parquet").unlink(missing_ok=True)
