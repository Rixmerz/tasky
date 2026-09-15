"""tasky/scheduler.py: advancing each project's serial run queue."""

from __future__ import annotations

import threading

from tasky import scheduler
from tasky.store import Store


class FakePopen:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.lock = threading.Lock()

    def __call__(self, args, **kwargs):
        with self.lock:
            self.calls.append((args, kwargs))
        return object()


def _queue_task(store, cwd, body="do it"):
    task = store.create_task(kind="prompt", body=body, status="queued", source="cli", cwd=cwd)
    return store.enqueue_task(task["id"], "default")


def test_kick_starts_the_head_only(store, config, tmp_path):
    cwd = str(tmp_path)
    first = _queue_task(store, cwd, "first")
    second = _queue_task(store, cwd, "second")
    fake = FakePopen()

    started = scheduler.kick(store, config, cwd, popen=fake)

    assert [t["id"] for t in started] == [first["id"]]
    assert store.get_task(first["id"])["status"] == "running"
    assert store.get_task(second["id"])["status"] == "queued"
    assert len(fake.calls) == 1


def test_kick_next_task_starts_after_the_previous_one_finishes(store, config, tmp_path):
    cwd = str(tmp_path)
    first = _queue_task(store, cwd, "first")
    second = _queue_task(store, cwd, "second")
    fake = FakePopen()

    scheduler.kick(store, config, cwd, popen=fake)
    store.update_task(first["id"], status="done", result="ok")

    started = scheduler.kick(store, config, cwd, popen=fake)

    assert [t["id"] for t in started] == [second["id"]]
    assert store.get_task(second["id"])["status"] == "running"


def test_kick_does_nothing_while_one_is_already_running(store, config, tmp_path):
    cwd = str(tmp_path)
    _queue_task(store, cwd, "first")
    second = _queue_task(store, cwd, "second")
    fake = FakePopen()

    scheduler.kick(store, config, cwd, popen=fake)
    started_again = scheduler.kick(store, config, cwd, popen=fake)

    assert started_again == []
    assert store.get_task(second["id"])["status"] == "queued"
    assert len(fake.calls) == 1


def test_kick_pauses_the_lane_on_launch_failure(store, config, tmp_path):
    cwd = str(tmp_path)
    _queue_task(store, cwd, "first")

    def raising_popen(*a, **k):
        raise OSError("no such file")

    started = scheduler.kick(store, config, cwd, popen=raising_popen)

    assert started == []
    lane = store.get_lane(cwd)
    assert lane["paused"] == 1


def test_kick_skips_a_paused_lane(store, config, tmp_path):
    cwd = str(tmp_path)
    task = _queue_task(store, cwd, "first")
    store.set_lane(cwd, paused=True, reason="x")
    fake = FakePopen()

    started = scheduler.kick(store, config, cwd, popen=fake)

    assert started == []
    assert store.get_task(task["id"])["status"] == "queued"


def test_kick_resumes_after_lane_unpaused(store, config, tmp_path):
    cwd = str(tmp_path)
    task = _queue_task(store, cwd, "first")
    store.set_lane(cwd, paused=True, reason="x")
    store.set_lane(cwd, paused=False)
    fake = FakePopen()

    started = scheduler.kick(store, config, cwd, popen=fake)

    assert [t["id"] for t in started] == [task["id"]]


def test_kick_projects_run_independently(store, config, tmp_path):
    cwd_a = str(tmp_path / "a")
    cwd_b = str(tmp_path / "b")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    task_a = _queue_task(store, cwd_a, "a")
    task_b = _queue_task(store, cwd_b, "b")
    fake = FakePopen()

    started = scheduler.kick(store, config, popen=fake)

    started_ids = {t["id"] for t in started}
    assert started_ids == {task_a["id"], task_b["id"]}
    assert store.get_task(task_a["id"])["status"] == "running"
    assert store.get_task(task_b["id"])["status"] == "running"


def test_kick_with_explicit_cwd_only_touches_that_directory(store, config, tmp_path):
    cwd_a = str(tmp_path / "a")
    cwd_b = str(tmp_path / "b")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    task_a = _queue_task(store, cwd_a, "a")
    task_b = _queue_task(store, cwd_b, "b")
    fake = FakePopen()

    started = scheduler.kick(store, config, cwd_a, popen=fake)

    assert [t["id"] for t in started] == [task_a["id"]]
    assert store.get_task(task_b["id"])["status"] == "queued"


def test_kick_concurrent_calls_on_one_lane_start_exactly_one_task(config, tmp_path):
    """Several threads calling kick() on the same idle lane at once."""
    cwd = str(tmp_path)
    with Store.open(config) as setup_store:
        _queue_task(setup_store, cwd, "a")
        _queue_task(setup_store, cwd, "b")

    fake = FakePopen()
    results = []

    def attempt():
        with Store.open(config) as thread_store:
            results.append(scheduler.kick(thread_store, config, cwd, popen=fake))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    started = [task for batch in results for task in batch]
    assert len(started) == 1
    assert len(fake.calls) == 1
