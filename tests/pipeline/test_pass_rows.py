from __future__ import annotations

from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet, read_parquet_dicts


def _model(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100", formula="=1+1"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "Revenue"],
    )


def test_fresh_compile_is_not_reread_and_stamp_skips_compile(
    tmp_path: Path, dest: Path, monkeypatch
) -> None:
    source = _model(tmp_path / "model.xlsx")
    (dest / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (dest / "source" / "source.xlsx").write_bytes(source.read_bytes())
    reads: list[str] = []
    column_reads: list[str] = []
    real = read_parquet
    real_columns = read_parquet_dicts

    def spy(path: Path) -> list[dict]:
        reads.append(Path(path).as_posix())
        return real(path)

    def spy_columns(path: Path, columns, **kwargs) -> list[dict]:
        column_reads.append(Path(path).as_posix())
        return real_columns(path, columns, **kwargs)

    for module in (
        "finance_context.store.fs",
        "finance_context.formulas.stage",
        "finance_context.layout.stage",
        "finance_context.graph.stage",
        "finance_context.app.pipeline",
    ):
        monkeypatch.setattr(f"{module}.read_parquet", spy)
        monkeypatch.setattr(f"{module}.read_parquet_dicts", spy_columns, raising=False)

    import finance_context.layout.stage as layout_stage
    import finance_context.store.duckdb_io as duckdb_io

    real_iter = duckdb_io._iter_parquet_rows

    def spy_iter(path: Path, columns, **kwargs):
        column_reads.append(Path(path).as_posix())
        return real_iter(path, columns, **kwargs)

    # Layout and the edge reload stream batches instead of building a dict list.
    monkeypatch.setattr(duckdb_io, "_iter_parquet_rows", spy_iter)
    monkeypatch.setattr(layout_stage, "_iter_parquet_rows", spy_iter)

    compiles: list[int] = []
    import finance_context.app.pipeline as pipeline_mod
    import finance_context.formulas.stage as formula_mod
    import finance_context.layout.stage as layout_stage

    real_group = layout_stage._group_layout_cells

    def spy_group(path: Path):
        column_reads.append(Path(path).as_posix())
        return real_group(path)

    monkeypatch.setattr(layout_stage, "_group_layout_cells", spy_group)

    real_compile = formula_mod.compile_workbook

    def wrapped(dest_dir: Path, **kwargs):
        compiles.append(1)
        return real_compile(dest_dir, **kwargs)

    monkeypatch.setattr(pipeline_mod, "compile_workbook", wrapped)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    pipeline.run(dest, job_id="job-pass", source_filename="model.xlsx")
    assert compiles == [1]
    # The full row dump stays unread. Layout and context ask for columns.
    assert not any(path.endswith("ir/cells.parquet") for path in reads)
    assert any(path.endswith("ir/cells.parquet") for path in column_reads)

    for name in ("context/context.json", "context/context.md"):
        (dest / name).unlink()
    reads.clear()
    column_reads.clear()
    compiles.clear()
    scanned: list[str] = []
    import finance_context.context.build as context_build

    real_stats = context_build._formula_stats

    def spy_stats(path: Path):
        scanned.append(Path(path).as_posix())
        return real_stats(path)

    monkeypatch.setattr(context_build, "_formula_stats", spy_stats)
    pipeline.run(dest, job_id="job-pass", source_filename="model.xlsx")
    assert compiles == []
    assert any(path.endswith("ir/cells.parquet") for path in scanned)
