from __future__ import annotations

import json
import os
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.cli import build
from finance_context.settings import Settings


def _offline_settings(**kwargs: object) -> Settings:
    data_dir = kwargs.get("data_dir", Path("data"))
    return Settings(
        data_dir=data_dir,  # type: ignore[arg-type]
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
        _env_file=None,
    )


def test_build_reuses_a_snapshot_until_the_publisher_changes(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr("finance_context.cli.Settings", _offline_settings)
    source = build_xlsx(
        tmp_path / "model.xlsx",
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "Revenue"],
    )
    dest = tmp_path / "out"
    data_dir = tmp_path / "data"
    build(source, output=dest, data_dir=data_dir)
    context = dest / "context.json"
    cells = dest / "ir" / "cells.parquet"
    raw = dest / "raw" / "workbook.json"
    assert context.is_file()
    assert cells.is_file()
    assert raw.is_file()
    cell_bytes = cells.read_bytes()
    stamped = context.stat().st_mtime_ns - 1_000_000_000
    os.utime(context, ns=(stamped, stamped))

    capsys.readouterr()
    build(source, output=dest, data_dir=data_dir)
    reused = capsys.readouterr().out
    assert reused.strip().splitlines()[-1] == "reused"
    assert context.stat().st_mtime_ns == stamped
    assert cells.read_bytes() == cell_bytes

    meta_path = dest / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["publisher"] = "stale"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    build(source, output=dest, data_dir=data_dir)
    assert context.stat().st_mtime_ns != stamped
    assert cells.read_bytes() == cell_bytes
    assert raw.is_file()
    assert json.loads(meta_path.read_text(encoding="utf-8"))["publisher"] != "stale"
    assert "reused" not in capsys.readouterr().out
