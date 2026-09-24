from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from finance_context.adapters.disk_store import DiskStore
from finance_context.app.artifacts import clear_downstream_artifacts
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.pipeline import Pipeline
from finance_context.app.publisher import publisher_matches, stale_from_meta
from finance_context.observability import configure_logging
from finance_context.settings import _DEFAULT_DATA_DIR, Settings
from finance_context.store.fs import atomic_write_bytes, write_json

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _read_meta(dest: Path) -> dict | None:
    meta_path = dest / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return meta if isinstance(meta, dict) else None


def _same_publisher(dest: Path) -> bool:
    return publisher_matches(_read_meta(dest))


@app.command()
def build(
    source: Path,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = _DEFAULT_DATA_DIR,
) -> None:
    """Parse an Excel workbook and write context and graph as JSON and Markdown."""
    settings = Settings(data_dir=data_dir)
    configure_logging(level=settings.log_level, json_output=False)
    data = source.read_bytes()
    digest = sha256_bytes(data)
    job_id = job_id_for(digest)
    store = DiskStore(data_dir, session_id=settings.session_id)
    if output is None:
        dest = store.dest_dir(job_id)
        shared: Path | None = store.shared_dir(job_id)
    else:
        dest = output
        shared = None
    dest.mkdir(parents=True, exist_ok=True)
    book = shared or dest
    book.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(book / "source.xlsx", data)
    write_json(
        dest / "owner.json",
        {"content_sha256": digest, "source_filename": source.name},
    )
    published = (
        dest / "context.json",
        dest / "context.md",
        dest / "graph.json",
        dest / "graph.md",
    )
    if all(path.is_file() for path in published) and _same_publisher(dest):
        for path in published:
            typer.echo(path)
        typer.echo("reused")
        return
    clear_downstream_artifacts(dest, stale_from=stale_from_meta(_read_meta(dest)), shared=shared)
    doc = Pipeline(settings).run(
        dest,
        job_id=job_id,
        source_filename=source.name,
        content_sha256=digest,
        shared_dir=shared,
    )
    for path in published:
        typer.echo(path)
    typer.echo(doc.meta.status)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8080,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
) -> None:
    """Serve the API. Each workbook runs in its own process."""
    if data_dir is not None:
        import os

        os.environ["DATA_DIR"] = str(data_dir)
    uvicorn.run(
        "finance_context.api.app:app_from_env",
        host=host,
        port=port,
        factory=True,
        workers=1,
    )
