"""Moves a `running` task with no sign of life to Needs attention.

A worker's supervisor (`tasky.supervise`) reports a terminal status the moment
its child exits, and an interactive session's `SessionEnd` or resumed
`SessionStart` interrupts whatever it left `running` (`tasky.hooks`). Both
rely on an event firing. Neither fires when the machine itself goes away
mid-turn -- a crash, a hard reset, a laptop that never comes back from sleep:
the task then stays `running` forever, since nothing is left to report it.

`sweep` is the fallback: it looks for evidence, already recorded for other
reasons, that a `running` task's session is still doing something --
`session.last_seen_at` (bumped on every prompt), the session's transcript
file's mtime (Claude Code appends to it on every message, hook or not) and
the worker's log file's mtime (`tasky.supervise` writes the child's stdout as
it runs) -- and only calls a task stale once *all* of that evidence is older
than the configured threshold. A single long tool call with no evidence
newer than the threshold looks the same as a dead process by this signal;
`TASKY_STALE_RUNNING_HOURS` is the knob to make that call for a given
machine, and `0` disables the sweep entirely.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from tasky.config import Config
from tasky.store import Store, _parse_iso

_NOTE_PREFIX = "[Tasky]"


def _mtime(path: str | Path | None) -> float | None:
    if not path:
        return None
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _last_sign_of_life(task: dict, session: dict | None, config: Config) -> float:
    """The newest timestamp of anything that shows this task's work is still moving.

    Falls back to `task["started_at"]` (always set for a `running` task) when
    nothing else is known, so a task with no session and no log is judged by
    its own age.
    """
    candidates = [_parse_iso(task.get("started_at") or "")]
    if session is not None:
        candidates.append(_parse_iso(session.get("last_seen_at") or ""))
        candidates.append(_mtime(session.get("transcript_path")))
    candidates.append(_mtime(config.log_dir / f"task-{task['id']}.log"))
    known = [c for c in candidates if c is not None]
    return max(known) if known else 0.0


def _format_span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        n, unit = days, "day"
    elif hours:
        n, unit = hours, "hour"
    elif minutes:
        n, unit = minutes, "minute"
    else:
        return "under a minute"
    return f"{n} {unit}" + ("" if n == 1 else "s")


def _reason(elapsed_seconds: float) -> str:
    return f"no activity for {_format_span(elapsed_seconds)}; its session or worker is gone"


def sweep(store: Store, config: Config, *, now: float | None = None) -> list[dict]:
    """Move every `running` task with no sign of life past the threshold to Needs attention.

    Idempotent and safe to call often: a task it moves becomes `interrupted`
    (terminal), so a second call finds nothing left to do. Never touches a
    task that is already terminal, and never deletes anything. A swept
    task's own `result` (a parent task reopened for a delegation keeps its
    earlier reply, see `tasky.hooks._handle_notifications`) is kept and the
    reason is appended, not overwritten. A swept `serial` task pauses its
    run queue, mirroring what `tasky.supervise._finish` does for a worker
    that reports `interrupted` on its own, so the queue does not silently
    start the next task behind a worker that vanished.
    """
    if config.stale_running_hours <= 0:
        return []
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    threshold_s = config.stale_running_hours * 3600

    sessions: dict[str, dict | None] = {}
    moved: list[dict] = []
    touched_sessions: set[str] = set()

    for task in store.list_tasks(status="running", include_hidden=True):
        session_id = task.get("session_id")
        if session_id is not None and session_id not in sessions:
            sessions[session_id] = store.get_session(session_id)
        session = sessions.get(session_id) if session_id else None

        session_ended = bool(session and session["state"] == "ended")
        last_life = _last_sign_of_life(task, session, config)
        elapsed = now - last_life
        if not session_ended and elapsed <= threshold_s:
            continue

        reason = _reason(elapsed)
        note = f"{_NOTE_PREFIX} {reason}"
        existing = task.get("result") or ""
        result = f"{existing}\n\n{note}" if existing else note
        store.update_task(task["id"], status="interrupted", result=result)

        if task.get("run_mode") == "serial" and task.get("cwd"):
            store.set_lane(
                task["cwd"], paused=True, reason=f"task #{task['id']} interrupted: {reason}"
            )

        if session_id:
            touched_sessions.add(session_id)
        moved.append(store.get_task(task["id"]))

    for session_id in touched_sessions:
        session = sessions.get(session_id)
        if session is None or session["state"] == "ended":
            continue
        if store.list_tasks(status="running", session_id=session_id, include_hidden=True):
            continue  # a task of this session is still within threshold: leave it alone
        store.update_session(session_id, state="ended")

    return moved
