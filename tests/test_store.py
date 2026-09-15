from __future__ import annotations

import multiprocessing
import sqlite3
import tempfile

import pytest

from tasky.store import Store


def test_create_task_requires_valid_kind(store):
    with pytest.raises(ValueError):
        store.create_task(kind="bogus", body="x", status="queued", source="cli")


def test_create_task_requires_valid_status(store):
    with pytest.raises(ValueError):
        store.create_task(kind="prompt", body="x", status="bogus", source="cli")


def test_create_task_derives_title_from_body(store):
    body = "fix the thing\nmore detail"
    task = store.create_task(kind="prompt", body=body, status="running", source="hook")
    assert task["title"] == "fix the thing"


def test_create_task_project_is_basename_of_cwd(store, tmp_path):
    project_dir = str(tmp_path / "project")
    task = store.create_task(
        kind="prompt", body="x", status="running", source="hook", cwd=project_dir
    )
    assert task["project"] == "project"


def test_create_task_project_is_empty_when_no_cwd(store):
    task = store.create_task(kind="prompt", body="x", status="running", source="hook")
    assert task["project"] == ""


def test_create_task_external_id_conflict_returns_existing_unchanged(store):
    first = store.create_task(
        kind="delegation", body="do it", status="running", source="hook", external_id="dup-1"
    )
    second = store.create_task(
        kind="delegation",
        body="different body",
        status="queued",
        source="hook",
        external_id="dup-1",
    )
    assert second["id"] == first["id"]
    assert second["body"] == "do it"
    assert second["status"] == "running"


def test_create_task_external_id_conflict_does_not_leave_transaction_open(store):
    """L-new-3: an IntegrityError from a duplicate external_id must roll back so the
    connection is free to claim (and any other connection is free to write)."""
    store.create_task(
        kind="delegation", body="first", status="queued", source="hook", external_id="dup-2"
    )
    queued = store.create_task(kind="prompt", body="q", status="queued", source="cli")

    existing = store.create_task(
        kind="delegation", body="second", status="queued", source="hook", external_id="dup-2"
    )
    assert existing["body"] == "first"

    # The same connection can claim right away: no leftover write transaction.
    claimed = store.claim_task(queued["id"], status="running")
    assert claimed is not None
    assert claimed["status"] == "running"

    # A second connection can write immediately too: the lock was released.
    other = Store(store.db_path)
    try:
        other.create_task(
            kind="prompt", body="from another connection", status="queued", source="cli"
        )
    finally:
        other.close()


def test_create_task_truncates_result(store):
    small = Store(store.db_path, max_result=30)
    try:
        task = small.create_task(
            kind="prompt", body="x", status="done", source="hook", result="y" * 80
        )
        assert task["result"].endswith("\n…[truncated]")
        assert len(task["result"]) == 30
    finally:
        small.close()


def test_create_task_does_not_truncate_short_result(store):
    task = store.create_task(kind="prompt", body="x", status="done", source="hook", result="short")
    assert task["result"] == "short"


def test_create_task_position_assignment_for_queued(store):
    first = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    second = store.create_task(kind="prompt", body="b", status="queued", source="cli")
    assert first["position"] is not None
    assert second["position"] > first["position"]


def test_create_task_no_position_for_non_queued(store):
    task = store.create_task(kind="prompt", body="a", status="running", source="hook")
    assert task["position"] is None


def test_create_task_sets_started_at_for_running(store):
    task = store.create_task(kind="prompt", body="a", status="running", source="hook")
    assert task["started_at"] is not None
    assert task["finished_at"] is None


def test_create_task_sets_finished_at_for_terminal(store):
    task = store.create_task(kind="prompt", body="a", status="done", source="hook")
    assert task["finished_at"] is not None


def test_create_task_respects_given_timestamps(store):
    task = store.create_task(
        kind="prompt",
        body="a",
        status="running",
        source="hook",
        started_at="2020-01-01T00:00:00.000Z",
    )
    assert task["started_at"] == "2020-01-01T00:00:00.000Z"


def test_update_task_missing_id_raises_key_error(store):
    with pytest.raises(KeyError):
        store.update_task(999999, title="x")


def test_update_task_unknown_field_raises_value_error(store):
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    with pytest.raises(ValueError):
        store.update_task(task["id"], bogus="x")


def test_update_task_invalid_status_raises_value_error(store):
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    with pytest.raises(ValueError):
        store.update_task(task["id"], status="bogus")


def test_update_task_to_running_sets_started_at(store):
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    updated = store.update_task(task["id"], status="running")
    assert updated["started_at"] is not None


def test_update_task_to_terminal_sets_finished_at(store):
    task = store.create_task(kind="prompt", body="a", status="running", source="hook")
    updated = store.update_task(task["id"], status="done")
    assert updated["finished_at"] is not None


def test_update_task_to_queued_clears_timestamps_and_sets_tail_position(store):
    task = store.create_task(kind="prompt", body="a", status="running", source="hook")
    other_queued = store.create_task(kind="prompt", body="b", status="queued", source="cli")
    updated = store.update_task(task["id"], status="queued")
    assert updated["started_at"] is None
    assert updated["finished_at"] is None
    assert updated["position"] > other_queued["position"]


def test_update_task_repeating_same_status_does_not_reset_started_at(store):
    task = store.create_task(kind="prompt", body="a", status="running", source="hook")
    original_started_at = task["started_at"]
    updated = store.update_task(task["id"], status="running", title="renamed")
    assert updated["started_at"] == original_started_at
    assert updated["title"] == "renamed"


def test_update_task_truncates_result(store):
    small = Store(store.db_path, max_result=30)
    try:
        task = small.create_task(kind="prompt", body="a", status="running", source="hook")
        updated = small.update_task(task["id"], result="y" * 50)
        assert updated["result"].endswith("\n…[truncated]")
        assert len(updated["result"]) == 30
    finally:
        small.close()


def test_get_task_by_external_id(store):
    task = store.create_task(
        kind="delegation", body="a", status="running", source="hook", external_id="ext-1"
    )
    found = store.get_task_by_external_id("ext-1")
    assert found["id"] == task["id"]
    assert store.get_task_by_external_id("missing") is None


def test_find_task_by_agent_id(store):
    task = store.create_task(kind="delegation", body="a", status="running", source="hook")
    store.update_task(task["id"], agent_id="agent-1")
    found = store.find_task_by_agent_id("agent-1")
    assert found["id"] == task["id"]
    assert store.find_task_by_agent_id("missing") is None


def test_delete_task(store):
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    assert store.delete_task(task["id"]) is True
    assert store.get_task(task["id"]) is None
    assert store.delete_task(task["id"]) is False


def test_list_tasks_filters_by_status(store):
    store.create_task(kind="prompt", body="a", status="queued", source="cli")
    store.create_task(kind="prompt", body="b", status="running", source="hook")
    tasks = store.list_tasks(status="queued")
    assert all(t["status"] == "queued" for t in tasks)
    assert len(tasks) == 1


def test_list_tasks_filters_by_multiple_statuses(store):
    store.create_task(kind="prompt", body="a", status="queued", source="cli")
    store.create_task(kind="prompt", body="b", status="running", source="hook")
    store.create_task(kind="prompt", body="c", status="done", source="hook")
    tasks = store.list_tasks(status=["queued", "done"])
    assert {t["status"] for t in tasks} == {"queued", "done"}


def test_list_tasks_orders_queued_by_position(store):
    first = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    second = store.create_task(kind="prompt", body="b", status="queued", source="cli")
    third = store.create_task(kind="prompt", body="c", status="queued", source="cli")
    store.move_task(third["id"], first["id"])
    tasks = store.list_tasks(status="queued")
    assert [t["id"] for t in tasks] == [third["id"], first["id"], second["id"]]


def test_list_tasks_orders_others_by_recency_desc(store):
    older = store.create_task(
        kind="prompt",
        body="a",
        status="done",
        source="hook",
        started_at="2020-01-01T00:00:00.000Z",
        finished_at="2020-01-01T00:00:00.000Z",
    )
    newer = store.create_task(
        kind="prompt",
        body="b",
        status="done",
        source="hook",
        started_at="2021-01-01T00:00:00.000Z",
        finished_at="2021-01-01T00:00:00.000Z",
    )
    tasks = store.list_tasks(status="done")
    assert [t["id"] for t in tasks] == [newer["id"], older["id"]]


def test_list_tasks_filters_by_session_cwd_kind_and_limit(store):
    store.upsert_session("s1", cwd="/proj")
    store.create_task(
        kind="prompt", body="a", status="running", source="hook", session_id="s1", cwd="/proj"
    )
    store.create_task(
        kind="delegation", body="b", status="running", source="hook", session_id="s1", cwd="/proj"
    )
    store.create_task(kind="prompt", body="c", status="running", source="hook", cwd="/other")
    assert len(store.list_tasks(session_id="s1")) == 2
    assert len(store.list_tasks(cwd="/other")) == 1
    assert len(store.list_tasks(kind="delegation")) == 1
    assert len(store.list_tasks(limit=1)) == 1


def test_latest_running_task_orders_by_started_at_desc_then_id_desc(store):
    store.upsert_session("s1")
    store.create_task(
        kind="prompt",
        body="a",
        status="running",
        source="hook",
        session_id="s1",
        started_at="2020-01-01T00:00:00.000Z",
    )
    second = store.create_task(
        kind="prompt",
        body="b",
        status="running",
        source="hook",
        session_id="s1",
        started_at="2020-01-01T00:00:00.000Z",
    )
    latest = store.latest_running_task("s1")
    assert latest["id"] == second["id"]


def test_latest_running_task_filters_by_prompt_id_and_kind(store):
    store.upsert_session("s1")
    store.create_task(
        kind="delegation",
        body="a",
        status="running",
        source="hook",
        session_id="s1",
        prompt_id="p1",
    )
    prompt_task = store.create_task(
        kind="prompt",
        body="b",
        status="running",
        source="hook",
        session_id="s1",
        prompt_id="p1",
    )
    result = store.latest_running_task("s1", prompt_id="p1")
    assert result["id"] == prompt_task["id"]


def test_latest_running_task_returns_none_without_match(store):
    store.upsert_session("s1")
    assert store.latest_running_task("s1") is None


def test_running_children(store):
    parent = store.create_task(kind="prompt", body="a", status="running", source="hook")
    child = store.create_task(
        kind="delegation", body="b", status="running", source="hook", parent_id=parent["id"]
    )
    store.create_task(
        kind="delegation", body="c", status="done", source="hook", parent_id=parent["id"]
    )
    children = store.running_children(parent["id"])
    assert [c["id"] for c in children] == [child["id"]]


def test_next_queued_prefers_session_bound(store):
    store.upsert_session("s1", cwd="/proj")
    unbound = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd="/proj")
    bound = store.create_task(
        kind="prompt", body="b", status="queued", source="cli", session_id="s1", cwd="/proj"
    )
    result = store.next_queued("s1", "/proj")
    assert result["id"] == bound["id"]
    assert unbound["id"] != bound["id"]


def test_next_queued_falls_back_to_unbound_cwd_match(store):
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd="/proj")
    result = store.next_queued("s-without-queue", "/proj")
    assert result["id"] == task["id"]


def test_next_queued_returns_none_when_nothing_matches(store):
    assert store.next_queued("s1", "/nowhere") is None


def test_move_task_before_another(store):
    a = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    b = store.create_task(kind="prompt", body="b", status="queued", source="cli")
    c = store.create_task(kind="prompt", body="c", status="queued", source="cli")
    store.move_task(c["id"], b["id"])
    ordered = store.list_tasks(status="queued")
    assert [t["id"] for t in ordered] == [a["id"], c["id"], b["id"]]


def test_move_task_to_end_when_before_id_none(store):
    a = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    b = store.create_task(kind="prompt", body="b", status="queued", source="cli")
    store.move_task(a["id"], None)
    ordered = store.list_tasks(status="queued")
    assert [t["id"] for t in ordered] == [b["id"], a["id"]]


def test_move_task_missing_id_raises_key_error(store):
    with pytest.raises(KeyError):
        store.move_task(999999, None)


def test_move_task_missing_before_id_raises_key_error(store):
    a = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    with pytest.raises(KeyError):
        store.move_task(a["id"], 999999)


def _create_queued_and_record(db_path, positions):
    with Store(db_path) as store:
        task = store.create_task(kind="prompt", body="++ queued", status="queued", source="chat")
        positions.append(task["position"])


def test_create_task_queued_positions_are_distinct_across_processes():
    """L6: two ``++`` queued tasks created from separate processes must land on
    distinct positions — a race in ``_next_position()`` would give them the same one."""
    manager = multiprocessing.Manager()
    for _iteration in range(20):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/tasky.db"
            positions = manager.list()
            proc_count = 2 + (_iteration % 3)  # 2, 3, or 4 processes
            procs = [
                multiprocessing.Process(target=_create_queued_and_record, args=(db_path, positions))
                for _ in range(proc_count)
            ]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(30)

            values = list(positions)
            assert len(values) == proc_count
            assert len(set(values)) == proc_count


def test_upsert_session_creates_and_updates(store):
    created = store.upsert_session("s1", cwd="/proj", transcript_path="/t.jsonl", source="hook")
    assert created["state"] == "active"
    assert created["cwd"] == "/proj"
    updated = store.upsert_session("s1", cwd="/proj2")
    assert updated["cwd"] == "/proj2"
    assert updated["transcript_path"] == "/t.jsonl"


def test_upsert_session_reactivates_ended_session(store):
    store.upsert_session("s1")
    store.end_session("s1")
    assert store.get_session("s1")["state"] == "ended"
    reactivated = store.upsert_session("s1")
    assert reactivated["state"] == "active"


def test_get_session_missing_returns_none(store):
    assert store.get_session("missing") is None


def test_list_sessions_orders_by_last_seen_desc(store):
    store.upsert_session("s1", at="2020-01-01T00:00:00.000Z")
    store.upsert_session("s2", at="2021-01-01T00:00:00.000Z")
    sessions = store.list_sessions()
    assert [s["id"] for s in sessions] == ["s2", "s1"]


def test_update_session_missing_id_raises_key_error(store):
    with pytest.raises(KeyError):
        store.update_session("missing", title="x")


def test_update_session_unknown_field_raises_value_error(store):
    store.upsert_session("s1")
    with pytest.raises(ValueError):
        store.update_session("s1", bogus="x")


def test_update_session_invalid_state_raises_value_error(store):
    store.upsert_session("s1")
    with pytest.raises(ValueError):
        store.update_session("s1", state="bogus")


def test_update_session_sets_fields(store):
    store.upsert_session("s1")
    updated = store.update_session("s1", title="my title", auto_pull=True, pull_chain=2)
    assert updated["title"] == "my title"
    assert updated["auto_pull"] == 1
    assert updated["pull_chain"] == 2


def test_end_session_marks_running_tasks_interrupted(store):
    store.upsert_session("s1")
    running = store.create_task(
        kind="prompt", body="a", status="running", source="hook", session_id="s1"
    )
    queued = store.create_task(
        kind="prompt", body="b", status="queued", source="cli", session_id="s1"
    )
    changed = store.end_session("s1")
    assert changed == 1
    assert store.get_task(running["id"])["status"] == "interrupted"
    assert store.get_task(queued["id"])["status"] == "queued"
    assert store.get_session("s1")["state"] == "ended"


def test_session_has_tasks_not_from(store):
    store.upsert_session("s1")
    store.create_task(kind="prompt", body="a", status="running", source="hook", session_id="s1")
    assert store.session_has_tasks_not_from("s1", "import") is True
    assert store.session_has_tasks_not_from("s1", "hook") is False


def test_interrupt_session_marks_every_running_task_including_delegations(store):
    store.upsert_session("s1")
    prompt = store.create_task(
        kind="prompt", body="a", status="running", source="hook", session_id="s1"
    )
    delegation = store.create_task(
        kind="delegation",
        body="d",
        status="running",
        source="hook",
        session_id="s1",
        parent_id=prompt["id"],
    )
    queued = store.create_task(
        kind="prompt", body="b", status="queued", source="cli", session_id="s1"
    )
    changed = store.interrupt_session("s1")
    assert changed == 2
    assert store.get_task(prompt["id"])["status"] == "interrupted"
    assert store.get_task(delegation["id"])["status"] == "interrupted"
    assert store.get_task(queued["id"])["status"] == "queued"


def test_state_returns_non_terminal_and_bounded_terminal(store):
    store.create_task(kind="prompt", body="a", status="running", source="hook")
    for i in range(3):
        store.create_task(
            kind="prompt",
            body=f"b{i}",
            status="done",
            source="hook",
            finished_at=f"2020-01-0{i + 1}T00:00:00.000Z",
        )
    result = store.state(done_limit=2)
    assert result["rev"] == store.rev()
    statuses = [t["status"] for t in result["tasks"]]
    assert statuses.count("running") == 1
    assert statuses.count("done") == 2


def test_state_includes_sessions(store):
    store.upsert_session("s1")
    result = store.state()
    assert [s["id"] for s in result["sessions"]] == ["s1"]


def test_rev_starts_at_zero_and_bumps_on_write(store):
    assert store.rev() == 0
    store.create_task(kind="prompt", body="a", status="queued", source="cli")
    assert store.rev() == 1


def test_rev_bumps_from_a_raw_second_connection(store):
    """The revision bump comes from SQLite triggers, not from Store's Python code:
    a bare sqlite3 connection performing raw DML must move meta.rev the same way."""
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli")
    before = store.rev()

    raw = sqlite3.connect(store.db_path)
    try:
        raw.execute(
            "INSERT INTO tasks (kind, title, body, status, source, created_at) "
            "VALUES ('prompt', 't', 'b', 'queued', 'cli', '2020-01-01T00:00:00.000Z')"
        )
        raw.commit()
        assert store.rev() == before + 1
        before = store.rev()

        raw.execute("UPDATE tasks SET title = 'renamed' WHERE id = ?", (task["id"],))
        raw.commit()
        assert store.rev() == before + 1
        before = store.rev()

        raw.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
        raw.commit()
        assert store.rev() == before + 1
        before = store.rev()

        raw.execute(
            "INSERT INTO sessions (id, started_at, last_seen_at) "
            "VALUES ('raw-session', '2020-01-01T00:00:00.000Z', '2020-01-01T00:00:00.000Z')"
        )
        raw.commit()
        assert store.rev() == before + 1
        before = store.rev()

        raw.execute("UPDATE sessions SET title = 'x' WHERE id = 'raw-session'")
        raw.commit()
        assert store.rev() == before + 1
    finally:
        raw.close()


def test_wal_mode_and_schema_version_persist_on_disk(store):
    store.create_task(kind="prompt", body="a", status="queued", source="cli")
    raw = sqlite3.connect(store.db_path)
    try:
        mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
        version = raw.execute("PRAGMA user_version").fetchone()[0]
        assert mode.lower() == "wal"
        assert version == 2
    finally:
        raw.close()


def test_store_open_creates_db_file(config):
    store_instance = Store.open(config)
    try:
        assert config.db_path.exists()
    finally:
        store_instance.close()


def test_store_context_manager_closes(config):
    with Store.open(config) as opened:
        opened.create_task(kind="prompt", body="a", status="queued", source="cli")
    with pytest.raises(sqlite3.ProgrammingError):
        opened.rev()


def test_access_token_is_redacted_on_create_and_update(store):
    link = "open http://127.0.0.1:7733/#token=abcDEF123_-xyz now"

    created = store.create_task(kind="prompt", body="b", status="done", source="hook", result=link)
    updated = store.update_task(created["id"], result=link + " and #token=second_value")

    assert created["result"] == "open http://127.0.0.1:7733/#token=<redacted> now"
    assert "abcDEF123_-xyz" not in updated["result"]
    assert "second_value" not in updated["result"]
    assert updated["result"].count("#token=<redacted>") == 2


def test_next_queued_ignores_serial_lane_tasks(store):
    store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd="/proj")
    serial_task = store.create_task(
        kind="prompt", body="b", status="queued", source="cli", cwd="/proj"
    )
    store.enqueue_task(serial_task["id"], "default")

    result = store.next_queued(None, "/proj")
    assert result["id"] != serial_task["id"]


def test_move_task_is_scoped_to_its_own_lane(store, tmp_path):
    cwd = str(tmp_path)
    inbox_a = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    inbox_b = store.create_task(kind="prompt", body="b", status="queued", source="cli", cwd=cwd)
    queue_a = store.create_task(kind="prompt", body="c", status="queued", source="cli", cwd=cwd)
    queue_b = store.create_task(kind="prompt", body="d", status="queued", source="cli", cwd=cwd)
    store.enqueue_task(queue_a["id"], "default")
    store.enqueue_task(queue_b["id"], "default")

    inbox_positions_before = {
        inbox_a["id"]: store.get_task(inbox_a["id"])["position"],
        inbox_b["id"]: store.get_task(inbox_b["id"])["position"],
    }

    # Reordering a run-queue task must not touch Inbox order or vice versa.
    store.move_task(queue_b["id"], queue_a["id"])
    inbox_order = [t["id"] for t in store.list_tasks(status="queued") if t["lane"] is None]
    queue_order = [t["id"] for t in store.list_tasks(status="queued") if t["lane"] == "serial"]
    assert inbox_order == [inbox_a["id"], inbox_b["id"]]
    assert queue_order == [queue_b["id"], queue_a["id"]]
    # A cross-lane scan that happens to preserve each lane's relative order
    # would still pass the two checks above; pin the actual position values
    # too, since a lane-scoped move must leave the other lane's numbers alone.
    assert store.get_task(inbox_a["id"])["position"] == inbox_positions_before[inbox_a["id"]]
    assert store.get_task(inbox_b["id"])["position"] == inbox_positions_before[inbox_b["id"]]


def test_get_lane_missing_returns_none(store):
    assert store.get_lane("/nowhere") is None


def test_set_lane_pause_and_resume(store):
    paused = store.set_lane("/proj", paused=True, reason="task #1 failed")
    assert paused == {"cwd": "/proj", "paused": 1, "reason": "task #1 failed"}
    resumed = store.set_lane("/proj", paused=False)
    assert resumed["paused"] == 0
    assert resumed["reason"] is None


def test_list_lanes_orders_by_cwd(store):
    store.set_lane("/b", paused=True)
    store.set_lane("/a", paused=True)
    assert [lane["cwd"] for lane in store.list_lanes()] == ["/a", "/b"]


def test_serial_lane_cwds_only_lists_queued_serial_tasks(store, tmp_path):
    cwd = str(tmp_path)
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    assert store.serial_lane_cwds() == []
    store.enqueue_task(task["id"], "default")
    assert store.serial_lane_cwds() == [cwd]


def test_claim_serial_head_claims_lowest_position(store, tmp_path):
    cwd = str(tmp_path)
    first = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    second = store.create_task(kind="prompt", body="b", status="queued", source="cli", cwd=cwd)
    store.enqueue_task(first["id"], "default")
    store.enqueue_task(second["id"], "default")

    claimed = store.claim_serial_head(cwd, "sess-1")
    assert claimed["id"] == first["id"]
    assert claimed["status"] == "running"
    assert claimed["session_id"] == "sess-1"
    assert claimed["run_mode"] == "serial"


def test_claim_serial_head_returns_none_when_paused(store, tmp_path):
    cwd = str(tmp_path)
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    store.enqueue_task(task["id"], "default")
    store.set_lane(cwd, paused=True, reason="x")

    assert store.claim_serial_head(cwd, "sess-1") is None
    assert store.get_task(task["id"])["status"] == "queued"


def test_claim_serial_head_returns_none_when_already_running(store, tmp_path):
    cwd = str(tmp_path)
    first = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    second = store.create_task(kind="prompt", body="b", status="queued", source="cli", cwd=cwd)
    store.enqueue_task(first["id"], "default")
    store.enqueue_task(second["id"], "default")
    store.claim_serial_head(cwd, "sess-1")

    assert store.claim_serial_head(cwd, "sess-2") is None
    assert store.get_task(second["id"])["status"] == "queued"


def test_claim_serial_head_returns_none_with_no_queue(store, tmp_path):
    assert store.claim_serial_head(str(tmp_path), "sess-1") is None


def test_claim_serial_head_concurrent_kicks_claim_exactly_one(config, tmp_path):
    """Several threads, each its own connection, kicking the same idle lane."""
    import threading

    cwd = str(tmp_path)
    with Store.open(config) as setup_store:
        task = setup_store.create_task(
            kind="prompt", body="a", status="queued", source="cli", cwd=cwd
        )
        setup_store.enqueue_task(task["id"], "default")

    results = []

    def attempt(i):
        with Store.open(config) as thread_store:
            results.append(thread_store.claim_serial_head(cwd, f"sess-{i}"))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r is not None]
    assert len(successes) == 1


def test_enqueue_task_sets_lane_and_permission_mode(store, tmp_path):
    task = store.create_task(
        kind="prompt", body="a", status="queued", source="cli", cwd=str(tmp_path)
    )
    enqueued = store.enqueue_task(task["id"], "acceptEdits")
    assert enqueued["lane"] == "serial"
    assert enqueued["permission_mode"] == "acceptEdits"


def test_enqueue_task_before_id_orders_within_the_lane(store, tmp_path):
    cwd = str(tmp_path)
    a = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    b = store.create_task(kind="prompt", body="b", status="queued", source="cli", cwd=cwd)
    store.enqueue_task(a["id"], "default")
    store.enqueue_task(b["id"], "default", before_id=a["id"])
    order = [t["id"] for t in store.list_tasks(status="queued") if t["lane"] == "serial"]
    assert order == [b["id"], a["id"]]


def test_enqueue_task_unknown_before_id_raises_key_error(store, tmp_path):
    task = store.create_task(
        kind="prompt", body="a", status="queued", source="cli", cwd=str(tmp_path)
    )
    with pytest.raises(KeyError):
        store.enqueue_task(task["id"], "default", before_id=999999)


def test_enqueue_task_not_queued_returns_none(store, tmp_path):
    task = store.create_task(
        kind="prompt", body="a", status="running", source="hook", cwd=str(tmp_path)
    )
    assert store.enqueue_task(task["id"], "default") is None


def test_clear_task_lane_returns_to_inbox(store, tmp_path):
    cwd = str(tmp_path)
    task = store.create_task(kind="prompt", body="a", status="queued", source="cli", cwd=cwd)
    store.enqueue_task(task["id"], "default")
    cleared = store.clear_task_lane(task["id"])
    assert cleared["lane"] is None


def test_clear_task_lane_not_queued_returns_none(store, tmp_path):
    task = store.create_task(
        kind="prompt", body="a", status="running", source="hook", cwd=str(tmp_path)
    )
    assert store.clear_task_lane(task["id"]) is None


def test_latest_session_for_cwd(store):
    store.upsert_session("s1", cwd="/proj", transcript_path="/t1", at="2020-01-01T00:00:00.000Z")
    store.upsert_session("s2", cwd="/proj", transcript_path="/t2", at="2021-01-01T00:00:00.000Z")
    store.upsert_session("s3", cwd="/other", transcript_path="/t3", at="2022-01-01T00:00:00.000Z")
    store.upsert_session("s4", cwd="/proj", at="2023-01-01T00:00:00.000Z")
    latest = store.latest_session_for_cwd("/proj")
    assert latest["id"] == "s2"


def test_latest_session_for_cwd_returns_none_without_match(store):
    assert store.latest_session_for_cwd("/nowhere") is None


def test_redaction_happens_before_truncation(tmp_path):
    from tasky.store import Store

    with Store(tmp_path / "t.db", max_result=40) as small:
        secret = "x" * 20 + "#token=" + "s" * 40
        task = small.create_task(
            kind="prompt", body="b", status="done", source="hook", result=secret
        )
    assert "sss" not in task["result"]
