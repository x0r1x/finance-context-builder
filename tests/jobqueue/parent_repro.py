"""Child process for the rq#2495 test. Not a pytest module."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from finance_context.jobqueue.parents import HORSE_ENV, arm_child_death, arm_horse_death


def main() -> None:
    ready = Path(sys.argv[1])
    horse = os.fork()
    if horse == 0:
        os.setpgrp()
        os.environ[HORSE_ENV] = "1"
        # Fork the grandchild before either process starts a thread.
        child = os.fork()
        if child == 0:
            arm_child_death(0.05)
            time.sleep(30)
            os._exit(0)
        arm_horse_death(0.05)
        ready.write_text(
            json.dumps({"horse": os.getpid(), "child": child}),
            encoding="utf-8",
        )
        time.sleep(30)
        os._exit(0)
    time.sleep(30)


if __name__ == "__main__":
    main()
