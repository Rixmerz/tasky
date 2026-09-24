import json
import subprocess

import pytest

from tasky import insights
from tasky.hooks import handle, main
from tasky.store import Store


def _task(store, body, result, cwd="/p", finished_at="2026-09-01T10:00:00.000Z"):
    return store.create_task(
        kind="prompt", body=body, status="done", source="hook", cwd=cwd,
        result=result, finished_at=finished_at,
    )


class FakeClaude:
    """Stands in for `claude -p`: records each call and replies with canned outputs."""

    def __init__(self, *outputs, cost=0.01):
        self.outputs = list(outputs)
        self.calls = []
        self.cost = cost

    def __call__(self, cmd, *, input, env, **kwargs):
        self.calls.append({"cmd": cmd, "input": input, "env": env})
        output = self.outputs.pop(0) if self.outputs else {"new": [], "updates": []}
        if isinstance(output, Exception):
            raise output
        reply = {"is_error": False, "total_cost_usd": self.cost, "structured_output": output}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")


# -- sync ------------------------------------------------------------------------


def test_sync_stores_cited_records_and_advances_the_cursor(store, config):
    fix = _task(store, "login fails", "added a retry")
    real = _task(store, "login still fails", "real cause: cookie domain; fixed")
    fake = FakeClaude({
        "new": [
            {"kind": "dead_end", "title": "Retry on login", "detail": "cause was the cookie",
             "solution": "set the cookie domain", "happened_on": "2026-09-01",
             "task_ids": [fix["id"], real["id"]]},
            {"kind": "milestone", "title": "Invented", "task_ids": [999]},
        ],
        "updates": [],
    })

    summary = insights.sync(config, "/p", run=fake)

    assert summary["added"] == 1
    assert summary["tasks"] == 2
    assert summary["pending"] == 0
    assert summary["cost_usd"] == pytest.approx(0.01)
    records = store.list_insights("/p")
    assert [(r["kind"], r["task_ids"]) for r in records] == [
        ("dead_end", [fix["id"], real["id"]])
    ]
    sync_row = store.insight_sync("/p")
    assert sync_row["state"] == "idle"
    assert sync_row["last_task_id"] == real["id"]
    assert sync_row["total_cost_usd"] == pytest.approx(0.01)


def test_sync_runs_the_model_without_tools_hooks_or_project_context(store, config):
    _task(store, "a", "b")
    fake = FakeClaude({"new": [], "updates": []})

    insights.sync(config, "/p", run=fake)

    call = fake.calls[0]
    assert call["env"]["TASKY_HOOKS_OFF"] == "1"
    cmd = call["cmd"]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd
    assert "<asked>a</asked>" in call["input"]


def test_second_sync_only_reads_new_tasks_and_can_update_records(store, config):
    first = _task(store, "deploy breaks", "no fix yet")
    insights.sync(config, "/p", run=FakeClaude({
        "new": [{"kind": "problem", "title": "Deploy breaks", "task_ids": [first["id"]]}],
        "updates": [],
    }))
    problem = store.list_insights("/p")[0]
    assert problem["state"] == "open"

    second = _task(store, "fixed deploy", "missing env var added")
    fake = FakeClaude({
        "new": [],
        "updates": [{"id": problem["id"], "state": "solved", "solution": "add the env var",
                     "task_ids": [second["id"]]}],
    })
    insights.sync(config, "/p", run=fake)

    assert f'<task id="{first["id"]}"' not in fake.calls[0]["input"]
    assert f'<task id="{second["id"]}"' in fake.calls[0]["input"]
    updated = store.get_insight(problem["id"])
    assert updated["state"] == "solved"
    assert updated["solution"] == "add the env var"
    assert updated["task_ids"] == [first["id"], second["id"]]


def test_updates_cannot_touch_another_projects_records(store, config):
    other = store.add_insight(cwd="/other", kind="milestone", title="theirs", task_ids=[1])
    _task(store, "a", "b")
    insights.sync(config, "/p", run=FakeClaude({
        "new": [], "updates": [{"id": other["id"], "title": "hijacked"}],
    }))
    assert store.get_insight(other["id"])["title"] == "theirs"


def test_sync_reads_in_batches_and_stops_at_the_batch_cap(store, config, monkeypatch):
    monkeypatch.setattr(insights, "BATCH_TASKS", 2)
    config = config.__class__(**{**config.__dict__, "insights_max_batches": 2})
    for i in range(5):
        _task(store, f"t{i}", "ok")
    fake = FakeClaude()

    summary = insights.sync(config, "/p", run=fake)

    assert len(fake.calls) == 2
    assert summary["tasks"] == 4
    assert summary["pending"] == 1


def test_failed_batch_keeps_earlier_batches_and_records_the_error(store, config, monkeypatch):
    monkeypatch.setattr(insights, "BATCH_TASKS", 1)
    first = _task(store, "one", "ok")
    _task(store, "two", "ok")
    fake = FakeClaude(
        {"new": [{"kind": "milestone", "title": "One", "task_ids": [first["id"]]}], "updates": []},
        OSError("claude not found"),
    )

    summary = insights.sync(config, "/p", run=fake)

    assert summary["added"] == 1
    assert "claude not found" in summary["error"]
    sync_row = store.insight_sync("/p")
    assert sync_row["state"] == "idle"
    assert sync_row["last_task_id"] == first["id"]
    assert "claude not found" in sync_row["error"]


def test_a_running_sync_blocks_a_second_one(store, config):
    assert store.begin_insight_sync("/p", stale_after_s=60)
    with pytest.raises(insights.SyncError):
        insights.sync(config, "/p", run=FakeClaude())


def test_a_stale_running_sync_is_taken_over(store, config):
    store.begin_insight_sync("/p", stale_after_s=60)
    store._conn.execute(
        "UPDATE insight_syncs SET started_at = '2020-01-01T00:00:00.000Z' WHERE cwd = '/p'"
    )
    store._conn.commit()
    _task(store, "a", "b")
    assert insights.sync(config, "/p", run=FakeClaude())["error"] is None


def test_model_error_reply_is_reported(store, config):
    _task(store, "a", "b")

    def failing(cmd, **kwargs):
        reply = {"is_error": True, "result": "rate limited", "total_cost_usd": 0}
        return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps(reply), stderr="")

    summary = insights.sync(config, "/p", run=failing)
    assert "rate limited" in summary["error"]
    assert store.insight_sync("/p")["last_task_id"] == 0


def test_cancelled_and_running_tasks_are_not_read(store, config):
    store.create_task(kind="prompt", body="x", status="cancelled", source="ui", cwd="/p")
    store.create_task(kind="prompt", body="y", status="running", source="hook", cwd="/p")
    assert store.pending_insight_tasks("/p") == 0


# -- hooks -------------------------------------------------------------------------


def _start(source="startup", cwd="/p"):
    return {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": cwd, "source": source}


def test_session_start_warns_about_the_projects_dead_ends(store, config):
    store.add_insight(
        cwd="/p", kind="dead_end", title="Retry on login", detail="cause was the cookie",
        solution="set the cookie domain", happened_on="2026-09-01", task_ids=[1],
    )
    result = handle(_start(), store, config, {})
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "Retry on login (2026-09-01)" in context
    assert "set the cookie domain" in context


def test_session_start_without_dead_ends_adds_nothing(store, config):
    store.add_insight(cwd="/p", kind="milestone", title="Shipped", task_ids=[1])
    assert handle(_start(), store, config, {}) is None
    assert handle(_start(cwd="/elsewhere"), store, config, {}) is None


def test_dead_end_context_can_be_turned_off(store, config):
    store.add_insight(cwd="/p", kind="dead_end", title="x", task_ids=[1])
    off = config.__class__(**{**config.__dict__, "dead_end_items": 0})
    assert handle(_start(), store, off, {}) is None


def test_hooks_off_records_nothing(config):
    import io

    event = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/p",
             "prompt": "summarise"}
    env = {"TASKY_HOME": str(config.home), "TASKY_HOOKS_OFF": "1"}
    assert main(io.StringIO(json.dumps(event)), io.StringIO(), env) == 0
    with Store.open(config) as store:
        assert store.list_tasks() == []
