from __future__ import annotations

import threading

from finance_context.adapters.memory_bus import JobProgress, MemoryJobBus


def test_stale_generation_cannot_overwrite_a_new_run() -> None:
    bus = MemoryJobBus()
    assert bus.enqueue("job")
    first = bus.current_generation("job")
    bus.set_progress("job", "parse", generation=first)
    assert bus.expire("job", first)
    assert bus.enqueue("job")
    second = bus.current_generation("job")
    assert second == first + 1
    bus.set_terminal("job", "failed", stage="failed", error="TimeoutError", generation=first)
    bus.set_progress("job", "parse", generation=first)
    rec = bus.get("job")
    assert rec is not None
    assert rec.status == "queued"
    assert rec.stage == "queued"
    assert rec.error is None
    assert rec.generation == 2


def test_abandon_records_the_given_error() -> None:
    bus = MemoryJobBus()
    assert bus.enqueue("job")
    generation = bus.current_generation("job")
    assert bus.abandon("job", generation, "process_lost")
    rec = bus.get("job")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.error == "process_lost"
    assert bus.enqueue("job")
    assert bus.current_generation("job") == generation + 1


def test_progress_updates_stage_until_expire() -> None:
    bus = MemoryJobBus()
    assert bus.enqueue("job")
    generation = bus.current_generation("job")
    bus.set_progress("job", "parse", generation=generation)
    progress = JobProgress(bus, "job", generation)
    progress.progress("mapping")
    rec = bus.get("job")
    assert rec is not None
    assert rec.stage == "mapping"
    assert rec.status == "running"
    assert bus.expire("job", generation)
    progress.progress("build")
    assert progress.cancelled()
    rec = bus.get("job")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.stage == "failed"


def test_live_map_lock_serializes_thread_updates() -> None:
    bus = MemoryJobBus()
    assert bus.enqueue("job")
    generation = bus.current_generation("job")
    errors: list[BaseException] = []

    def bump(stage: str) -> None:
        try:
            for _ in range(50):
                bus.set_progress("job", stage, generation=generation)
                bus.get("job")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=bump, args=(stage,)) for stage in ("compile", "layout")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    rec = bus.get("job")
    assert rec is not None
    assert rec.stage in {"compile", "layout"}
    assert rec.status == "running"
