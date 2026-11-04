"""Run one synthetic workbook through the pipeline with a fixed identity.

The source file name and job id are part of ``context.json`` and ``context.md``.
Callers pass the same names every time so those artifacts stay byte-stable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from finance_context.app.ids import sha256_bytes
from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings

ARTIFACTS = (
    "context/context.json",
    "context/context.md",
    "graph/graph.json",
    "layout/layout.json",
)


def run_book(xlsx: Path, dest: Path, *, filename: str, job_id: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    data = xlsx.read_bytes()
    source = dest / "source" / "source.xlsx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)
    Pipeline(Settings(data_dir=dest.parent / "data", _env_file=None)).run(
        dest,
        job_id=job_id,
        source_filename=filename,
        content_sha256=sha256_bytes(data),
    )


def artifact_hashes(dest: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((dest / name).read_bytes()).hexdigest() for name in ARTIFACTS
    }
