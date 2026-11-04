"""Stage time and RSS ceilings for the large synthetic books.

Each book runs in a fresh interpreter with ``MALLOC_ARENA_MAX=2``, the same
knob the job child uses, so the RSS is the pipeline's and not the test
runner's. Numbers below were measured that way on 2026-11-04. A ceiling is
three times the measured duration (at least fifteen seconds more, and at
least fifteen seconds), rounded up to a second. ``rel20k`` and ``hops100k``
layout stay under 180s: the parent walk those books used to do took about
494s, and ``hops100k`` had not finished a multi-hour run.

RSS headroom is ``1.5 × measured + 50 MiB``, rounded up to 50 MiB. When a
build was measured at or under 1200 MiB, that ceiling is clipped to 1536 so
a build that crosses the product memory budget fails. ``hops100k`` build
(1885) and ``text200k`` build (3251) and render (2238) are already over
1536; their ceilings sit above the measurement and are not a product limit.

``text200k`` layout took 789s. Rows whose values are ``n, n+1, n+2`` inside
1900–2099 are bare-year headers, and layout scans the sheet once per such
header. The ceiling is three times that cost. ``dense2m`` layout peaked at
1066 MiB (the earlier run was about 2184); 1650 still fails that peak, and
its build ceiling is the 1536 budget.

Measured ``duration_ms`` / ``rss_peak_mb``:

- rel20k (123s, ru_maxrss 884 MiB): parse 20191/249, compile 25179/322,
  layout 30165/884, relations 4031/390, graph 21618/626, build 17924/856,
  render 3237/748.
- hops100k (81s, ru_maxrss 2169 MiB): parse 5776/227, compile 7337/276,
  layout 22967/532, relations 2265/518, graph 6308/598, build 33436/1885,
  render 1428/1301.
- series10k (63s, ru_maxrss 712 MiB): parse 5449/327, compile 13932/398,
  layout 13514/523, relations 1185/461, graph 10637/544, build 14386/704,
  render 1213/628.
- dense2m (152s, ru_maxrss 1066 MiB): parse 28252/243, compile 16040/255,
  layout 75415/1066, relations 52/336, graph 4607/370, build 21245/1044,
  render 5066/788.
- text200k (984s, ru_maxrss 3933 MiB): parse 16715/371, compile 9016/290,
  layout 789109/1054, relations 373/723, graph 2640/703, build 157237/3251,
  render 4000/2238.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# stage -> (duration_ms ceiling, rss_peak_mb ceiling)
_CEILINGS: dict[str, dict[str, tuple[int, int]]] = {
    "rel20k": {
        "parse": (61_000, 450),
        "compile": (76_000, 550),
        "layout": (91_000, 1400),
        "relations": (20_000, 650),
        "graph": (65_000, 1000),
        "build": (54_000, 1350),
        "render": (19_000, 1200),
    },
    "hops100k": {
        "parse": (21_000, 400),
        "compile": (23_000, 500),
        "layout": (69_000, 850),
        "relations": (18_000, 850),
        "graph": (22_000, 950),
        "build": (101_000, 2900),
        "render": (17_000, 2050),
    },
    "series10k": {
        "parse": (21_000, 550),
        "compile": (42_000, 650),
        "layout": (41_000, 850),
        "relations": (17_000, 750),
        "graph": (32_000, 900),
        "build": (44_000, 1150),
        "render": (17_000, 1000),
    },
    "dense2m": {
        "parse": (85_000, 450),
        "compile": (49_000, 450),
        "layout": (227_000, 1650),
        "relations": (16_000, 600),
        "graph": (20_000, 650),
        "build": (64_000, 1536),
        "render": (21_000, 1250),
    },
    "text200k": {
        "parse": (51_000, 650),
        "compile": (28_000, 500),
        "layout": (2_368_000, 1650),
        "relations": (16_000, 1150),
        "graph": (18_000, 1150),
        "build": (472_000, 4950),
        "render": (19_000, 3450),
    },
}


@pytest.mark.slow
@pytest.mark.parametrize("shape", list(_CEILINGS))
def test_stage_time_and_memory_stay_under_ceiling(shape: str, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["MALLOC_ARENA_MAX"] = "2"
    script = Path(__file__).resolve().parent / "measure_job.py"
    proc = subprocess.run(
        [sys.executable, str(script), shape, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=3600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(item for item in proc.stdout.splitlines() if item.startswith("RESULT "))
    payload = json.loads(line.removeprefix("RESULT "))
    seen = {row["stage"]: row for row in payload["stages"]}
    expected = _CEILINGS[shape]
    assert set(seen) == set(expected), sorted(seen)
    for stage, (limit_ms, limit_mb) in expected.items():
        row = seen[stage]
        assert row["duration_ms"] <= limit_ms, (shape, stage, row, limit_ms)
        assert row["rss_peak_mb"] <= limit_mb, (shape, stage, row, limit_mb)
