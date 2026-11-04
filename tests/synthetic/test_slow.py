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
when that formula is above 1536. ``block500k`` build (1028 → 1600) is clipped
to 1536. ``dense2m`` build (675 → 1100) stays under the clip.

``text200k`` layout took 45s. Rows whose values are ``n, n+1, n+2`` inside
1900–2099 are still bare-year headers, and layout still walks those bands.
An ASCII integer outside that range returns before the calendar matcher, and
a row shorter than a relative run returns before that scan. ``dense2m``
layout peaked at 563 MiB. ``block500k`` is one 500,000-row block. Its build
measured 1028 MiB and its render 520, so both ceilings stay at or under the
1536 MiB product budget. Layout of that book peaked at 877 MiB and is not
part of the budget.

Measured ``duration_ms`` / ``rss_peak_mb``:

- rel20k (112s, ru_maxrss 608 MiB): parse 19013/245, compile 25312/323,
  layout 24115/608, relations 4234/351, graph 21253/605, build 17229/517,
  render 41/325.
- hops100k (58s, ru_maxrss 570 MiB): parse 5200/227, compile 6569/280,
  layout 11291/371, relations 1959/422, graph 5636/504, build 26035/570,
  render 33/438.
- series10k (38s, ru_maxrss 435 MiB): parse 4072/217, compile 11610/337,
  layout 4692/347, relations 1087/356, graph 8912/429, build 6527/385,
  render 16/346.
- dense2m (140s, ru_maxrss 680 MiB): parse 26119/244, compile 15596/257,
  layout 69220/563, relations 47/283, graph 4436/323, build 23093/675,
  render 64/277.
- text200k (155s, ru_maxrss 692 MiB): parse 16315/371, compile 9040/302,
  layout 45122/608, relations 443/502, graph 2669/474, build 76087/685,
  render 154/458.
- block500k (248s, ru_maxrss 1030 MiB): parse 29831/296, compile 15275/258,
  layout 66263/877, relations 1140/712, graph 4955/598, build 126866/1028,
  render 124/520. One block, streamed rows, cells read per block.
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
        "parse": (58_000, 450),
        "compile": (76_000, 550),
        "layout": (73_000, 1000),
        "relations": (20_000, 600),
        "graph": (64_000, 1000),
        "build": (52_000, 850),
        "render": (16_000, 550),
    },
    "hops100k": {
        "parse": (21_000, 400),
        "compile": (22_000, 500),
        "layout": (34_000, 650),
        "relations": (17_000, 700),
        "graph": (21_000, 850),
        "build": (79_000, 950),
        "render": (16_000, 750),
    },
    "series10k": {
        "parse": (20_000, 400),
        "compile": (35_000, 600),
        "layout": (20_000, 600),
        "relations": (17_000, 600),
        "graph": (27_000, 700),
        "build": (22_000, 650),
        "render": (16_000, 600),
    },
    "dense2m": {
        "parse": (79_000, 450),
        "compile": (47_000, 450),
        "layout": (208_000, 900),
        "relations": (16_000, 500),
        "graph": (20_000, 550),
        "build": (70_000, 1100),
        "render": (16_000, 500),
    },
    "text200k": {
        "parse": (49_000, 650),
        "compile": (28_000, 550),
        "layout": (136_000, 1000),
        "relations": (16_000, 850),
        "graph": (18_000, 800),
        "build": (229_000, 1100),
        "render": (16_000, 750),
    },
    # Build 1028 clips 1600 to 1536. Render 520 stays inside that budget.
    "block500k": {
        "parse": (90_000, 500),
        "compile": (46_000, 450),
        "layout": (199_000, 1400),
        "relations": (17_000, 1150),
        "graph": (20_000, 950),
        "build": (381_000, 1536),
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
