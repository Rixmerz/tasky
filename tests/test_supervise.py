import stat
import sys

from tasky.supervise import supervise


def _running_task(store, tmp_path, session_id="sess-1"):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    store.upsert_session(session_id, cwd=str(tmp_path), source="worker")
    store.claim_task(task["id"], status="running", session_id=session_id)
    return store.get_task(task["id"])


def _running_queue_task(store, tmp_path, session_id="sess-1"):
    """A task started from a project's run queue (`run_mode='serial'`)."""
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    store.enqueue_task(task["id"], "default")
    store.upsert_session(session_id, cwd=str(tmp_path), source="worker")
    store.claim_task(task["id"], status="running", session_id=session_id, run_mode="serial")
    return store.get_task(task["id"])


def _prompt_and_log(config, task_id, body="do the thing"):
    config.log_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = config.log_dir / f"task-{task_id}.prompt"
    prompt_path.write_text(body, encoding="utf-8")
    log_path = config.log_dir / f"task-{task_id}.log"
    return prompt_path, log_path


def _python_exit(code: int) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.exit({code})"]


def test_supervise_marks_failed_on_nonzero_exit(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(3)
    )

    assert code == 3
    updated = store.get_task(task["id"])
    assert updated["status"] == "failed"
    assert "code 3" in updated["result"]
    assert str(log_path) in updated["result"]
    assert not prompt_path.exists()


def test_supervise_marks_interrupted_on_clean_exit_without_result(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(0)
    )

    assert code == 0
    updated = store.get_task(task["id"])
    assert updated["status"] == "interrupted"
    assert "without reporting a result" in updated["result"]
    assert not prompt_path.exists()


def test_supervise_leaves_already_done_task_untouched(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    store.update_task(task["id"], status="done", result="finished on its own")
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(1)
    )

    assert code == 1
    unchanged = store.get_task(task["id"])
    assert unchanged["status"] == "done"
    assert unchanged["result"] == "finished on its own"


def test_supervise_leaves_task_reclaimed_by_another_session_untouched(store, config, tmp_path):
    task = _running_task(store, tmp_path, session_id="sess-1")
    prompt_path, log_path = _prompt_and_log(config, task["id"])
    # a later claim under a different session (e.g. task was re-queued and re-run)
    store.update_task(task["id"], status="queued")
    store.claim_task(task["id"], status="running", session_id="sess-2")

    code = supervise(
        config, task["id"], "sess-1", str(prompt_path), str(log_path), _python_exit(1)
    )

    assert code == 1
    current = store.get_task(task["id"])
    assert current["session_id"] == "sess-2"
    assert current["status"] == "running"


def test_supervise_writes_child_stdout_and_stderr_to_log(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])
    script = "import sys; print('out-line'); print('err-line', file=sys.stderr)"

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path),
        [sys.executable, "-c", script],
    )

    text = log_path.read_text(encoding="utf-8")
    assert "out-line" in text
    assert "err-line" in text


def test_supervise_log_file_is_private(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(0)
    )

    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_supervise_passes_prompt_file_as_child_stdin(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"], body="read me back")
    script = "import sys; sys.stdout.write(sys.stdin.read())"

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path),
        [sys.executable, "-c", script],
    )

    assert log_path.read_text(encoding="utf-8") == "read me back"


def test_supervise_oserror_starting_child_marks_failed_and_removes_prompt(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    def raising_popen(*a, **k):
        raise OSError("no such file or directory")

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path),
        [str(tmp_path / "no-such-binary")], popen=raising_popen,
    )

    assert code == 127
    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert "no such file" in failed["result"]
    assert not prompt_path.exists()


def test_supervise_oserror_leaves_a_cancelled_task_alone(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    store.update_task(task["id"], status="cancelled")
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    def raising_popen(*a, **k):
        raise OSError("no such file or directory")

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path),
        [str(tmp_path / "no-such-binary")], popen=raising_popen,
    )

    assert code == 127
    unchanged = store.get_task(task["id"])
    assert unchanged["status"] == "cancelled"


def test_supervise_oserror_with_a_deleted_task_does_not_raise(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])
    store.delete_task(task["id"])

    def raising_popen(*a, **k):
        raise OSError("no such file or directory")

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path),
        [str(tmp_path / "no-such-binary")], popen=raising_popen,
    )

    assert code == 127
    assert store.get_task(task["id"]) is None


def test_supervise_removes_prompt_file_even_on_nonzero_exit(store, config, tmp_path):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(9)
    )

    assert not prompt_path.exists()


# -- lane pause and kick on exit (design.md decision 2) -----------------------


def test_supervise_pauses_the_lane_when_a_queue_task_fails(store, config, tmp_path):
    task = _running_queue_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(3)
    )

    assert store.get_task(task["id"])["status"] == "failed"
    lane = store.get_lane(str(tmp_path))
    assert lane["paused"] == 1
    assert str(task["id"]) in lane["reason"]


def test_supervise_pauses_the_lane_when_a_queue_task_is_interrupted(store, config, tmp_path):
    task = _running_queue_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(0)
    )

    assert store.get_task(task["id"])["status"] == "interrupted"
    lane = store.get_lane(str(tmp_path))
    assert lane["paused"] == 1


def test_supervise_leaves_the_lane_running_when_a_queue_task_succeeds(store, config, tmp_path):
    task = _running_queue_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])
    # The Stop hook marks it done from inside the child; simulate that by
    # updating the store before the child exits (the child's own exit code
    # is irrelevant once the task is already terminal).
    store.update_task(task["id"], status="done", result="finished")

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(0)
    )

    lane = store.get_lane(str(tmp_path))
    assert lane is None or lane["paused"] == 0


def test_supervise_kicks_the_lane_so_the_next_queue_task_starts(store, config, tmp_path):
    task = _running_queue_task(store, tmp_path)
    next_task = store.create_task(
        kind="prompt", body="next", status="queued", source="cli", cwd=str(tmp_path)
    )
    store.enqueue_task(next_task["id"], "default")
    store.update_task(task["id"], status="done", result="finished")
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(0)
    )

    assert store.get_task(next_task["id"])["status"] == "running"


def test_supervise_kick_failure_does_not_crash_the_supervisor(store, config, tmp_path, monkeypatch):
    task = _running_task(store, tmp_path)
    prompt_path, log_path = _prompt_and_log(config, task["id"])

    def raising_kick(*a, **k):
        raise RuntimeError("boom")

    import tasky.supervise as supervise_mod

    monkeypatch.setattr(supervise_mod.scheduler, "kick", raising_kick)

    code = supervise(
        config, task["id"], task["session_id"], str(prompt_path), str(log_path), _python_exit(0)
    )

    assert code == 0
    assert store.get_task(task["id"])["status"] == "interrupted"
