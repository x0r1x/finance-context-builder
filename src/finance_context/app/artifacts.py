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


def clear_downstream_artifacts(
    dest: Path, stale_from: str | None = "layout", *, shared: Path | None = None
) -> None:
    """Drop artifacts from the first stale stage downward. Parse bytes in source.xlsx stay.

    ``dest`` holds the session publication. ``shared`` holds source, raw, formula IR, and
    layout. When ``shared`` is omitted both live in ``dest``. ``stale_from=None`` leaves
    the directories alone. A missing stage map uses ``layout``, which keeps formula IR.
    ``compile`` also drops the parsed raw sheets. ``.lock`` is never removed.
    """
    if stale_from is None:
        return
    book = shared or dest
    normalized = _normalize_stage(stale_from)
    rank = STAGE_ORDER.index(normalized)

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
    clear_shared_artifacts(book, normalized)
    (dest / "meta.json").unlink(missing_ok=True)


def clear_shared_artifacts(book: Path, stale_from: str | None) -> bool:
    """Drop shared ``layout.json``, formula IR, and ``raw/`` from ``stale_from`` down.

    The caller holds ``book / ".lock"``. Source bytes and ``.lock`` stay. Returns
    True when a file that was present is removed. ``None`` leaves the tree alone.
    A missing ``source.xlsx`` also drops formula IR, matching a book that has not
    been converted yet.
    """
    if stale_from is None:
        return False
    normalized = _normalize_stage(stale_from)
    rank = STAGE_ORDER.index(normalized)

    def reached(stage: str) -> bool:
        return rank <= STAGE_ORDER.index(stage)

    removed = False
    if reached("layout"):
        removed |= _unlink(book / "layout.json")
    ir = book / "ir"
    drop_formula = reached("compile") or not (book / "source.xlsx").is_file()
    if drop_formula:
        for name in _FORMULA_IR:
            removed |= _unlink(ir / name)
    if reached("compile"):
        raw = book / "raw"
        for name in _RAW:
            removed |= _unlink(raw / name)
    return removed


def _normalize_stage(stale_from: str) -> str:
    if stale_from not in STAGE_ORDER:
        return "layout"
    return stale_from


def _unlink(path: Path) -> bool:
    if not path.is_file():
        return False
    path.unlink()
    return True
