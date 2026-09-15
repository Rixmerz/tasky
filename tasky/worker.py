"""Spawn a queued task as a detached, supervised headless Claude Code process.

`run_task` never launches `claude` directly. It claims the task atomically
(so two concurrent `run` requests cannot both start a worker, CWE-367) and
launches `tasky supervise` (see `tasky.supervise`) instead, which owns the
actual child process, its stdio, and reporting how it ended. The prompt body
is written to a private file and read back by the supervisor as the child's
stdin, so it never appears in argv where a leading `-` could be parsed as an
option (CWE-88).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from tasky.config import Config
from tasky.store import Store

PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")

# Session-scoped variables a Claude Code turn exports. A detached worker that
# inherits them can be mistaken for a nested/child session of the caller, or
# hand the caller's messaging credentials to an unrelated process.
_SCRUB_ENV_VARS = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_EFFORT",
    }
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TASKY_BIN = _REPO_ROOT / "bin" / "tasky"


class WorkerError(Exception):
    """Raised when a task cannot be run as a worker."""


class TaskNotQueued(WorkerError):
    """Raised when the task was no longer queued at claim time."""


def _worker_env(task_id: int) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _SCRUB_ENV_VARS and key != "TASKY_TASK_ID"
    }
    env["TASKY_TASK_ID"] = str(task_id)
    return env


def run_task(
    store: Store,
    config: Config,
    task_id: int,
    permission_mode: str = "default",
    popen=subprocess.Popen,
) -> dict:
    task = store.get_task(task_id)
    if task is None:
        raise WorkerError(f"unknown task: {task_id}")
    if permission_mode not in PERMISSION_MODES:
        raise WorkerError(f"invalid permission mode: {permission_mode!r}")
    if permission_mode == "bypassPermissions" and not config.allow_bypass:
        raise WorkerError("bypassPermissions requires TASKY_ALLOW_BYPASS=1")
    cwd = task["cwd"]
    if not cwd or not Path(cwd).is_dir():
        raise WorkerError(f"invalid task cwd: {cwd!r}")

    session_id = str(uuid.uuid4())
    claimed = store.claim_task(task_id, status="running", session_id=session_id)
    if claimed is None:
        raise TaskNotQueued(f"task {task_id} is not queued")

    # Everything from here on can fail after the claim already committed --
    # disk full, `logs` replaced by a file, the session upsert racing another
    # writer -- and a task left `running` with nothing supervising it would
    # sit stuck forever. One try covers it all: on failure the task is marked
    # `failed` with the error and any prompt file written so far is removed.
    prompt_path = config.log_dir / f"task-{task_id}.prompt"
    try:
        # Registered after the claim so a lost race leaves no orphan session,
        # and before the launch so the child's first hook finds source="worker".
        store.upsert_session(session_id, cwd=cwd, source="worker")

        config.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_path = config.log_dir / f"task-{task_id}.log"

        fd = os.open(prompt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(prompt_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(task["body"])

        cmd = [
            sys.executable,
            str(_TASKY_BIN),
            "supervise",
            str(task_id),
            session_id,
            str(prompt_path),
            str(log_path),
            "--",
            config.claude_bin,
            "-p",
            "--session-id",
            session_id,
            "--permission-mode",
            permission_mode,
        ]
        env = _worker_env(task_id)

        popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, sqlite3.Error) as exc:
        store.update_task(task_id, status="failed", result=str(exc))
        prompt_path.unlink(missing_ok=True)
        raise WorkerError(f"failed to start worker: {exc}") from exc

    return claimed
