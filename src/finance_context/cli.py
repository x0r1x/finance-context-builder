from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from finance_context.adapters.disk_store import DiskStore
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.pipeline import Pipeline, execute_job, mark_job_failed
from finance_context.app.publisher import publisher_matches, stale_from_meta
from finance_context.app.shared_cache import record_rebuild_stage
from finance_context.errors import ContextError
from finance_context.excel.legacy import LEGACY_SOURCE, SOURCE, source_name
from finance_context.jobqueue.embedded import start_for_serve, stop_for_serve
from finance_context.jobqueue.parents import arm_embedded_parent
from finance_context.jobqueue.pool import serve_workers, worker_check
from finance_context.memory import MEMORY_ERROR_DETAIL, out_of_memory
from finance_context.observability import configure_logging
from finance_context.settings import DEFAULT_DATA_DIR, Settings
from finance_context.store.filesystem import ensure_data_dir_local
from finance_context.store.fs import atomic_write_bytes, write_json

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _read_meta(dest: Path) -> dict | None:
    meta_path = dest / "meta" / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return meta if isinstance(meta, dict) else None


def _same_publisher(dest: Path) -> bool:
    return publisher_matches(_read_meta(dest))


def _read_owner(dest: Path) -> dict:
    path = dest / "owner.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@app.command()
def build(
    source: Path,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = DEFAULT_DATA_DIR,
) -> None:
    """Parse an Excel workbook and write context and graph as JSON and Markdown."""
    settings = Settings(data_dir=data_dir)
    configure_logging(level=settings.log_level, json_output=False)
    ensure_data_dir_local(
        settings.data_dir,
        allow_network=settings.data_dir_allow_network_fs,
    )
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
    previous_sha = _read_owner(dest).get("content_sha256")
    kept = source_name(data)
    atomic_write_bytes(book / kept, data)
    # A missing or invalid digest is not this workbook, so the old sibling is dropped.
    source_changed = previous_sha != digest
    if source_changed:
        # `build -o` reuses the directory. Drop the other source name so a previous
        # `source/source.xlsx` is not parsed in place of this `source.xls`. A repeat of the
        # same `.xls` keeps the converted sibling.
        sibling = LEGACY_SOURCE if kept == SOURCE else SOURCE
        (book / sibling).unlink(missing_ok=True)
    write_json(
        dest / "owner.json",
        {"content_sha256": digest, "source_filename": source.name},
    )
    published = (
        dest / "context" / "context.json",
        dest / "context" / "context.md",
        dest / "graph" / "graph.json",
        dest / "graph" / "graph.md",
    )
    if not source_changed and all(path.is_file() for path in published) and _same_publisher(dest):
        for path in published:
            typer.echo(path)
        typer.echo("reused")
        return
    # A different workbook must not keep the previous raw/ parse.
    # The child drops those files under the book lock. POST and build only record the stage.
    stale = "compile" if source_changed else stale_from_meta(_read_meta(dest))
    record_rebuild_stage(dest, stale)
    try:
        doc = execute_job(
            Pipeline(settings),
            dest,
            job_id=job_id,
            source_filename=source.name,
            content_sha256=digest,
            shared_dir=shared,
        )
    except ContextError as exc:
        mark_job_failed(dest, job_id, exc.code, detail=exc.detail or None)
        typer.echo(f"failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
    except Exception as exc:
        if not out_of_memory(exc):
            mark_job_failed(dest, job_id, type(exc).__name__)
            raise
        mark_job_failed(dest, job_id, "workbook_too_large", detail=MEMORY_ERROR_DETAIL)
        typer.echo(f"failed: workbook_too_large: {MEMORY_ERROR_DETAIL}", err=True)
        raise typer.Exit(code=1) from None
    for path in published:
        typer.echo(path)
    typer.echo(doc.meta.status)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8080,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
) -> None:
    """Serve the API. The RQ pool starts as a child unless EMBEDDED_WORKER is false."""
    if data_dir is not None:
        os.environ["DATA_DIR"] = str(data_dir)
    settings = Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    # The child has to be up before the socket accepts traffic. workers=1 keeps
    # this process, and the supervisor flag, in the process that serves /readyz.
    supervisor = start_for_serve(settings)
    # uvicorn restores the previous handler and re-raises SIGTERM after the
    # socket closes, which skips a finally. Stop the child from that handler.
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    def _stop_child(signum: int, frame: object) -> None:
        del frame
        stop_for_serve(supervisor)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, _stop_child)
    signal.signal(signal.SIGINT, _stop_child)
    try:
        uvicorn.run(
            "finance_context.api.app:app_from_env",
            host=host,
            port=port,
            factory=True,
            workers=1,
        )
    finally:
        stop_for_serve(supervisor)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


@app.command()
def worker(
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
    check: Annotated[bool, typer.Option("--check")] = False,
) -> None:
    """Run the shared job queue, or check Redis and DATA_DIR and exit."""
    if data_dir is not None:
        os.environ["DATA_DIR"] = str(data_dir)
    settings = Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    if os.environ.get("FINANCE_CONTEXT_EMBEDDED_WORKER") == "1":
        arm_embedded_parent(settings.job_parent_watch_interval_sec)
    if check:
        raise typer.Exit(code=worker_check(settings))
    serve_workers(settings)


if __name__ == "__main__":
    app()
