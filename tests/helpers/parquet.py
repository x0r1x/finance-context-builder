from __future__ import annotations

import json
from pathlib import Path

from finance_context.store.fs import read_parquet


def load_parquet(path: Path) -> list[dict]:
    return read_parquet(path)


def load_cells(audit_dir: Path) -> list[dict]:
    return load_parquet(audit_dir / "raw" / "cells.parquet")


def load_workbook_json(audit_dir: Path) -> dict:
    return json.loads((audit_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
