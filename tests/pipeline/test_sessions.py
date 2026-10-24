from __future__ import annotations

from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.ids import sha256_bytes
from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings
from finance_context.store.fs import write_json
from finance_context.store.paths import session_job_dir, shared_book_dir


def test_sessions_share_formula_ir(tmp_path: Path) -> None:
    source = tmp_path / "model.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                ],
            )
        ],
        shared_strings=["Item", "2024", "Revenue"],
    )
    raw = source.read_bytes()
    job_id = sha256_bytes(raw)
    root = tmp_path / "data"
    shared = shared_book_dir(root, job_id)
    shared.mkdir(parents=True)
    (shared / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (shared / "source" / "source.xlsx").write_bytes(raw)

    def once(session: str) -> None:
        settings = Settings(
            data_dir=root,
            session_id=session,
            _env_file=None,
        )
        dest = session_job_dir(root, job_id, session)
        write_json(
            dest / "owner.json",
            {"content_sha256": job_id, "source_filename": source.name},
        )
        Pipeline(settings).run(
            dest,
            job_id=job_id,
            shared_dir=shared,
            source_filename=source.name,
            content_sha256=job_id,
        )

    once("alpha")
    compiled = (shared / "ir" / "cells.parquet").stat().st_mtime_ns
    once("beta")

    assert (shared / "ir" / "cells.parquet").stat().st_mtime_ns == compiled
    assert (shared / "layout" / "layout.json").is_file()
    assert (session_job_dir(root, job_id, "alpha") / "context" / "context.md").is_file()
    assert (session_job_dir(root, job_id, "beta") / "context" / "context.md").is_file()
