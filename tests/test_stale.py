"""tasky/stale.py: sweeping a `running` task with no sign of life."""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timedelta, timezone

from tasky import stale
from tasky.server import make_server

_NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
_NOW_TS = _NOW.timestamp()


def _iso(hours_ago: float) -> str:
    when = _NOW - timedelta(hours=hours_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def _stale_config(config, hours=24):
    return dataclasses.replace(config, stale_running_hours=hours)


def _running_task(
    store,
    tmp_path,
    *,
    session_id="sess-1",
    hours_ago=48,
    run_mode=None,
    kind="prompt",
    parent_id=None,
):
    task = store.create_task(
        kind=kind,
        body="x",
        status="running",
        source="hook",
        cwd=str(tmp_path),
        session_id=session_id,
        started_at=_iso(hours_ago),
        parent_id=parent_id,
    )
    if run_mode:
        store.update_task(task["id"], run_mode=run_mode)
    return store.get_task(task["id"])


def _old_session(store, tmp_path, session_id="sess-1", hours_ago=48, transcript_path=None):
    store.upsert_session(
        session_id, cwd=str(tmp_path), transcript_path=transcript_path, at=_iso(hours_ago)
    )
    return store.get_session(session_id)


def test_no_op_below_threshold(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=1)
    _old_session(store, tmp_path, hours_ago=1)

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert moved == []
    assert store.get_task(task["id"])["status"] == "running"


def test_stale_task_moves_to_needs_attention_with_reason(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100)

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert [t["id"] for t in moved] == [task["id"]]
    updated = store.get_task(task["id"])
    assert updated["status"] == "interrupted"
    assert "no activity for 4 days" in updated["result"]
    assert "its session or worker is gone" in updated["result"]
    assert updated["finished_at"] is not None


def test_fresh_transcript_protects_a_long_running_task(store, config, tmp_path):
    """A worker whose session, prompt and task are all days old is not stale
    while its transcript file is still being written to -- the evidence a
    long tool call or a big single turn leaves behind."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    os.utime(transcript, (_NOW_TS - 60, _NOW_TS - 60))  # touched a minute ago

    task = _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100, transcript_path=str(transcript))

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert moved == []
    assert store.get_task(task["id"])["status"] == "running"
    assert store.get_session("sess-1")["state"] == "active"


def test_fresh_worker_log_also_protects_the_task(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.log_dir / f"task-{task['id']}.log"
    log_path.write_text("still working\n", encoding="utf-8")
    os.utime(log_path, (_NOW_TS - 120, _NOW_TS - 120))

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert moved == []
    assert store.get_task(task["id"])["status"] == "running"


def test_threshold_edge_exactly_at_boundary_is_not_stale(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=24)
    _old_session(store, tmp_path, hours_ago=24)

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert moved == []
    assert store.get_task(task["id"])["status"] == "running"


def test_threshold_edge_just_past_boundary_is_stale(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=24.01)
    _old_session(store, tmp_path, hours_ago=24.01)

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert [t["id"] for t in moved] == [task["id"]]


def test_terminal_task_is_never_touched(store, config, tmp_path):
    task = store.create_task(
        kind="prompt",
        body="x",
        status="done",
        source="hook",
        cwd=str(tmp_path),
        session_id="sess-1",
        started_at=_iso(200),
        finished_at=_iso(199),
        result="already finished",
    )
    _old_session(store, tmp_path, hours_ago=200)

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert moved == []
    unchanged = store.get_task(task["id"])
    assert unchanged["status"] == "done"
    assert unchanged["result"] == "already finished"


def test_no_session_falls_back_to_started_at(store, config, tmp_path):
    task = store.create_task(
        kind="prompt",
        body="x",
        status="running",
        source="cli",
        cwd=str(tmp_path),
        session_id=None,
        started_at=_iso(100),
    )

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert [t["id"] for t in moved] == [task["id"]]
    assert store.get_task(task["id"])["status"] == "interrupted"


def test_delegation_with_no_sign_of_life_is_swept_too(store, config, tmp_path):
    parent = _running_task(store, tmp_path, hours_ago=100)
    delegation = _running_task(
        store, tmp_path, hours_ago=100, kind="delegation", parent_id=parent["id"]
    )
    _old_session(store, tmp_path, hours_ago=100)

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    moved_ids = {t["id"] for t in moved}
    assert parent["id"] in moved_ids
    assert delegation["id"] in moved_ids
    assert store.get_task(delegation["id"])["status"] == "interrupted"


def test_serial_task_pauses_its_lane(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=100, run_mode="serial")
    _old_session(store, tmp_path, hours_ago=100)

    stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    lane = store.get_lane(str(tmp_path))
    assert lane is not None
    assert lane["paused"]
    assert f"task #{task['id']}" in lane["reason"]


def test_parallel_task_does_not_pause_a_lane(store, config, tmp_path):
    _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100)

    stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert store.get_lane(str(tmp_path)) is None


def test_existing_result_is_kept_and_reason_appended(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=100)
    store.update_task(task["id"], result="the earlier reply, still here")
    _old_session(store, tmp_path, hours_ago=100)

    stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    updated = store.get_task(task["id"])
    assert "the earlier reply, still here" in updated["result"]
    assert "no activity for" in updated["result"]


def test_already_ended_session_is_swept_regardless_of_elapsed_time(store, config, tmp_path):
    """A running task whose session already ended is a contradiction the
    sweep resolves immediately: the process is confirmed gone, so elapsed
    time does not matter."""
    task = _running_task(store, tmp_path, hours_ago=0.01)
    session = _old_session(store, tmp_path, hours_ago=0.01)
    store.update_session(session["id"], state="ended")

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert [t["id"] for t in moved] == [task["id"]]


def test_second_sweep_is_a_no_op(store, config, tmp_path):
    _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100)
    cfg = _stale_config(config, hours=24)

    first = stale.sweep(store, cfg, now=_NOW_TS)
    second = stale.sweep(store, cfg, now=_NOW_TS)

    assert len(first) == 1
    assert second == []


def test_zero_hours_disables_the_sweep(store, config, tmp_path):
    task = _running_task(store, tmp_path, hours_ago=1000)
    _old_session(store, tmp_path, hours_ago=1000)

    moved = stale.sweep(store, _stale_config(config, hours=0), now=_NOW_TS)

    assert moved == []
    assert store.get_task(task["id"])["status"] == "running"


def test_session_ends_once_its_last_running_task_is_swept(store, config, tmp_path):
    _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100)

    stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert store.get_session("sess-1")["state"] == "ended"


def test_server_periodic_tick_runs_the_sweep(store, config, tmp_path):
    """The sweep is only useful wired into the server's periodic sync tick
    (``_Server.sync_titles``, called from every ``/api/version`` poll): this
    is the one line that makes it run in production, so it gets its own
    test rather than relying on ``sweep()`` alone staying correct."""
    task = _running_task(store, tmp_path, hours_ago=100)
    _old_session(store, tmp_path, hours_ago=100)
    srv = make_server(_stale_config(config, hours=24), port=0)
    try:
        srv.sync_titles(store)
    finally:
        srv.server_close()

    assert store.get_task(task["id"])["status"] == "interrupted"


def test_session_with_another_live_task_stays_active(store, config, tmp_path):
    """One task of a session is stale, another still has its own fresh
    per-task evidence (its worker log): sweeping the first must not close
    out the session out from under the second."""
    parent = _running_task(store, tmp_path, hours_ago=100)
    delegation = _running_task(
        store, tmp_path, hours_ago=100, kind="delegation", parent_id=parent["id"]
    )
    _old_session(store, tmp_path, hours_ago=100)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.log_dir / f"task-{delegation['id']}.log"
    log_path.write_text("still working\n", encoding="utf-8")
    os.utime(log_path, (_NOW_TS - 60, _NOW_TS - 60))

    moved = stale.sweep(store, _stale_config(config, hours=24), now=_NOW_TS)

    assert [t["id"] for t in moved] == [parent["id"]]
    assert store.get_task(parent["id"])["status"] == "interrupted"
    assert store.get_task(delegation["id"])["status"] == "running"
    assert store.get_session("sess-1")["state"] == "active"
