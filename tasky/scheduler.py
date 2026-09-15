"""Advances each project's serial run queue by one task at a time.

`kick` is the only entry point. It is called by the enqueue endpoint, the
lane-resume endpoint, `supervise` after a worker exits, and the server's
periodic sync -- every place the design (design.md decision 2) lists as a
point where a queue might need to advance. It never raises: a launch failure
marks that task `failed` and pauses the lane instead, so one broken directory
does not spin forever or take the caller down with it.
"""

from __future__ import annotations

import subprocess
import uuid

from tasky import worker
from tasky.config import Config
from tasky.store import Store


def kick(
    store: Store, config: Config, cwd: str | None = None, popen=subprocess.Popen
) -> list[dict]:
    """Start the head of every idle, unpaused run queue (or just `cwd`'s).

    The check-and-claim for each directory is one atomic transaction
    (`Store.claim_serial_head`), so concurrent kicks racing on the same idle
    queue start exactly one task.
    """
    started = []
    cwds = [cwd] if cwd is not None else store.serial_lane_cwds()
    for target_cwd in cwds:
        if not target_cwd:
            continue
        session_id = str(uuid.uuid4())
        claimed = store.claim_serial_head(target_cwd, session_id)
        if claimed is None:
            continue
        try:
            task = worker.run_task(store, config, claimed["id"], mode="serial", popen=popen)
        except worker.WorkerError as exc:
            store.set_lane(target_cwd, paused=True, reason=str(exc))
            continue
        started.append(task)
    return started
