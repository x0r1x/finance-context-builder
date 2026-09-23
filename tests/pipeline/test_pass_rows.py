from __future__ import annotations

from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet


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
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    reads: list[str] = []
    real = read_parquet

    def spy(path: Path) -> list[dict]:
        reads.append(Path(path).as_posix())
        return real(path)

    for module in (
        "finance_context.store.fs",
        "finance_context.formulas.stage",
        "finance_context.layout.stage",
        "finance_context.mapping.stage",
        "finance_context.graph.stage",
        "finance_context.app.pipeline",
    ):
        monkeypatch.setattr(f"{module}.read_parquet", spy)

    compiles: list[int] = []
    import finance_context.app.pipeline as pipeline_mod
    import finance_context.formulas.stage as formula_mod

    real_compile = formula_mod.compile_workbook

    def wrapped(dest_dir: Path):
        compiles.append(1)
        return real_compile(dest_dir)

    monkeypatch.setattr(pipeline_mod, "compile_workbook", wrapped)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    pipeline.run(dest, job_id="job-pass", source_filename="model.xlsx")
    assert compiles == [1]
    assert not any(path.endswith("ir/cells.parquet") for path in reads)

    for name in ("context.json", "context.md"):
        (dest / name).unlink()
    reads.clear()
    compiles.clear()
    pipeline.run(dest, job_id="job-pass", source_filename="model.xlsx")
    assert compiles == []
    assert any(path.endswith("ir/cells.parquet") for path in reads)
