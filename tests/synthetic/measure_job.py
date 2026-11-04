"""Measure one synthetic book in a fresh process.

Stage lines are the pipeline ``stage_done`` records: ``duration_ms`` and
``rss_peak_mb``. ``MALLOC_ARENA_MAX=2`` is set by the caller so the RSS matches
the way those books were measured. This module does not change product limits.
"""

from __future__ import annotations

import json
import logging
import resource
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.fixtures.synthetic import block, dense, hops_tl, rel, series, text  # noqa: E402
from tests.synthetic.support import run_book  # noqa: E402

BOOKS: dict[str, tuple[object, str]] = {
    "rel20k": (lambda path: rel(path, 20_000), "rel20k.xlsx"),
    "hops100k": (lambda path: hops_tl(path, 100_000), "hops100k.xlsx"),
    "dense2m": (lambda path: dense(path, 10_000), "dense2m.xlsx"),
    "series10k": (lambda path: series(path, 10_000), "series10k.xlsx"),
    "text200k": (lambda path: text(path, 200_000), "text200k.xlsx"),
    "block500k": (lambda path: block(path, 500_000), "block500k.xlsx"),
}


class _Stages(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "stage_done":
            return
        self.rows.append(
            {
                "stage": getattr(record, "stage", None),
                "duration_ms": getattr(record, "duration_ms", None),
                "rss_peak_mb": getattr(record, "rss_peak_mb", None),
            }
        )


def measure(shape: str, dest: Path) -> dict[str, object]:
    build, filename = BOOKS[shape]
    dest.mkdir(parents=True, exist_ok=True)
    xlsx = dest / filename
    logger = logging.getLogger("finance_context")
    captured = _Stages()
    logger.setLevel(logging.INFO)
    logger.addHandler(captured)
    started = time.perf_counter()
    try:
        build(xlsx)
        run_book(xlsx, dest / "job", filename=filename, job_id=f"synthetic-{shape}")
    finally:
        logger.removeHandler(captured)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes. macOS reports bytes.
    rss_kb = usage if sys.platform != "darwin" else usage // 1024
    return {
        "shape": shape,
        "elapsed_ms": elapsed_ms,
        "ru_maxrss_kb": rss_kb,
        "stages": captured.rows,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: measure_job.py SHAPE DEST", file=sys.stderr)
        return 2
    payload = measure(argv[1], Path(argv[2]))
    print("RESULT " + json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
