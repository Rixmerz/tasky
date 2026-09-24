"""0.8.0: one task per turn, cancelled prompts, tokens, recaps and smart search."""

from __future__ import annotations

import json
import subprocess

import pytest

from tasky import __version__, history, smart_search, transcripts
from tasky.hooks import handle
from tasky.store import Store


def _line(**entry) -> str:
    return json.dumps(entry) + "\n"


def _user(uuid, text, prompt_id, **extra) -> str:
    return _line(
        type="user", uuid=uuid, sessionId="s1", cwd="/w", promptId=prompt_id,
        timestamp="2026-09-01T10:00:00.000Z", message={"role": "user", "content": text}, **extra,
    )


def _assistant(uuid, blocks, *, message_id="m1", usage=None, session="s1", sidechain=False):
    message = {"id": message_id, "role": "assistant", "model": "claude-opus-5", "content": blocks}
    if usage is not None:
        message["usage"] = usage
    return _line(
        type="assistant", uuid=uuid, sessionId=session, cwd="/w", isSidechain=sidechain,
        timestamp="2026-09-01T10:00:05.000Z", message=message,
    )


def _text(text):
    return [{"type": "text", "text": text}]


def _edit(path):
    return [{"type": "tool_use", "id": "t", "name": "Edit",
             "input": {"file_path": path, "old_string": "a", "new_string": "b"}}]


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text("")
    return path


def _event(name, log, **fields):
    return {"hook_event_name": name, "session_id": "s1", "cwd": "/w",
            "transcript_path": str(log), **fields}


def _submit(store, config, log, prompt_id, prompt, **fields):
    return handle(
        _event("UserPromptSubmit", log, prompt_id=prompt_id, prompt=prompt, **fields),
        store, config, {},
    )


def _append(log, *lines):
    with log.open("a") as fh:
        fh.write("".join(lines))


def _prompts(store):
    return store.list_tasks(kind="prompt")


# -- a prompt cancelled before any reply ------------------------------------------------


def test_prompt_cancelled_before_a_reply_is_dropped_whatever_comes_next(store, config, log):
    _append(log, _user("u1", "how do we improve the board?", "p1"))
    _submit(store, config, log, "p1", "how do we improve the board?")
    # Esc before any reply, then a much longer prompt: nothing like the first text.
    _append(log, _user("u2", "how do we improve the board? also the cancel bug", "p2"))
    _submit(store, config, log, "p2", "list the cancel bugs first, then the layout issues")

    assert [t["prompt_id"] for t in _prompts(store)] == ["p2"]


def test_prompt_cancelled_after_claude_started_working_is_kept(store, config, log):
    _append(log, _user("u1", "migrate invoices", "p1"),
            _assistant("a1", _edit("/w/db.sql")),
            _user("u1b", "[Request interrupted by user for tool use]", "p1"))
    _submit(store, config, log, "p1", "migrate invoices")
    _append(log, _user("u2", "migrate invoices", "p2"))
    _submit(store, config, log, "p2", "migrate invoices")

    assert [t["prompt_id"] for t in _prompts(store)] == ["p2", "p1"]
    handle(_event("Stop", log, prompt_id="p2", last_assistant_message="done"), store, config, {})
    statuses = {t["prompt_id"]: t["status"] for t in _prompts(store)}
    assert statuses == {"p1": "interrupted", "p2": "done"}


def test_thinking_alone_is_not_a_reply(store, config, log):
    _append(log, _user("u1", "plan the release", "p1"),
            _assistant("a1", [{"type": "thinking", "thinking": "hmm"}]))
    _submit(store, config, log, "p1", "plan the release")
    _submit(store, config, log, "p2", "something else entirely")

    assert [t["prompt_id"] for t in _prompts(store)] == ["p2"]


def test_without_a_readable_transcript_the_text_comparison_decides(store, config, tmp_path):
    missing = tmp_path / "gone.jsonl"
    _submit(store, config, missing, "p1", "explain the retry policy")
    _submit(store, config, missing, "p2", "what is the weather like")

    assert len(_prompts(store)) == 2


def test_extended_resend_counts_as_the_same_request_in_the_fallback(store, config, tmp_path):
    missing = tmp_path / "gone.jsonl"
    _submit(store, config, missing, "p1", "how do we improve the board?")
    _submit(store, config, missing, "p2", "how do we improve the board? and fix the cancel bug")

    assert [t["prompt_id"] for t in _prompts(store)] == ["p2"]


def test_session_closed_right_after_a_cancel_drops_the_prompt(store, config, log):
    _append(log, _user("u1", "one", "p1"), _assistant("a1", _text("ok")))
    _submit(store, config, log, "p1", "first request here")
    handle(_event("Stop", log, prompt_id="p1", last_assistant_message="ok"), store, config, {})
    _append(log, _user("u2", "second", "p2"))
    _submit(store, config, log, "p2", "second request here")
    handle(_event("SessionEnd", log), store, config, {})

    assert [t["prompt_id"] for t in _prompts(store)] == ["p1"]


def test_session_end_keeps_a_turn_that_had_started(store, config, log):
    _append(log, _user("u1", "long job", "p1"), _assistant("a1", _edit("/w/x.py")))
    _submit(store, config, log, "p1", "long job")
    handle(_event("SessionEnd", log), store, config, {})

    [task] = _prompts(store)
    assert task["status"] == "interrupted"


# -- messages typed while Claude works -------------------------------------------------


def test_message_typed_mid_turn_joins_the_running_task(store, config, log):
    _submit(store, config, log, "p1", "build the favicon")
    _submit(store, config, log, "p1", "also update the changelog")
    handle(_event("Stop", log, prompt_id="p1", last_assistant_message="done both"),
           store, config, {})

    [task] = _prompts(store)
    assert task["body"] == "build the favicon"
    assert [f["text"] for f in task["followups"]] == ["also update the changelog"]
    assert task["status"] == "done"
    assert task["result"] == "done both"


def test_edited_mid_turn_message_replaces_its_first_copy(store, config, log):
    _submit(store, config, log, "p1", "build the favicon")
    _submit(store, config, log, "p1", "credit the projects tasky builds on")
    _submit(store, config, log, "p1", "credit the projects tasky builds on and add the license")

    [task] = _prompts(store)
    assert [f["text"] for f in task["followups"]] == [
        "credit the projects tasky builds on and add the license"
    ]


def test_followups_are_searchable(store, config, log):
    _submit(store, config, log, "p1", "build the favicon")
    _submit(store, config, log, "p1", "añade la licencia MIT")

    assert [t["prompt_id"] for t in store.search_tasks("licencia")] == ["p1"]


# -- prompts that are not the user's ------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [{"agent_id": "a1"}, {"transcript_path": "/x/s1/subagents/agent-a1.jsonl"}],
)
def test_subagent_prompts_are_not_tasks(store, config, log, fields):
    handle(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/w",
         "transcript_path": str(log), "prompt_id": "p1", "prompt": "You are a reviewer", **fields},
        store, config, {},
    )
    assert _prompts(store) == []
    session = store.get_session("s1")
    assert session is None or "subagents" not in (session["transcript_path"] or "")


def test_agent_messages_are_not_tasks(store, config, log):
    _submit(store, config, log, "p1", '<agent-message from="a6d6">done</agent-message>')
    assert _prompts(store) == []


def test_subagent_prompt_in_the_running_turn_is_not_a_followup(store, config, log):
    prompt = "You are implementing the dashboard UI for a feature of tasky"
    _append(log, _user("u1", "do it", "p1"),
            _assistant("a1", [{"type": "tool_use", "id": "t", "name": "Agent",
                               "input": {"description": "ui", "prompt": prompt}}]))
    _submit(store, config, log, "p1", "do it")
    _submit(store, config, log, "p1", prompt)

    [task] = _prompts(store)
    assert task["followups"] == []


def test_hooks_record_their_version_on_the_session(store, config, log):
    _submit(store, config, log, "p1", "hello there, a task")
    assert store.get_session("s1")["hook_version"] == __version__


# -- transcripts: turns, tokens, subagents, recaps -----------------------------------------


def test_assistant_messages_take_the_prompt_id_across_incremental_reads(store, log):
    _append(log, _user("u1", "fix it", "p1"), _assistant("a1", _edit("/w/a.py"), message_id="m1"))
    transcripts.ingest_file(store, log)
    _append(log, _assistant("a2", _edit("/w/b.py"), message_id="m2"))
    transcripts.ingest_file(store, log)

    assert store.edited_files("p1") == ["/w/a.py", "/w/b.py"]


def test_usage_is_counted_once_per_api_message(store, log):
    usage = {"input_tokens": 3, "output_tokens": 100, "cache_read_input_tokens": 5000,
             "cache_creation_input_tokens": 200}
    _append(
        log,
        _user("u1", "fix it", "p1"),
        # One API message written as two transcript lines, each with the same usage.
        _assistant("a1", _text("looking"), message_id="m1", usage=usage),
        _assistant("a2", _edit("/w/a.py"), message_id="m1", usage=usage),
    )
    transcripts.ingest_file(store, log)
    transcripts.ingest_file(store, log)

    stats = store.task_stats(["p1"])["p1"]
    assert stats["tokens"] == {"input": 3, "output": 100, "cache_read": 5000, "cache_write": 200}
    assert stats["files"] == 1 and stats["tools"] == 1


def test_subagent_transcripts_count_for_the_parent_turn(store, log):
    _append(log, _user("u1", "delegate", "p1"))
    sub = log.parent / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.jsonl").write_text(
        _line(type="user", uuid="su1", sessionId="s1", isSidechain=True, promptId="p1",
              agentId="a1", message={"role": "user", "content": "You are a helper"})
        + _assistant("sa1", _edit("/w/sub.py"), message_id="sm1", sidechain=True,
                     usage={"input_tokens": 1, "output_tokens": 40})
    )
    transcripts.ingest_path(store, log)

    assert store.edited_files("p1") == ["/w/sub.py"]
    assert store.task_stats(["p1"])["p1"]["tokens"]["output"] == 40
    assert store.session_tokens()["s1"]["output"] == 40


def test_queued_messages_and_recaps_are_copied(store, log):
    _append(
        log,
        _user("u1", "start", "p1"),
        _line(type="attachment", uuid="q1", sessionId="s1",
              attachment={"type": "queued_command", "prompt": "and the changelog",
                          "origin": {"kind": "human"}}),
        _line(type="system", subtype="away_summary", uuid="r1", sessionId="s1", cwd="/w",
              timestamp="2026-09-01T11:00:00.000Z",
              content="Building the favicon. Next: changelog. (disable recaps in /config)"),
    )
    transcripts.ingest_file(store, log)

    [recap] = store.recaps()
    assert recap["text"] == "Building the favicon. Next: changelog."
    assert recap["prompt_id"] == "p1"
    hits = store.search_messages("changelog", None)
    assert {h["kind"] for h in hits} >= {"text"}


def test_backfill_reads_subagent_transcripts(store, config):
    project = config.claude_config_dir / "projects" / "-w"
    (project / "s1" / "subagents").mkdir(parents=True)
    (project / "s1.jsonl").write_text(_user("u1", "hi", "p1"))
    (project / "s1" / "subagents" / "agent-a.jsonl").write_text(
        _assistant("sa1", _text("sub reply"), message_id="sm1", sidechain=True)
    )
    counts = transcripts.backfill(store, config)
    assert counts["files"] == 2 and counts["messages"] == 2


# -- repairing rows earlier versions wrote ---------------------------------------------


def _old_task(store, body, prompt_id, status="interrupted", result=None, session="s1"):
    return store.create_task(kind="prompt", body=body, status=status, source="hook",
                             session_id=session, prompt_id=prompt_id, result=result)


def test_repair_folds_a_split_turn_into_its_first_task(store):
    first = _old_task(store, "build the favicon", "p1")
    _old_task(store, "credit the projects tasky builds on", "p1")
    _old_task(store, "credit the projects tasky builds on and add the license", "p1",
              status="done", result="all done")
    _old_task(store, '<agent-message from="x">hi</agent-message>', "p1", status="done")

    counts = store.repair()

    [task] = _prompts(store)
    assert task["id"] == first["id"]
    assert task["status"] == "done" and task["result"] == "all done"
    assert [f["text"] for f in task["followups"]] == [
        "credit the projects tasky builds on and add the license"
    ]
    assert counts["folded"] == 1


def test_repair_drops_only_prompts_proved_unanswered(store, log):
    _append(
        log,
        _user("u1", "cancelled one", "p1"),
        _user("u2", "answered one", "p2"),
        _assistant("a2", _text("answer"), message_id="m2"),
        _user("u3", "last, still being copied", "p3"),
    )
    transcripts.ingest_file(store, log)
    _old_task(store, "cancelled one", "p1")
    _old_task(store, "answered one", "p2")
    _old_task(store, "last, still being copied", "p3", status="running")
    _old_task(store, "newest", "p4", status="running")

    store.repair()

    # p3 has no reply but also no later prompt copied: the proof is missing, keep it.
    assert sorted(t["prompt_id"] for t in _prompts(store)) == ["p2", "p3", "p4"]


def test_migration_from_v5_repairs_and_rereads_transcripts(tmp_path, config):
    import sqlite3

    with Store.open(config) as store:
        store.create_task(kind="prompt", body="a", status="interrupted", source="hook",
                          session_id="s1", prompt_id="p1")
        store.create_task(kind="prompt", body="b", status="done", source="hook",
                          session_id="s1", prompt_id="p1", result="r")
        store.set_transcript_offset("/x.jsonl", 10, 10)
    raw = sqlite3.connect(config.db_path)
    raw.execute("PRAGMA user_version = 5")
    raw.commit()
    raw.close()

    with Store.open(config) as store:
        [task] = store.list_tasks(kind="prompt")
        assert task["body"] == "a" and task["result"] == "r"
        assert store.transcript_offset("/x.jsonl") == (0, 0)


# -- dashboard state -------------------------------------------------------------------------


def test_state_marks_the_latest_task_and_ships_stats_recaps_and_tokens(store, log):
    _append(log, _user("u1", "one", "p1"),
            _assistant("a1", _edit("/w/a.py"), usage={"output_tokens": 7}),
            _line(type="system", subtype="away_summary", uuid="r1", sessionId="s1",
                  timestamp="2026-09-01T11:00:00.000Z", content="Recap text"))
    transcripts.ingest_file(store, log)
    store.upsert_session("s1", cwd="/w", hook_version="0.8.0")
    older = _old_task(store, "one", "p1", status="done", result="Want me to go on?")
    newer = _old_task(store, "two", "p2", status="done", result="done")

    state = store.state()

    by_id = {t["id"]: t for t in state["tasks"]}
    assert by_id[older["id"]]["latest_in_session"] is False
    assert by_id[newer["id"]]["latest_in_session"] is True
    assert by_id[older["id"]]["stats"]["files"] == 1
    assert by_id[older["id"]]["stats"]["tokens"]["output"] == 7
    assert state["recaps"][0]["text"] == "Recap text"
    [session] = state["sessions"]
    assert session["tokens"]["output"] == 7 and session["hook_version"] == "0.8.0"


# -- smart search --------------------------------------------------------------------------


def _fake_model(output, cost=0.004, calls=None):
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        reply = {"structured_output": output, "total_cost_usd": cost, "is_error": False}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")

    return run


def test_smart_search_returns_only_listed_tasks_with_reasons(store, config):
    login = store.create_task(kind="prompt", body="fix 401 on token refresh", status="done",
                              source="hook", result="Refresh now retries once.")
    store.create_task(kind="prompt", body="write the changelog", status="done", source="hook")
    calls: list = []
    run = _fake_model({"matches": [
        {"id": login["id"], "reason": "Es el arreglo del login."},
        {"id": 99999, "reason": "invented"},
        {"id": login["id"], "reason": "duplicate"},
    ]}, calls=calls)

    found = smart_search.search(config, store, "el bug del login", run=run)

    assert [r["task"]["id"] for r in found["results"]] == [login["id"]]
    assert found["results"][0]["reason"] == "Es el arreglo del login."
    assert found["cost_usd"] == 0.004 and found["model"] == "haiku" and found["scanned"] == 2
    cmd, kwargs = calls[0]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    system = cmd[cmd.index("--system-prompt") + 1]
    assert "fix 401 on token refresh" in system
    assert kwargs["input"].startswith("Question: el bug del login")
    assert kwargs["env"]["TASKY_HOOKS_OFF"] == "1"


def test_smart_search_reports_a_failed_model_call(store, config):
    store.create_task(kind="prompt", body="anything", status="done", source="hook")

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")

    with pytest.raises(smart_search.SmartSearchError, match="not logged in"):
        smart_search.search(config, store, "login", run=run)


def test_smart_search_on_an_empty_ledger_does_not_call_the_model(store, config):
    def run(cmd, **kwargs):
        raise AssertionError("no call expected")

    assert smart_search.search(config, store, "anything", run=run)["results"] == []


def test_history_call_model_keeps_its_own_prompt_by_default(config):
    calls: list = []
    history.call_model(config, "sonnet", "x", run=_fake_model({}, calls=calls))
    cmd = calls[0][0]
    assert cmd[cmd.index("--system-prompt") + 1] == history.SYSTEM_PROMPT


def test_repair_drops_recorded_session_commands_and_fixes_paste_titles(store):
    store.create_task(kind="prompt", body="/compact Keep: the roadmap", status="done",
                      source="import")
    kept = store.create_task(kind="prompt", body="/review 12", status="done", source="import")
    pasted = store.create_task(kind="prompt", status="done", source="import",
                               body='<pasted_content id="1">\nlog\n</pasted_content>\nwhy?',
                               title='<pasted_content id="1">')
    store.repair()
    tasks = {t["id"]: t for t in _prompts(store)}
    assert set(tasks) == {kept["id"], pasted["id"]}
    assert tasks[pasted["id"]]["title"] == "why?"


def test_subagent_transcripts_share_one_byte_budget(store, log):
    _append(log, _user("u1", "delegate", "p1"))
    sub = log.parent / "s1" / "subagents"
    sub.mkdir(parents=True)
    for name in ("a", "b", "c"):
        (sub / f"agent-{name}.jsonl").write_text(
            "".join(_assistant(f"{name}{i}", _text("x" * 200), message_id=f"{name}{i}",
                               sidechain=True) for i in range(5))
        )
    budget = log.stat().st_size + (sub / "agent-a.jsonl").stat().st_size
    transcripts.ingest_path(store, log, budget=budget)
    assert store.transcript_offset(str(sub / "agent-b.jsonl"))[0] == 0
    transcripts.ingest_path(store, log, budget=10**9)
    assert store.count_messages("s1") == 16


def test_repair_reads_command_markup_in_either_order(store):
    store.create_task(kind="prompt", status="done", source="import",
                      body="<command-name>/plugin</command-name> <command-message>plugin"
                           "</command-message> <command-args></command-args>")
    review = store.create_task(kind="prompt", status="done", source="import",
                               body="<command-name>/review</command-name> <command-message>review"
                                    "</command-message> <command-args>12</command-args>")
    store.repair()
    [task] = _prompts(store)
    assert task["id"] == review["id"] and task["title"] == "/review 12"


def test_mid_turn_message_with_the_next_turns_id_joins_the_running_task(store, config, log):
    # Claude Code sometimes gives a message typed mid-turn the id of the next turn.
    _append(log, _user("u1", "fix everything", "p1"), _assistant("a1", _edit("/w/a.py")))
    _submit(store, config, log, "p1", "fix everything")
    _submit(store, config, log, "p2", "also show the recaps big on the board")

    [task] = _prompts(store)
    assert task["prompt_id"] == "p1"
    assert [f["text"] for f in task["followups"]] == ["also show the recaps big on the board"]


def test_prompt_after_esc_mid_work_is_a_new_task(store, config, log):
    _append(
        log,
        _user("u1", "fix everything", "p1"),
        _assistant("a1", _edit("/w/a.py")),
        _user("u2", "[Request interrupted by user for tool use]", "p1"),
    )
    _submit(store, config, log, "p1", "fix everything")
    _submit(store, config, log, "p2", "do something else instead")

    assert sorted(t["prompt_id"] for t in _prompts(store)) == ["p1", "p2"]


def test_repair_folds_messages_absorbed_by_another_turn(store, log):
    text = "oye! claude tiene los famosos recap, muéstralos en grande"
    _append(
        log,
        _user("u1", "soluciona todos los problemas", "p1"),
        _assistant("a1", _edit("/w/a.py")),
        _line(type="attachment", uuid="q1", sessionId="s1",
              attachment={"type": "queued_command", "prompt": text,
                          "origin": {"kind": "human"}}),
    )
    transcripts.ingest_file(store, log)
    turn = _old_task(store, "soluciona todos los problemas", "p1", status="running")
    _old_task(store, text, "p9", status="running")

    store.repair()

    [task] = _prompts(store)
    assert task["id"] == turn["id"]
    assert [f["text"] for f in task["followups"]] == [text]


def test_repair_removes_agent_messages_recorded_as_tasks(store):
    _old_task(store, '<agent-message from="a1">report</agent-message>', "p1", status="running")
    store.repair()
    assert _prompts(store) == []


def test_repair_gives_the_turn_the_reply_its_absorbed_message_took(store, log):
    text = "and show the recaps big on the board please"
    _append(log, _user("u1", "fix it all", "p1"), _assistant("a1", _edit("/w/a.py")),
            _line(type="attachment", uuid="q1", sessionId="s1",
                  attachment={"type": "queued_command", "prompt": text}))
    transcripts.ingest_file(store, log)
    turn = _old_task(store, "fix it all", "p1")
    _old_task(store, text, "p2", status="running", result="All done.")

    store.repair()

    [task] = _prompts(store)
    assert task["id"] == turn["id"] and task["status"] == "done" and task["result"] == "All done."
