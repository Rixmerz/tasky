import io
import json
import sqlite3
import subprocess

import pytest

from tasky import history, mcp, repos
from tasky.config import now_iso
from tasky.hooks import handle, main
from tasky.store import Store

REPO = "github.com/o/app"


@pytest.fixture(autouse=True)
def _fixed_repo(monkeypatch):
    # Folders under /p belong to REPO, anything else to itself; no git calls.
    monkeypatch.setattr(
        repos,
        "resolve",
        lambda cwd: (REPO, "app") if cwd.startswith("/p") else (f"path:{cwd}", cwd),
    )


def _task(store, body, result, cwd="/p", finished_at="2026-09-01T10:00:00.000Z"):
    task = store.create_task(
        kind="prompt",
        body=body,
        status="done",
        source="hook",
        cwd=cwd,
        result=result,
        finished_at=finished_at,
    )
    repos.ensure(store, [cwd])
    return task


class FakeClaude:
    """Stands in for `claude -p`: records each call and replies with canned outputs."""

    def __init__(self, *outputs, cost=0.02):
        self.outputs = list(outputs)
        self.calls = []
        self.cost = cost

    def __call__(self, cmd, *, input, env, **kwargs):
        self.calls.append({"cmd": cmd, "input": input, "env": env})
        output = self.outputs.pop(0) if self.outputs else {"problems": [], "milestones": []}
        if isinstance(output, Exception):
            raise output
        reply = {"is_error": False, "total_cost_usd": self.cost, "structured_output": output}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")


# -- repos --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("git@github.com:Rixmerz/Tasky.git", "github.com/rixmerz/tasky"),
        ("https://user:tok@github.com/Rixmerz/tasky.git", "github.com/rixmerz/tasky"),
        ("ssh://git@bitbucket.org:22/team/api/", "bitbucket.org/team/api"),
    ],
)
def test_normalize_remote(url, key):
    assert repos.normalize_remote(url) == key


def test_worktrees_of_one_repo_share_a_history(store):
    _task(store, "a", "b", cwd="/p/main")
    _task(store, "c", "d", cwd="/p/worktree")
    assert sorted(store.repo_cwds(REPO)) == ["/p/main", "/p/worktree"]
    assert [r["name"] for r in store.repo_list()] == ["app"]


# -- sync -----------------------------------------------------------------------------


def test_sync_builds_a_problem_with_its_attempt_chain(store, config):
    t1 = _task(store, "login fails after deploy", "Added a retry around the login call.")
    t2 = _task(
        store,
        "login still fails",
        "The retry did not help: the cookie domain was wrong. Fixed it.",
        finished_at="2026-09-03T10:00:00.000Z",
    )
    fake = FakeClaude(
        {
            "problems": [
                {
                    "title": "Login fails",
                    "symptom": "401 after deploy",
                    "topic": "auth",
                    "happened_on": "2026-09-01",
                    "task_ids": [t1["id"], t2["id"]],
                    "attempts": [
                        {
                            "description": "retry around login",
                            "outcome": "failed",
                            "why": "cause was the cookie domain",
                            "believed_from": "2026-09-01",
                            "invalidated_on": "2026-09-03",
                            "evidence": "The retry did not help",
                            "task_ids": [t1["id"], t2["id"]],
                        },
                        {
                            "description": "fix cookie domain",
                            "outcome": "worked",
                            "evidence": "this sentence is not in any task",
                            "task_ids": [t2["id"]],
                        },
                    ],
                }
            ],
            "milestones": [{"title": "Invented", "task_ids": [999]}],
        }
    )

    summary = history.sync(config, REPO, model="opus", run=fake)

    assert summary["error"] is None
    assert summary["pending"] == 0
    assert summary["cost_usd"] == pytest.approx(0.02)
    [problem] = store.problems(REPO)
    assert problem["state"] == "solved"  # a worked attempt with no explicit state
    assert problem["topic"] == "auth"
    failed, worked = problem["attempts"]
    assert (failed["seq"], failed["outcome"], failed["invalidated_on"]) == (
        1,
        "failed",
        "2026-09-03",
    )
    assert failed["evidence"] == "The retry did not help"
    assert worked["evidence"] == ""  # not a verbatim quote: dropped
    assert store.milestones(REPO) == []  # cited a task the model was not shown
    assert store.history_sync(REPO)["model"] == "opus"


def test_sync_runs_the_model_without_tools_hooks_or_project_context(store, config):
    _task(store, "a", "b")
    fake = FakeClaude()
    history.sync(config, REPO, run=fake)
    call = fake.calls[0]
    assert call["env"]["TASKY_HOOKS_OFF"] == "1"
    cmd = call["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd
    assert "<asked>a</asked>" in call["input"]


def test_a_later_sync_marks_an_attempt_failed_and_adds_the_next(store, config):
    t1 = _task(store, "deploy breaks", "Pinned node 20 in the Dockerfile.")
    history.sync(
        config,
        REPO,
        run=FakeClaude(
            {
                "problems": [
                    {
                        "title": "Deploy breaks",
                        "task_ids": [t1["id"]],
                        "attempts": [
                            {
                                "description": "pin node 20",
                                "outcome": "pending",
                                "task_ids": [t1["id"]],
                            }
                        ],
                    }
                ],
                "milestones": [],
            }
        ),
    )
    [problem] = store.problems(REPO)
    attempt = problem["attempts"][0]

    t2 = _task(store, "deploy still breaks", "Node 20 was not it; the env var was missing.")
    fake = FakeClaude(
        {
            "problems": [
                {
                    "existing_id": problem["id"],
                    "state": "solved",
                    "task_ids": [t2["id"]],
                    "attempts": [
                        {
                            "existing_id": attempt["id"],
                            "outcome": "failed",
                            "why": "not the cause",
                            "invalidated_on": "2026-09-01",
                            "task_ids": [t2["id"]],
                        },
                        {
                            "description": "add the env var",
                            "outcome": "worked",
                            "task_ids": [t2["id"]],
                        },
                    ],
                }
            ],
            "milestones": [],
        }
    )
    history.sync(config, REPO, run=fake)

    assert f'<task id="{t1["id"]}"' not in fake.calls[0]["input"]
    assert f'"existing_id": {attempt["id"]}' in fake.calls[0]["input"]
    [problem] = store.problems(REPO)
    assert problem["state"] == "solved"
    assert [(a["description"], a["outcome"]) for a in problem["attempts"]] == [
        ("pin node 20", "failed"),
        ("add the env var", "worked"),
    ]
    assert problem["task_ids"] == [t1["id"], t2["id"]]


def test_updates_cannot_touch_another_repos_records(store, config):
    other = store.add_problem(cwd="/elsewhere", title="theirs", task_ids=[])
    _task(store, "a", "b")
    history.sync(
        config,
        REPO,
        run=FakeClaude(
            {
                "problems": [{"existing_id": other, "title": "hijacked"}],
                "milestones": [],
            }
        ),
    )
    assert store.get_problem(other)["title"] == "theirs"


def test_ids_the_model_invents_for_new_records_do_not_drop_them(store, config):
    t = _task(store, "a", "the cache key ignored the locale")
    history.sync(config, REPO, run=FakeClaude({
        "problems": [{"existing_id": 1, "title": "Stale cache", "task_ids": [t["id"]],
                      "attempts": [{"existing_id": 1, "description": "key by locale",
                                    "outcome": "worked", "task_ids": [t["id"]]}]}],
        "milestones": [{"existing_id": 2, "title": "Cache layer", "task_ids": [t["id"]]}],
    }))
    [problem] = store.problems(REPO)
    assert [a["description"] for a in problem["attempts"]] == ["key by locale"]
    assert [m["title"] for m in store.milestones(REPO)] == ["Cache layer"]


def test_commits_must_come_from_the_git_log_shown(store, config):
    t = _task(store, "a", "b")
    batch = history._Batch([t], "abc1234 2026-09-01 fix login\n")
    assert batch.commits(["abc1234", "deadbee", "abc12"]) == ["abc1234"]


def test_sync_reads_in_batches_and_stops_at_the_cap(store, config, monkeypatch):
    monkeypatch.setattr(history, "BATCH_TASKS", 2)
    config = config.__class__(**{**config.__dict__, "history_max_batches": 2})
    for i in range(5):
        _task(store, f"t{i}", "ok")
    fake = FakeClaude()
    summary = history.sync(config, REPO, run=fake)
    assert len(fake.calls) == 2
    assert (summary["tasks"], summary["pending"]) == (4, 1)


def test_failed_batch_keeps_earlier_batches_and_records_the_error(store, config, monkeypatch):
    monkeypatch.setattr(history, "BATCH_TASKS", 1)
    first = _task(store, "one", "ok")
    _task(store, "two", "ok")
    fake = FakeClaude(
        {"problems": [], "milestones": [{"title": "One", "task_ids": [first["id"]]}]},
        OSError("claude not found"),
    )
    summary = history.sync(config, REPO, run=fake)
    assert summary["added"] == 1
    assert "claude not found" in summary["error"]
    assert store.history_sync(REPO)["state"] == "idle"
    assert store.history_cursor("/p") == first["id"]


def test_sync_refuses_unknown_models_and_a_second_run(store, config):
    _task(store, "a", "b")
    for model in ("gpt-4", "haiku"):
        with pytest.raises(history.SyncError):
            history.sync(config, REPO, model=model, run=FakeClaude())
    store.begin_history_sync(REPO, "sonnet", stale_after_s=60)
    with pytest.raises(history.SyncError):
        history.sync(config, REPO, run=FakeClaude())


# -- search ---------------------------------------------------------------------------


def test_search_history_is_accent_insensitive_and_surfaces_the_problem(store):
    _task(store, "a", "b")
    problem_id = store.add_problem(cwd="/p", title="Catálogo lento", task_ids=[])
    store.add_attempt(problem_id, description="índice en mongo", outcome="failed", task_ids=[])
    found = store.search_history("indice MONGO", REPO)
    assert [p["id"] for p in found["problems"]] == [problem_id]
    assert (
        store.search_history("catalogo", None)["problems"][0]["attempts"][0]["outcome"] == "failed"
    )
    assert store.search_history("catalogo", "path:/elsewhere")["problems"] == []


# -- migration from 0.5.0 -----------------------------------------------------------------


def test_v3_records_become_problems_attempts_and_milestones(config):
    config.home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.executescript("""
        CREATE TABLE insights (id INTEGER PRIMARY KEY, cwd TEXT, kind TEXT, title TEXT,
          detail TEXT, happened_on TEXT, state TEXT, solution TEXT, task_ids TEXT,
          created_at TEXT, updated_at TEXT);
        CREATE TABLE insight_syncs (cwd TEXT PRIMARY KEY, last_task_id INTEGER, state TEXT,
          started_at TEXT, finished_at TEXT, error TEXT, last_cost_usd REAL, total_cost_usd REAL);
        INSERT INTO insights VALUES
          (1, '/p', 'milestone', 'Shipped', 'd', '2026-09-01', NULL, NULL, '[1]', '', ''),
          (2, '/p', 'dead_end', 'Retry', 'cookie', '2026-09-02', NULL, 'cookie domain', '[2]',
           '', ''),
          (3, '/p', 'problem', 'Slow', 'no index', '2026-09-03', 'open', NULL, '[3]', '', '');
        INSERT INTO insight_syncs VALUES ('/p', 23, 'idle', NULL, NULL, NULL, 0, 0);
        PRAGMA user_version = 3;
    """)
    conn.close()
    with Store.open(config) as store:
        assert [m["title"] for m in store.milestones(None)] == ["Shipped"]
        problems = {p["title"]: p for p in store.problems(None)}
        assert [(a["description"], a["outcome"]) for a in problems["Retry"]["attempts"]] == [
            ("Retry", "failed"),
            ("cookie domain", "worked"),
        ]
        assert problems["Slow"]["cause"] == "no index" and problems["Slow"]["attempts"] == []
        assert store.history_cursor("/p") == 23
        assert store.search_history("cookie", None)["problems"]


# -- hooks ------------------------------------------------------------------------------


def _start(cwd="/p"):
    return {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": cwd, "source": "startup"}


def test_session_start_warns_about_the_repos_failed_attempts(store, config):
    store.set_repo("/p/main", REPO, "app", now_iso())
    problem_id = store.add_problem(cwd="/p/main", title="Login fails", task_ids=[])
    store.add_attempt(
        problem_id,
        description="retry",
        outcome="failed",
        why="cookie",
        invalidated_on="2026-09-03",
        task_ids=[],
    )
    store.add_attempt(problem_id, description="cookie domain", outcome="worked", task_ids=[])
    context = handle(_start("/p/worktree"), store, config, {})["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "Login fails: tried retry (2026-09-03); failed because cookie" in context
    assert "what worked: cookie domain" in context


def test_session_start_without_failed_attempts_adds_nothing(store, config):
    assert handle(_start(), store, config, {}) is None
    problem_id = store.add_problem(cwd="/q", title="x", task_ids=[])
    store.add_attempt(problem_id, description="y", outcome="failed", task_ids=[])
    assert handle(_start(), store, config, {}) is None  # another repo's dead end
    off = config.__class__(**{**config.__dict__, "dead_end_items": 0})
    assert handle(_start("/q"), store, off, {}) is None


def test_hooks_off_records_nothing(config):
    event = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "cwd": "/p",
        "prompt": "summarise",
    }
    env = {"TASKY_HOME": str(config.home), "TASKY_HOOKS_OFF": "1"}
    assert main(io.StringIO(json.dumps(event)), io.StringIO(), env) == 0
    with Store.open(config) as store:
        assert store.list_tasks() == []


# -- MCP ---------------------------------------------------------------------------------


def _rpc(config, lines):
    out = io.StringIO()
    mcp.serve(config, io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n"), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _call(config, name, **arguments):
    [reply] = _rpc(
        config,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ],
    )
    return reply["result"]


def test_mcp_handshake_and_tool_list(config):
    replies = _rpc(
        config,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "nope"},
        ],
    )
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["serverInfo"]["name"] == "tasky"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert names == {
        "search_history", "get_problem", "dead_ends", "search_tasks", "record_attempt",
        "search_conversations", "last_session", "get_architecture",
    }
    assert replies[2]["error"]["code"] == -32601


def test_mcp_record_attempt_builds_a_chain_the_search_finds(config, monkeypatch):
    monkeypatch.chdir("/")
    monkeypatch.setattr(mcp.os, "getcwd", lambda: "/p")
    first = _call(
        config,
        "record_attempt",
        problem_title="Flaky e2e test",
        description="raise the timeout",
        outcome="failed",
        why="still flaky",
    )
    assert first["isError"] is False
    with Store.open(config) as store:
        [problem] = store.problems(None)
    attempt_id = problem["attempts"][0]["id"]
    second = _call(
        config,
        "record_attempt",
        problem_id=problem["id"],
        description="wait for the network idle event",
        outcome="worked",
    )
    assert "2. ✓ worked: wait for the network idle event" in second["content"][0]["text"]
    found = _call(config, "search_history", query="flaky")["content"][0]["text"]
    assert "1. ✗ failed: raise the timeout | why: still flaky" in found
    assert f"attempt {attempt_id}, by agent" in found
    assert "[solved]" in found
    assert "raise the timeout" in _call(config, "dead_ends")["content"][0]["text"]


def test_mcp_tool_errors_are_reported_not_raised(config):
    result = _call(config, "get_problem", id=12345)
    assert result["isError"] is True
    assert _call(config, "record_attempt", description="x", outcome="meh")["isError"] is True
