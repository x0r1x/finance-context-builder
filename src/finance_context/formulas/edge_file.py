"""Formula edges from ``ir/edges.parquet``, one batch at a time.

The compile stage writes this file and then drops the ``Edge`` list. Later
stages walk the file in the same order instead of loading it back.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from finance_context.formulas.models import Edge, as_edge

# Same columns as ``formulas.stage.IR_EDGE_COLUMNS``. Kept here so layout and
# graph can read the file without importing the compile stage.
_EDGE_COLUMNS = ("source", "kind", "target", "unresolved", "truncated", "named")


def iter_formula_edges(path: Path) -> Iterator[Edge]:
    """Edges in file order. A missing file yields nothing."""
    if not path.is_file():
        return
    import pyarrow.parquet as pq

    names = list(_EDGE_COLUMNS)
    for batch in pq.ParquetFile(path).iter_batches(columns=names, batch_size=65536):
        columns = [batch.column(index).to_pylist() for index in range(len(names))]
        width = batch.num_rows
        del batch
        for index in range(width):
            target = columns[2][index]
            kind = columns[1][index] or "ref"
            yield Edge(
                kind=kind,  # type: ignore[arg-type]
                source="" if columns[0][index] is None else str(columns[0][index]),
                target=None if target is None else str(target),
                unresolved=bool(columns[3][index]),
                truncated=bool(columns[4][index]),
                named=bool(columns[5][index]),
            )
        del columns


def iterate_edges(edges: Iterable | Path | None) -> Iterator[Edge]:
    """A caller-supplied list, or a parquet path. ``None`` yields nothing.

    A path is scanned from the start on every call, so a sheet can read it
    again without keeping the rows.
    """
    if edges is None:
        return
    if isinstance(edges, Path):
        yield from iter_formula_edges(edges)
        return
    for edge in edges:
        yield as_edge(edge)
