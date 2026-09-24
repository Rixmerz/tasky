import json

import pytest

from tasky import history, mcp, repos, transcripts
from tasky.hooks import handle
from tasky.store import Store

REPO = "github.com/o/app"


@pytest.fixture(autouse=True)
def _fixed_repo(monkeypatch):
    monkeypatch.setattr(
        repos,
        "resolve",
        lambda cwd: (REPO, "app") if cwd.startswith("/p") else (f"path:{cwd}", cwd),
    )


def _line(**entry):
    return json.dumps(entry) + "\n"


def _user(uuid, text, prompt_id="p1", **extra):
    return _line(
        type="user",
        uuid=uuid,
        sessionId="s1",
        cwd="/p",
        promptId=prompt_id,
        timestamp="2026-09-01T10:00:00.000Z",
        message={"role": "user", "content": text},
        **extra,
    )


def _assistant(uuid, blocks, prompt_id="p1"):
    return _line(
        type="assistant",
        uuid=uuid,
        sessionId="s1",
        cwd="/p",
        promptId=prompt_id,
        timestamp="2026-09-01T10:00:05.000Z",
        message={"role": "assistant", "content": blocks},
    )


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text(
        _line(type="ai-title", aiTitle="Fix login", sessionId="s1")
        + _user("u1", "login fails with 401 <system-reminder>noise</system-reminder>")
        + _assistant(
            "a1",
            [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "Checking the cookie domain."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Edit",
                    "input": {
                        "file_path": "/p/src/auth/cookie.py",
                        "old_string": "a",
                        "new_string": "domain = '.example.com'",
                    },
                },
            ],
        )
        + _line(
            type="user",
            uuid="u2",
            sessionId="s1",
            cwd="/p",
            promptId="p1",
            message={
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "ok, token ghp_" + "a" * 36,
                    }
                ],
            },
        )
    )
    return path


def test_ingest_copies_text_tool_calls_and_results(store, transcript):
    assert transcripts.ingest_file(store, transcript) == 4
    rows = store._conn.execute(
        "SELECT kind, role, tool_name, file_path, text FROM messages ORDER BY id"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("text", "user"),
        ("text", "assistant"),
        ("tool_use", "assistant"),
        ("tool_result", "user"),
    ]
    assert rows[0][4] == "login fails with 401"  # system reminder stripped
    assert rows[2][2:4] == ("Edit", "/p/src/auth/cookie.py")
    assert "ghp_" not in rows[3][4] and "[redacted]" in rows[3][4]
    assert store.files_touched("p1") == ["/p/src/auth/cookie.py"]


def test_ingest_is_incremental_and_waits_for_complete_lines(store, transcript):
    transcripts.ingest_file(store, transcript)
    assert transcripts.ingest_file(store, transcript) == 0
    with transcript.open("a") as handle:
        handle.write(_user("u3", "next question", prompt_id="p2")[:-10])  # partial line
    assert transcripts.ingest_file(store, transcript) == 0
    with transcript.open("a") as handle:
        handle.write(_user("u3", "next question", prompt_id="p2")[-10:])
    assert transcripts.ingest_file(store, transcript) == 1
    assert store.count_messages("s1") == 5


def test_rewritten_transcript_is_reread_without_duplicates(store, transcript):
    transcripts.ingest_file(store, transcript)
    transcript.write_text(_user("u1", "login fails with 401"))
    assert transcripts.ingest_file(store, transcript) == 0
    assert store.count_messages() == 4


def test_budget_stops_early_and_resumes(store, transcript):
    size = transcript.stat().st_size
    first = transcripts.ingest_file(store, transcript, budget=size // 2)
    rest = transcripts.ingest_file(store, transcript, budget=size)
    assert first + rest == 4


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-" + "x" * 30,
        "AKIAABCDEFGHIJKLMNOP",
        "password=hunter22222",
        "Bearer " + "y" * 30,
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_scrub_hides_secrets(secret):
    assert secret not in transcripts.scrub(f"before {secret} after")


def test_search_messages_finds_words_with_context(store, transcript):
    transcripts.ingest_file(store, transcript)
    hits = store.search_messages("cookie domain", None)
    assert {h["kind"] for h in hits} == {"text", "tool_use"}  # the edit's path matches too
    hit = next(h for h in hits if h["kind"] == "text")
    assert hit["text"] == "Checking the cookie domain."
    assert any(c["text"].startswith("login fails") for c in hit["context"])
    assert store.search_messages("cookie", ["/elsewhere"]) == []


def test_stop_hook_copies_the_transcript_and_session_end_stays_ended(store, config, transcript):
    base = {"session_id": "s1", "cwd": "/p", "transcript_path": str(transcript)}
    handle(
        {**base, "hook_event_name": "UserPromptSubmit", "prompt": "login fails"}, store, config, {}
    )
    handle({**base, "hook_event_name": "Stop", "last_assistant_message": "done"}, store, config, {})
    assert store.count_messages("s1") == 4
    handle({**base, "hook_event_name": "SessionEnd"}, store, config, {})
    assert store.get_session("s1")["state"] == "ended"


def test_mcp_search_conversations_and_last_session(store, config, transcript, monkeypatch):
    store.upsert_session("s1", cwd="/p", transcript_path=str(transcript))
    store.update_session("s1", title="Fix login", compact_prompt="Keep the cookie fix.")
    store.create_task(
        kind="prompt",
        body="login fails",
        status="done",
        source="hook",
        cwd="/p",
        session_id="s1",
        result="Fixed the cookie domain.",
    )
    transcripts.ingest_file(store, transcript)
    repos.ensure(store, ["/p"])
    monkeypatch.setattr(mcp.os, "getcwd", lambda: "/p")
    found = mcp.call_tool(store, "search_conversations", {"query": "cookie"})
    assert "[session Fix login 2026-09-01]" in found and "Edit /p/src/auth/cookie.py" in found
    last = mcp.call_tool(store, "last_session", {})
    assert "Fixed the cookie domain." in last and "suggested /compact: Keep the cookie fix." in last


# -- /compact suggestions ----------------------------------------------------------


def test_sync_stores_compact_instructions_only_for_open_interactive_sessions(store, config):
    import subprocess

    for sid, source in (("live", "hook"), ("worker", "worker"), ("gone", "hook")):
        store.upsert_session(sid, cwd="/p", source=source)
        store.create_task(
            kind="prompt",
            body=f"task of {sid}",
            status="done",
            source="hook",
            cwd="/p",
            session_id=sid,
            result="ok",
        )
    store.update_session("gone", state="ended")
    repos.ensure(store, ["/p"])
    output = {
        "problems": [],
        "milestones": [],
        "compact": [
            {"session_id": sid, "instructions": f"Keep {sid}"} for sid in ("live", "worker", "gone")
        ],
    }
    captured = {}

    def fake(cmd, *, input, **kwargs):
        captured["input"] = input
        reply = {"is_error": False, "total_cost_usd": 0, "structured_output": output}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")

    summary = history.sync(config, REPO, run=fake)

    assert summary["compact"] == 1
    assert '<session id="live"' in captured["input"]
    assert '<session id="worker"' not in captured["input"]
    assert store.get_session("live")["compact_prompt"] == "Keep live"
    assert store.get_session("worker")["compact_prompt"] is None
    assert store.get_session("gone")["compact_prompt"] is None


def test_v4_database_gains_compact_columns_and_message_tables(config):
    import sqlite3

    config.home.mkdir(parents=True, exist_ok=True)
    with Store.open(config) as store:
        store.upsert_session("s1", cwd="/p")
    conn = sqlite3.connect(config.db_path)
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    with Store.open(config) as store:
        store.update_session("s1", compact_prompt="x")
        assert store.get_session("s1")["compact_prompt"] == "x"
        assert store.count_messages() == 0
