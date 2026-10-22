from __future__ import annotations

import logging
from pathlib import Path

from finance_context.jobqueue.pool import _log_queue_config
from finance_context.memory import (
    admission_blocked,
    cgroup_memory_anon_bytes,
    cgroup_oom_kill_count,
)
from finance_context.observability import JsonFormatter
from finance_context.settings import Settings


def test_admission_needs_limit_anon_and_a_budget() -> None:
    assert admission_blocked(
        enabled=True, limit_bytes=1000, anon_bytes=900, budget_bytes=200
    )
    assert not admission_blocked(
        enabled=True, limit_bytes=1000, anon_bytes=100, budget_bytes=200
    )
    assert not admission_blocked(
        enabled=False, limit_bytes=1000, anon_bytes=900, budget_bytes=200
    )
    assert not admission_blocked(
        enabled=True, limit_bytes=None, anon_bytes=900, budget_bytes=200
    )
    assert not admission_blocked(
        enabled=True, limit_bytes=1000, anon_bytes=None, budget_bytes=200
    )


def test_cgroup_anon_and_oom_kill_come_from_the_leaf(tmp_path: Path) -> None:
    leaf = tmp_path / "sys" / "pod"
    leaf.mkdir(parents=True)
    (leaf / "memory.stat").write_text("anon 123\nfile 4\n", encoding="utf-8")
    (leaf / "memory.events").write_text("oom_kill 7\n", encoding="utf-8")
    proc = tmp_path / "cgroup"
    proc.write_text("0::/pod\n", encoding="utf-8")
    sysfs = tmp_path / "sys"
    assert cgroup_memory_anon_bytes(proc, sysfs) == 123
    assert cgroup_oom_kill_count(proc, sysfs) == 7


def test_queue_config_omits_the_password(caplog) -> None:
    settings = Settings(
        _env_file=None,
        redis_url="redis://:s3cret@127.0.0.1:6390/0",
    )
    caplog.set_level(logging.INFO, logger="finance_context.jobqueue")
    _log_queue_config(settings)
    rows = [record for record in caplog.records if record.event == "queue_config"]
    assert len(rows) == 1
    text = JsonFormatter().format(rows[0])
    assert "s3cret" not in text
    assert "127.0.0.1" in text
    assert rows[0].queue == "finance-context"
    assert rows[0].slots == 2
    assert rows[0].timeout_sec == 3730
