from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from finance_context.adapters.disk_store import DiskStore
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.pipeline import Pipeline
from finance_context.observability import configure_logging
from finance_context.settings import Settings
from finance_context.store.fs import atomic_write_bytes, write_json

app = typer.Typer(no_args_is_help=True, add_completion=False)
_DEFAULT_DATA_DIR = Path("data")


@app.command()
def build(
    source: Path,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = _DEFAULT_DATA_DIR,
) -> None:
    """Parse an Excel workbook and write context.json + context.md."""
    settings = Settings(data_dir=data_dir)
    configure_logging(level=settings.log_level, json_output=False)
    data = source.read_bytes()
    digest = sha256_bytes(data)
    job_id = job_id_for(digest)
    dest = output or DiskStore(data_dir).dest_dir(job_id)
    dest.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest / "source.xlsx", data)
    write_json(
        dest / "owner.json",
        {"content_sha256": digest, "source_filename": source.name},
    )
    doc = Pipeline(settings).run(
        dest,
        job_id=job_id,
        source_filename=source.name,
        content_sha256=digest,
    )
    typer.echo(dest / "context.json")
    typer.echo(dest / "context.md")
    typer.echo(doc.meta.status)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8080,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
) -> None:
    """Run the in-process FastAPI worker."""
    if data_dir is not None:
        import os

        os.environ["DATA_DIR"] = str(data_dir)
    uvicorn.run("finance_context.api.app:app_from_env", host=host, port=port, factory=True)
