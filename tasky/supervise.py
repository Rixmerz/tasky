"""Supervises a detached headless Claude Code worker and reports how it ended.

`tasky.worker.run_task` never waits for the `claude` process itself: it
launches `tasky supervise` detached and returns immediately. This module is
that supervisor. It owns the child's stdio (prompt file in, log file out),
waits for it, and -- if the task is still `running` under the session it
started -- writes a terminal status so a worker that dies silently does not
stay `running` forever.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from tasky import scheduler
from tasky.config import Config
from tasky.store import Store

_PAUSING_STATUSES = ("failed", "interrupted")


def _finish(config: Config, store: Store, task_id: int) -> None:
    """Pause the lane on a failed/interrupted queue task, then kick it either way.

    A task the Stop hook marked `done` leaves the lane running (design.md
    decision 2). Kick failures must never crash the supervisor: a broken
    directory pauses its own lane (see `scheduler.kick`), but any other
    failure here (e.g. the store itself) is swallowed rather than taking the
    worker's exit reporting down with it.
    """
    task = store.get_task(task_id)
    if task is None or not task.get("cwd"):
        return
    if task.get("run_mode") == "serial" and task["status"] in _PAUSING_STATUSES:
        store.set_lane(task["cwd"], paused=True, reason=f"task #{task_id} {task['status']}")
    # A broken directory must not crash the supervisor; scheduler.kick already
    # pauses the lane itself on a launch failure (WorkerError), so anything
    # that reaches here is a store-level surprise not worth losing the
    # worker's own exit reporting over.
    with contextlib.suppress(Exception):  # noqa: BLE001, S110
        scheduler.kick(store, config, task["cwd"])


def supervise(
    config: Config,
    task_id: int,
    session_id: str,
    prompt_path: str,
    log_path: str,
    argv: list[str],
    popen=subprocess.Popen,
) -> int:
    prompt_path = Path(prompt_path)
    log_path = Path(log_path)

    try:
        try:
            with open(prompt_path, "rb") as stdin_f:
                log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(log_fd, "ab") as log_f:
                    proc = popen(argv, stdin=stdin_f, stdout=log_f, stderr=subprocess.STDOUT)
                    code = proc.wait()
        except OSError as exc:
            with Store.open(config) as store:
                task = store.get_task(task_id)
                # Same guard as the exit path below: only touch the task if
                # it's still the one this supervisor is responsible for. A
                # cancelled or deleted task must be left alone, not resurrected
                # as `failed`.
                if (
                    task is not None
                    and task["status"] == "running"
                    and task["session_id"] == session_id
                ):
                    store.update_task(task_id, status="failed", result=str(exc))
                _finish(config, store, task_id)
            return 127
    finally:
        prompt_path.unlink(missing_ok=True)

    with Store.open(config) as store:
        task = store.get_task(task_id)
        if task is not None and task["status"] == "running" and task["session_id"] == session_id:
            if code != 0:
                store.update_task(
                    task_id,
                    status="failed",
                    result=(
                        f"worker exited with code {code} before reporting a result; "
                        f"see {log_path}"
                    ),
                )
            else:
                store.update_task(
                    task_id,
                    status="interrupted",
                    result=f"worker exited without reporting a result; see {log_path}",
                )
        _finish(config, store, task_id)
    return code
