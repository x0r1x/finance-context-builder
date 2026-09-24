"""Time one pipeline run per corpus workbook and a cold import of the formula compiler.

Numbers are for a local before/after note. They are not a golden test.
"""

from __future__ import annotations

import logging
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BOOKS = (
    Path("resources/cashflow.xlsx"),
    Path("resources/corpus/packt-project-finance.xlsx"),
    Path("resources/corpus/rvi-project-finance.xlsx"),
)


class _Stages(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, int]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "stage_done":
            return
        stage = getattr(record, "stage", None)
        duration = getattr(record, "duration_ms", None)
        if isinstance(stage, str) and isinstance(duration, int):
            self.rows.append((stage, duration))


def _rss_kb() -> int:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes. Linux reports kilobytes.
    if sys.platform == "darwin":
        return usage // 1024
    return usage


def _cold_import_ms() -> float:
    code = (
        "import time\n"
        "started = time.perf_counter()\n"
        "import finance_context.formulas.stage\n"
        "print((time.perf_counter() - started) * 1000)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def main() -> None:
    cold_ms = _cold_import_ms()
    print(f"cold_import_ms {cold_ms:.1f}")

    from finance_context.app.pipeline import Pipeline
    from finance_context.observability import configure_logging
    from finance_context.settings import Settings

    configure_logging(level="INFO", json_output=False)
    captured = _Stages()
    logging.getLogger("finance_context").addHandler(captured)

    for book in BOOKS:
        if not book.is_file():
            print(f"missing {book}")
            continue
        captured.rows.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "job"
            dest.mkdir()
            (dest / "source.xlsx").write_bytes(book.read_bytes())
            settings = Settings(
                data_dir=root,
                llm_base_url=None,
                embedding_base_url=None,
                embedding_model=None,
            )
            before = _rss_kb()
            started = time.perf_counter()
            Pipeline(settings).run(
                dest,
                job_id="bench",
                source_filename=book.name,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            after = _rss_kb()
        print(
            f"{book.name} total_ms {elapsed_ms:.0f} "
            f"rss_kb {after} rss_delta_kb {after - before}"
        )
        for stage, duration in captured.rows:
            share = (duration / elapsed_ms * 100) if elapsed_ms else 0
            print(f"  {stage} {duration} {share:.0f}%")
        if book.name.startswith("rvi"):
            print(f"  cold_import_share {(cold_ms / elapsed_ms * 100) if elapsed_ms else 0:.0f}%")


if __name__ == "__main__":
    main()
