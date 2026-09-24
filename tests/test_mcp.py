"""Every MCP tool, called the way Claude Code calls it: JSON-RPC lines on stdio."""

from __future__ import annotations

import io
import json

import pytest

from tasky import mcp, repos, transcripts
from tasky.store import Store

REPO = "github.com/o/app"


@pytest.fixture(autouse=True)
def _in_repo(monkeypatch):
    monkeypatch.setattr(
        repos, "resolve",
        lambda cwd: (REPO, "app") if cwd.startswith("/p") else (f"path:{cwd}", cwd),
    )
    monkeypatch.setattr(mcp.os, "getcwd", lambda: "/p")


def _call(config, name, **arguments):
    out = io.StringIO()
    request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    mcp.serve(config, io.StringIO(json.dumps(request) + "\n"), out)
    [reply] = [json.loads(line) for line in out.getvalue().splitlines()]
    assert reply["id"] == 7
    result = reply["result"]
    return result["isError"], result["content"][0]["text"]


@pytest.fixture
def ledger(config, tmp_path):
    """Two repositories with tasks, a problem chain, messages and a recap."""
    log = tmp_path / "s1.jsonl"
    log.write_text(
        "".join(
            json.dumps(e) + "\n"
            for e in (
                {"type": "user", "uuid": "u1", "sessionId": "s1", "cwd": "/p",
                 "promptId": "p1", "message": {"role": "user", "content": "cookie domain bug"}},
                {"type": "assistant", "uuid": "a1", "sessionId": "s1", "cwd": "/p",
                 "message": {"id": "m1", "role": "assistant", "content": [
                     {"type": "text", "text": "The cookie domain lacks the leading dot."}]}},
                {"type": "system", "subtype": "away_summary", "uuid": "r1", "sessionId": "s1",
                 "cwd": "/p", "timestamp": "2026-09-01T12:00:00.000Z",
                 "content": "Fixing the login cookie. Next: deploy."},
            )
        )
    )
    with Store.open(config) as store:
        store.upsert_session("s1", cwd="/p", transcript_path=str(log))
        store.upsert_session("s2", cwd="/other")
        task = store.create_task(kind="prompt", body="fix the login cookie", status="done",
                                 source="hook", cwd="/p", session_id="s1", prompt_id="p1",
                                 result="Set domain to .example.com")
        store.add_followup(task["id"], "and add a regression test")
        store.create_task(kind="prompt", body="login page copy", status="done", source="hook",
                          cwd="/other", session_id="s2", result="Changed the heading")
        transcripts.ingest_file(store, log)
        repos.ensure(store, ["/p", "/other"])
        problem = store.add_problem(cwd="/p", title="Login loses the session", symptom="401",
                                    first_seen="2026-09-01", last_seen="2026-09-01",
                                    task_ids=[task["id"]])
        store.add_attempt(problem, description="extend the token lifetime", outcome="failed",
                          why="the cookie was never sent", evidence="", believed_from="2026-09-01",
                          invalidated_on="2026-09-01", task_ids=[task["id"]], commits=[],
                          source="sync")
        store.add_attempt(problem, description="set the cookie domain", outcome="worked",
                          why="", evidence="", believed_from="2026-09-01", invalidated_on=None,
                          task_ids=[task["id"]], commits=[], source="sync")
        store.update_problem(problem, state="solved")
        other = store.add_problem(cwd="/other", title="Other repo problem", symptom="",
                                  first_seen="2026-09-01", last_seen="2026-09-01", task_ids=[])
        store.add_attempt(other, description="restart the pod", outcome="failed", why="no",
                          evidence="", believed_from=None, invalidated_on=None, task_ids=[],
                          commits=[], source="sync")
    return {"problem": problem, "task": task}


def test_search_history_finds_the_chain_in_this_repo_only(config, ledger):
    error, text = _call(config, "search_history", query="login session")
    assert not error
    assert "Login loses the session" in text
    assert "✗ failed: extend the token lifetime | why: the cookie was never sent" in text
    assert "✓ worked: set the cookie domain" in text
    assert "Other repo problem" not in _call(config, "search_history", query="restart")[1]
    assert "Other repo problem" in _call(config, "search_history", query="restart",
                                         scope="all")[1]


def test_get_problem(config, ledger):
    error, text = _call(config, "get_problem", id=ledger["problem"])
    assert not error and text.startswith(f"#{ledger['problem']} [solved] Login loses the session")
    assert _call(config, "get_problem", id=999)[0] is True
    assert _call(config, "get_problem", id="1")[0] is True


def test_dead_ends(config, ledger):
    error, text = _call(config, "dead_ends")
    assert not error
    assert "extend the token lifetime | why: the cookie was never sent" in text
    assert "what worked: set the cookie domain" in text
    assert "restart the pod" not in text
    assert "restart the pod" in _call(config, "dead_ends", scope="all", limit=50)[1]


def test_search_tasks_includes_followups_and_respects_scope(config, ledger):
    error, text = _call(config, "search_tasks", query="login")
    assert not error
    assert "fix the login cookie" in text and "also asked: and add a regression test" in text
    assert "login page copy" not in text
    assert "login page copy" in _call(config, "search_tasks", query="login", scope="all")[1]
    assert _call(config, "search_tasks", query="regression")[1].startswith("task #")
    assert _call(config, "search_tasks", query="")[0] is True


def test_search_conversations(config, ledger):
    error, text = _call(config, "search_conversations", query="leading dot")
    assert not error and "The cookie domain lacks the leading dot." in text
    assert _call(config, "search_conversations", query="zzzz")[1] == "No matching messages."


def test_last_session_shows_tasks_and_the_latest_recap(config, ledger):
    error, text = _call(config, "last_session")
    assert not error
    assert "fix the login cookie" in text
    assert "latest recap (2026-09-01T12:00): Fixing the login cookie. Next: deploy." in text
    assert "login page copy" in _call(config, "last_session", scope="all", count=5)[1]


def test_record_attempt_both_ways_and_its_errors(config, ledger):
    error, text = _call(config, "record_attempt", problem_id=ledger["problem"],
                        description="rotate the signing key", outcome="pending")
    assert not error and "rotate the signing key" in text
    with Store.open(config) as store:
        pending = store.get_problem(ledger["problem"])["attempts"][-1]
    error, text = _call(config, "record_attempt", problem_id=ledger["problem"],
                        description="pin the cookie path", outcome="worked",
                        failed_attempt_id=pending["id"], failed_because="keys were fine")
    assert not error
    with Store.open(config) as store:
        chain = store.get_problem(ledger["problem"])["attempts"]
    assert [a["outcome"] for a in chain][-2:] == ["failed", "worked"]
    assert chain[-2]["why"] == "keys were fine" and chain[-1]["source"] == "agent"

    error, _ = _call(config, "record_attempt", problem_title="New flaky test",
                     description="retry twice", outcome="failed", why="still flaky")
    assert not error
    assert "New flaky test" in _call(config, "search_history", query="flaky")[1]

    assert _call(config, "record_attempt", description="x", outcome="meh")[0] is True
    assert _call(config, "record_attempt", description="x", outcome="worked")[0] is True
    assert _call(config, "record_attempt", problem_id=999, description="x",
                 outcome="worked")[0] is True
    assert _call(config, "record_attempt", problem_id=ledger["problem"], description="x",
                 outcome="worked", failed_attempt_id=999999)[0] is True


def test_unknown_tool_is_an_error_result(config):
    assert _call(config, "nope")[0] is True
