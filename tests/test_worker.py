import stat
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from tasky.worker import PERMISSION_MODES, TaskNotQueued, WorkerError, run_task

_TASKY_BIN = Path(__file__).resolve().parent.parent / "bin" / "tasky"


class FakeProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


class FakePopen:
    """Records every call instead of spawning a real process."""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeProcess()


def _queued_task(store, config, tmp_path, body="do the thing"):
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    return store.create_task(kind="prompt", body=body, status="queued", source="ui", cwd=str(cwd))


def test_run_task_starts_supervisor_and_binds_session(store, config, tmp_path):
    task = _queued_task(store, config, tmp_path)
    fake = FakePopen()

    updated = run_task(store, config, task["id"], permission_mode="acceptEdits", popen=fake)

    assert updated["status"] == "running"
    assert updated["session_id"]
    uuid.UUID(updated["session_id"])  # a real uuid4 was generated

    assert len(fake.calls) == 1
    args, kwargs = fake.calls[0]
    assert args[0] == sys.executable
    assert args[1] == str(_TASKY_BIN)
    assert args[2] == "supervise"
    assert args[3] == str(task["id"])
    assert args[4] == updated["session_id"]
    prompt_path = Path(args[5])
    log_path = Path(args[6])
    assert args[7] == "--"
    assert args[8] == config.claude_bin
    assert args[9] == "-p"
    assert args[10] == "--session-id"
    assert args[11] == updated["session_id"]
    assert args[12] == "--permission-mode"
    assert args[13] == "acceptEdits"

    # the body is never handed over as an argv element (CWE-88)
    assert "do the thing" not in args

    assert kwargs["cwd"] == task["cwd"]
    assert kwargs["env"]["TASKY_TASK_ID"] == str(task["id"])
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True

    assert prompt_path.read_text(encoding="utf-8") == "do the thing"
    assert stat.S_IMODE(prompt_path.stat().st_mode) == 0o600
    assert log_path.parent == config.log_dir

    session = store.get_session(updated["session_id"])
    assert session is not None
    assert session["source"] == "worker"
    assert session["cwd"] == task["cwd"]


def test_run_task_scrubs_claude_code_session_vars(store, config, tmp_path, monkeypatch):
    task = _queued_task(store, config, tmp_path)
    for key, value in (
        ("CLAUDECODE", "1"),
        ("CLAUDE_CODE_CHILD_SESSION", "1"),
        ("CLAUDE_CODE_SESSION_ID", "parent-session"),
        ("CLAUDE_PID", "999"),
        ("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/sock"),
        ("CLAUDE_CODE_MESSAGING_TOKEN", "secret-token"),  # noqa: S105 - synthetic test value
        ("CLAUDE_CODE_SESSION_ATTENDED", "1"),
        ("CLAUDE_CODE_ENTRYPOINT", "cli"),
        ("CLAUDE_CODE_EXECPATH", "/usr/bin/claude"),
        ("TASKY_TASK_ID", "999"),
        ("SOME_OTHER_VAR", "keep-me"),
    ):
        monkeypatch.setenv(key, value)
    fake = FakePopen()

    run_task(store, config, task["id"], popen=fake)

    _, kwargs = fake.calls[0]
    env = kwargs["env"]
    for key in (
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
    ):
        assert key not in env
    assert env["TASKY_TASK_ID"] == str(task["id"])
    assert env["SOME_OTHER_VAR"] == "keep-me"


def test_run_task_rejects_unknown_task(store, config):
    fake = FakePopen()

    with pytest.raises(WorkerError):
        run_task(store, config, 999999, popen=fake)

    assert fake.calls == []


def test_run_task_rejects_non_queued_task(store, config, tmp_path):
    task = _queued_task(store, config, tmp_path)
    store.update_task(task["id"], status="running")
    fake = FakePopen()

    with pytest.raises(TaskNotQueued):
        run_task(store, config, task["id"], popen=fake)

    assert fake.calls == []
    assert store.get_task(task["id"])["status"] == "running"


def test_run_task_rejects_unknown_permission_mode(store, config, tmp_path):
    task = _queued_task(store, config, tmp_path)
    fake = FakePopen()

    with pytest.raises(WorkerError):
        run_task(store, config, task["id"], permission_mode="yolo", popen=fake)

    assert fake.calls == []
    assert store.get_task(task["id"])["status"] == "queued"


def test_run_task_rejects_bypass_permissions_without_allow_bypass(store, config, tmp_path):
    task = _queued_task(store, config, tmp_path)
    assert config.allow_bypass is False
    fake = FakePopen()

    with pytest.raises(WorkerError, match="TASKY_ALLOW_BYPASS=1"):
        run_task(store, config, task["id"], permission_mode="bypassPermissions", popen=fake)

    assert fake.calls == []
    assert store.get_task(task["id"])["status"] == "queued"


def test_run_task_accepts_bypass_permissions_with_allow_bypass(store, config, tmp_path):
    task = _queued_task(store, config, tmp_path)
    allowed_config = replace(config, allow_bypass=True)
    fake = FakePopen()

    updated = run_task(
        store, allowed_config, task["id"], permission_mode="bypassPermissions", popen=fake
    )

    assert updated["status"] == "running"
    assert len(fake.calls) == 1


def test_run_task_rejects_invalid_cwd(store, config, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path / "missing")
    )
    fake = FakePopen()

    with pytest.raises(WorkerError):
        run_task(store, config, task["id"], popen=fake)

    assert fake.calls == []
    assert store.get_task(task["id"])["status"] == "queued"


def test_all_permission_modes_accepted(store, config, tmp_path):
    allowed_config = replace(config, allow_bypass=True)
    for mode in PERMISSION_MODES:
        task = _queued_task(store, allowed_config, tmp_path, body=f"mode {mode}")
        fake = FakePopen()
        updated = run_task(store, allowed_config, task["id"], permission_mode=mode, popen=fake)
        assert updated["status"] == "running"


def test_run_task_missing_binary_marks_task_failed(store, config, tmp_path):
    task = _queued_task(store, config, tmp_path)
    bad_config = replace(config, claude_bin=str(tmp_path / "no-such-claude-binary"))

    def raising_popen(*a, **k):
        raise OSError("no such file")

    with pytest.raises(WorkerError):
        run_task(store, config=bad_config, task_id=task["id"], popen=raising_popen)

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert failed["result"] == "no such file"
    prompt_path = config.log_dir / f"task-{task['id']}.prompt"
    assert not prompt_path.exists()


def test_run_task_post_claim_oserror_marks_task_failed_and_removes_prompt(
    store, config, tmp_path, monkeypatch
):
    """An OSError after the claim (disk full, a bad `logs` dir, ...) must not
    leave the task `running` with nothing supervising it (L-new-1)."""
    task = _queued_task(store, config, tmp_path)

    def raising_mkdir(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", raising_mkdir)

    with pytest.raises(WorkerError):
        run_task(store, config, task["id"], popen=FakePopen())

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert "disk full" in failed["result"]
    prompt_path = config.log_dir / f"task-{task['id']}.prompt"
    assert not prompt_path.exists()


def test_run_task_post_claim_sqlite_error_marks_task_failed(store, config, tmp_path, monkeypatch):
    import sqlite3

    task = _queued_task(store, config, tmp_path)

    def raising_upsert(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "upsert_session", raising_upsert)

    with pytest.raises(WorkerError):
        run_task(store, config, task["id"], popen=FakePopen())

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert "database is locked" in failed["result"]


def test_run_task_concurrent_claims_exactly_once(config, tmp_path):
    """Eight racing callers, each with their own Store connection: one claim wins."""
    import threading

    from tasky.store import Store

    with Store.open(config) as setup_store:
        task = _queued_task(setup_store, config, tmp_path)

    fake = FakePopen()
    popen_lock = threading.Lock()

    def locking_popen(*args, **kwargs):
        with popen_lock:
            return fake(*args, **kwargs)

    results = []

    def attempt():
        with Store.open(config) as thread_store:
            try:
                results.append(run_task(thread_store, config, task["id"], popen=locking_popen))
            except TaskNotQueued:
                results.append(None)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r is not None]
    assert len(successes) == 1
    assert results.count(None) == 7
    assert len(fake.calls) == 1
