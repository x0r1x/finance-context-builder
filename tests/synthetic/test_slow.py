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
a build that crosses the product memory budget fails. The clip applies only
when that formula is above 1536. ``dense2m`` (976 → 1550) is clipped to 1536.
``hops100k`` (575 → 950), ``text200k`` (714 → 1150) and the unclipped part of
``block500k`` (1027 → 1600, then clipped to 1536) stay on that formula.

``text200k`` layout took 48s. Rows whose values are ``n, n+1, n+2`` inside
1900–2099 are still bare-year headers, and layout still walks those bands.
A plain integer outside that window is not matched, and a long label is not
scanned as a date. ``dense2m`` layout peaked at 1050 MiB; 1650 still fails
that peak, and its build ceiling is the 1536 budget. ``block500k`` is one
500,000-row block. Its build measured 1027 MiB and its render 525, so both
ceilings stay at or under the 1536 MiB product budget. Layout of that book
peaked at 1168 MiB and is not part of the budget.

Measured ``duration_ms`` / ``rss_peak_mb``:

- rel20k (123s, ru_maxrss 884 MiB): parse 20191/249, compile 25179/322,
  layout 30165/884, relations 4031/390, graph 21618/626, build 17924/856,
  render 3237/748.
- hops100k (62s, ru_maxrss 575 MiB): parse 5453/227, compile 7228/275,
  layout 12350/413, relations 1855/426, graph 6464/504, build 27224/575,
  render 37/445. Remeasured after address reads stayed direct slot reads.
- series10k (41s, ru_maxrss 514 MiB): parse 4157/215, compile 12172/338,
  layout 5966/410, relations 1301/368, graph 9082/440, build 6625/514,
  render 715/450. Remeasured after every series cache stayed a finite float.
- dense2m (152s, ru_maxrss 1050 MiB): parse 27376/244, compile 15629/256,
  layout 73487/1050, relations 46/324, graph 4421/363, build 24137/976,
  render 5031/699. Remeasured after row models became slots dataclasses.
- text200k (158s, ru_maxrss 757 MiB): parse 16589/371, compile 9035/293,
  layout 47604/757, relations 711/490, graph 2895/482, build 76235/714,
  render 158/456. Remeasured after integers outside 1900–2099 skipped the
  year matcher.
- block500k (259s, ru_maxrss 1168 MiB): parse 31224/294, compile 15122/255,
  layout 67927/1168, relations 910/734, graph 5188/615, build 133369/1027,
  render 112/525. One block, streamed rows, cells read per block.
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
        "compile": (22_000, 500),
        "layout": (38_000, 700),
        "relations": (17_000, 700),
        "graph": (21_000, 800),
        "build": (80_000, 950),
        "render": (16_000, 750),
    },
    "series10k": {
        "parse": (20_000, 400),
        "compile": (37_000, 600),
        "layout": (21_000, 700),
        "relations": (17_000, 650),
        "graph": (28_000, 750),
        "build": (22_000, 850),
        "render": (16_000, 750),
    },
    "dense2m": {
        "parse": (83_000, 450),
        "compile": (47_000, 450),
        "layout": (221_000, 1650),
        "relations": (16_000, 550),
        "graph": (20_000, 600),
        "build": (73_000, 1536),
        "render": (21_000, 1100),
    },
    "text200k": {
        "parse": (51_000, 650),
        "compile": (27_000, 550),
        "layout": (143_000, 1200),
        "relations": (16_000, 850),
        "graph": (18_000, 800),
        "build": (229_000, 1150),
        "render": (16_000, 750),
    },
    # Build 1027 clips 1600 to 1536. Render 525 stays inside that budget.
    "block500k": {
        "parse": (93_000, 500),
        "compile": (45_000, 450),
        "layout": (204_000, 1850),
        "relations": (17_000, 1200),
        "graph": (21_000, 1000),
        "build": (401_000, 1536),
        "render": (16_000, 850),
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
