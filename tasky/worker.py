"""Spawn a queued task as a detached, supervised headless Claude Code process.

`run_task` never launches `claude` directly. It claims the task atomically
(so two concurrent `run` requests cannot both start a worker, CWE-367) and
launches `tasky supervise` (see `tasky.supervise`) instead, which owns the
actual child process, its stdio, and reporting how it ended. The prompt body
is written to a private file and read back by the supervisor as the child's
stdin, so it never appears in argv where a leading `-` could be parsed as an
option (CWE-88).

Three start modes (design.md decision 3):
  - "now": a fresh worker, claimed straight from `queued`.
  - "fork": a worker that clones an existing session's conversation with
    `--resume <source> --fork-session`, claimed straight from `queued`.
  - "serial": the scheduler's mode. `Store.claim_serial_head` has already done
    the atomic queued-to-running claim for the project's run queue; this just
    launches the process for the task it claimed.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from tasky.config import Config
from tasky.store import Store

PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
RUN_MODES = ("now", "fork", "serial")

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

# A Claude Code session id is a canonical UUID; a fork source has to match
# this before it is ever handed to `--resume` (CWE-88).
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


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


def _resolve_fork_source(store: Store, task: dict) -> dict:
    """The session mode "fork" should clone: the task's own session, else the
    most recently active resumable session in the task's directory, preferring
    sessions the user drove over headless workers.

    Raises `WorkerError` before any claim happens if nothing valid is found,
    per design.md decision 3 and the parallel-workers spec's "Nothing to
    clone" / "Invalid source session id" scenarios.
    """
    cwd = task["cwd"]
    source = None
    if task["session_id"]:
        source = store.get_session(task["session_id"])
    if source is None and cwd:
        source = store.latest_session_for_cwd(cwd)
    if (
        source is None
        or not _UUID_RE.match(source["id"] or "")
        or not source["cwd"]
        or not Path(source["cwd"]).is_dir()
        or not source["transcript_path"]
        or not Path(source["transcript_path"]).is_file()
    ):
        raise WorkerError(f"no session in {cwd} to clone")
    return source


def _launch(
    store: Store,
    config: Config,
    task_id: int,
    session_id: str,
    cwd: str,
    permission_mode: str,
    extra_args: list[str],
    popen,
) -> None:
    task = store.get_task(task_id)
    assert task is not None

    # Registered after the claim so a lost race leaves no orphan session, and
    # before the launch so the child's first hook finds source="worker".
    store.upsert_session(session_id, cwd=cwd, source="worker")

    config.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    prompt_path = config.log_dir / f"task-{task_id}.prompt"
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
        *extra_args,
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


def run_task(
    store: Store,
    config: Config,
    task_id: int,
    permission_mode: str = "default",
    mode: str = "now",
    popen=subprocess.Popen,
    extra_args: list[str] | None = None,
) -> dict:
    """Claim a queued task and launch it; ``extra_args`` go to ``claude -p`` (e.g. a model)."""
    if mode not in RUN_MODES:
        raise WorkerError(f"invalid run mode: {mode!r}")
    task = store.get_task(task_id)
    if task is None:
        raise WorkerError(f"unknown task: {task_id}")

    if mode == "serial":
        # The scheduler already claimed the head of the run queue atomically
        # via Store.claim_serial_head; this call only launches the process.
        if task["status"] != "running" or task["run_mode"] != "serial" or not task["session_id"]:
            raise WorkerError(f"task {task_id} was not claimed for its run queue")
        session_id = task["session_id"]
        permission_mode = task["permission_mode"] or "default"
        if permission_mode not in PERMISSION_MODES or (
            permission_mode == "bypassPermissions" and not config.allow_bypass
        ):
            reason = f"permission mode not allowed at launch: {permission_mode!r}"
            store.update_task(task_id, status="failed", result=reason)
            raise WorkerError(reason)
        cwd = task["cwd"]
        if not cwd or not Path(cwd).is_dir():
            store.update_task(task_id, status="failed", result=f"invalid task cwd: {cwd!r}")
            raise WorkerError(f"invalid task cwd: {cwd!r}")
        claimed = task
        launch_cwd = cwd
    else:
        if permission_mode not in PERMISSION_MODES:
            raise WorkerError(f"invalid permission mode: {permission_mode!r}")
        if permission_mode == "bypassPermissions" and not config.allow_bypass:
            raise WorkerError("bypassPermissions requires TASKY_ALLOW_BYPASS=1")
        cwd = task["cwd"]
        if not cwd or not Path(cwd).is_dir():
            raise WorkerError(f"invalid task cwd: {cwd!r}")

        session_id = str(uuid.uuid4())
        launch_cwd = cwd
        claim_fields: dict[str, object] = {
            "status": "running",
            "session_id": session_id,
            "run_mode": mode,
            "permission_mode": permission_mode,
        }
        if mode == "fork":
            source = _resolve_fork_source(store, task)
            claim_fields["fork_of"] = source["id"]
            launch_cwd = source["cwd"]
            extra_args = ["--resume", source["id"], "--fork-session", *(extra_args or [])]

        # The queued check happens inside this atomic claim, not before it: a
        # check-then-act split here is exactly the race two concurrent /run
        # calls would win together (CWE-367).
        claimed = store.claim_task(task_id, expected_status="queued", **claim_fields)
        if claimed is None:
            raise TaskNotQueued(f"task {task_id} is not queued")

    # Everything from here on can fail after the claim already committed --
    # disk full, `logs` replaced by a file, the session upsert racing another
    # writer -- and a task left `running` with nothing supervising it would
    # sit stuck forever. One try covers it all: on failure the task is marked
    # `failed` with the error and any prompt file written so far is removed.
    prompt_path = config.log_dir / f"task-{task_id}.prompt"
    try:
        _launch(
            store, config, task_id, session_id, launch_cwd, permission_mode, extra_args or [], popen
        )
    except (OSError, sqlite3.Error) as exc:
        store.update_task(task_id, status="failed", result=str(exc))
        prompt_path.unlink(missing_ok=True)
        raise WorkerError(f"failed to start worker: {exc}") from exc

    return claimed
