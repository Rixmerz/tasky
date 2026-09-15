import json
import os
import sqlite3

from tasky.importer import import_transcripts

PROJECT = "-work-app"
CWD = "/work/app"


def _write_transcript(config, session_id, entries, project=PROJECT):
    project_dir = config.claude_config_dir / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def _write_raw(config, session_id, lines, project=PROJECT):
    project_dir = config.claude_config_dir / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def user_entry(uuid, ts, text, session_id, cwd=CWD, **extra):
    entry = {
        "type": "user",
        "uuid": uuid,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": cwd,
        "message": {"role": "user", "content": text},
    }
    entry.update(extra)
    return entry


def assistant_text(uuid, ts, text, session_id, cwd=CWD, *, is_sidechain=False):
    entry = {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": cwd,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    if is_sidechain:
        entry["isSidechain"] = True
    return entry


def assistant_tool_use(uuid, ts, tool_id, name, tool_input, session_id, cwd=CWD):
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": cwd,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def notification_entry(uuid, ts, session_id, task_id, tool_use_id, status, result, cwd=CWD):
    text = (
        "<task-notification>\n"
        f"<task-id>{task_id}</task-id>\n"
        f"<tool-use-id>{tool_use_id}</tool-use-id>\n"
        f"<status>{status}</status>\n"
        "<summary>agent finished</summary>\n"
        f"<result>{result}</result>\n"
        "</task-notification>"
    )
    return user_entry(uuid, ts, text, session_id, cwd)


def meta_entry(uuid, ts, session_id, cwd=CWD):
    return user_entry(uuid, ts, "compaction housekeeping", session_id, cwd, isMeta=True)


def compact_summary_entry(uuid, ts, session_id, cwd=CWD):
    return user_entry(uuid, ts, "summary of prior turns", session_id, cwd, isCompactSummary=True)


def tool_result_entry(
    uuid, ts, session_id, tool_use_id, content="ok", cwd=CWD, *, is_error=False, is_async=False
):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    entry = user_entry(uuid, ts, [block], session_id, cwd)
    entry["toolUseResult"] = {"isAsync": True} if is_async else {"stdout": "ok"}
    return entry


def test_missing_projects_dir_returns_zeros(store, config):
    result = import_transcripts(store, config)

    assert result == {
        "files": 0,
        "sessions": 0,
        "tasks": 0,
        "skipped_sessions": 0,
        "bad_lines": 0,
    }


def test_import_a_transcript(store, config):
    session_id = "session-import-1"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
            user_entry("u2", "2026-01-01T00:01:00.000Z", "do task b", session_id),
            assistant_text("a2", "2026-01-01T00:01:05.000Z", "result b", session_id),
        ],
    )

    result = import_transcripts(store, config)

    assert result["files"] == 1
    assert result["sessions"] == 1
    assert result["tasks"] == 2
    assert result["skipped_sessions"] == 0
    assert result["bad_lines"] == 0

    tasks = sorted(
        store.list_tasks(session_id=session_id, kind="prompt"), key=lambda t: t["created_at"]
    )
    assert [t["status"] for t in tasks] == ["done", "done"]
    assert [t["result"] for t in tasks] == ["result a", "result b"]
    assert tasks[0]["created_at"] == "2026-01-01T00:00:00.000Z"
    assert tasks[0]["finished_at"] == "2026-01-01T00:00:05.000Z"
    assert tasks[0]["external_id"] == "import:u1"


def test_unanswered_last_prompt_is_interrupted(store, config):
    session_id = "session-import-2"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
            user_entry("u2", "2026-01-01T00:01:00.000Z", "do task b, never answered", session_id),
        ],
    )

    import_transcripts(store, config)

    tasks = sorted(
        store.list_tasks(session_id=session_id, kind="prompt"), key=lambda t: t["created_at"]
    )
    assert tasks[0]["status"] == "done"
    assert tasks[1]["status"] == "interrupted"
    assert tasks[1]["result"] is None


def test_meta_entries_skipped(store, config):
    session_id = "session-import-3"
    _write_transcript(
        config,
        session_id,
        [
            meta_entry("m1", "2026-01-01T00:00:00.000Z", session_id),
            compact_summary_entry("c1", "2026-01-01T00:00:01.000Z", session_id),
            tool_result_entry("t1", "2026-01-01T00:00:02.000Z", session_id, "toolu_x"),
            notification_entry(
                "n1", "2026-01-01T00:00:03.000Z", session_id, "a1", "toolu_missing",
                "completed", "done",
            ),
        ],
    )

    result = import_transcripts(store, config)

    assert result["sessions"] == 1
    assert result["tasks"] == 0
    assert store.list_tasks(session_id=session_id, kind="prompt") == []


def test_delegation_completed_from_notification(store, config):
    session_id = "session-import-4"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
            assistant_tool_use(
                "a1",
                "2026-01-01T00:00:01.000Z",
                "toolu_1",
                "Agent",
                {"description": "sub task", "prompt": "go do x", "subagent_type": "general"},
                session_id,
            ),
            notification_entry(
                "n1", "2026-01-01T00:00:05.000Z", session_id, "a1", "toolu_1",
                "completed", "four",
            ),
            assistant_text("a2", "2026-01-01T00:00:06.000Z", "wrapped up", session_id),
        ],
    )

    import_transcripts(store, config)

    delegation = store.get_task_by_external_id("toolu_1")
    assert delegation is not None
    assert delegation["kind"] == "delegation"
    assert delegation["status"] == "done"
    assert delegation["result"] == "four"
    assert delegation["title"] == "sub task"
    assert delegation["body"] == "go do x"

    prompt = store.get_task_by_external_id("import:u1")
    assert delegation["parent_id"] == prompt["id"]
    assert prompt["status"] == "done"
    assert prompt["result"] == "wrapped up"


def test_second_run_does_not_duplicate(store, config):
    session_id = "session-import-5"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    import_transcripts(store, config)
    first_count = len(store.list_tasks())

    second_result = import_transcripts(store, config)
    second_count = len(store.list_tasks())

    assert second_count == first_count
    assert second_result["tasks"] == 0


def test_session_captured_live_is_skipped(store, config):
    session_id = "session-import-6"
    store.upsert_session(session_id, source="hook")
    store.create_task(
        kind="prompt",
        body="live captured",
        status="done",
        source="hook",
        session_id=session_id,
    )
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    result = import_transcripts(store, config)

    assert result["skipped_sessions"] == 1
    assert result["tasks"] == 0
    tasks = store.list_tasks(session_id=session_id)
    assert len(tasks) == 1
    assert tasks[0]["source"] == "hook"


def test_corrupt_line_is_skipped_without_aborting(store, config):
    session_id = "session-import-7"
    valid_user = json.dumps(
        user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id)
    )
    valid_assistant = json.dumps(
        assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id)
    )
    _write_raw(config, session_id, [valid_user, "{not valid json", valid_assistant])

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 1
    tasks = store.list_tasks(session_id=session_id, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"
    assert tasks[0]["result"] == "result a"


def test_subagent_transcripts_are_ignored(store, config):
    session_id = "session-import-8"
    subagents_dir = (
        config.claude_config_dir / "projects" / PROJECT / session_id / "subagents"
    )
    subagents_dir.mkdir(parents=True, exist_ok=True)
    (subagents_dir / "sub1.jsonl").write_text(
        json.dumps(user_entry("su1", "2026-01-01T00:00:00.000Z", "sub prompt", session_id))
        + "\n",
        encoding="utf-8",
    )

    result = import_transcripts(store, config)

    assert result["files"] == 0
    assert result["sessions"] == 0
    assert store.list_tasks() == []


def test_dry_run_counts_without_writing(store, config):
    session_id = "session-import-9"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    result = import_transcripts(store, config, dry_run=True)

    assert result["files"] == 1
    assert result["sessions"] == 1
    assert result["tasks"] == 1
    assert store.list_sessions() == []
    assert store.list_tasks() == []


def test_foreground_delegation_closed_by_tool_result(store, config):
    """M1: a synchronous Agent call is closed by its matching tool_result, not left running."""
    session_id = "session-import-10"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
            assistant_tool_use(
                "a1",
                "2026-01-01T00:00:01.000Z",
                "toolu_fg",
                "Agent",
                {"description": "sub task", "prompt": "go do x", "subagent_type": "general"},
                session_id,
            ),
            tool_result_entry(
                "t1", "2026-01-01T00:00:02.000Z", session_id, "toolu_fg", "delegation output"
            ),
            assistant_text("a2", "2026-01-01T00:00:03.000Z", "wrapped up", session_id),
        ],
    )

    import_transcripts(store, config)

    delegation = store.get_task_by_external_id("toolu_fg")
    assert delegation["status"] == "done"
    assert delegation["result"] == "delegation output"


def test_foreground_delegation_error_result_marks_failed(store, config):
    session_id = "session-import-11"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
            assistant_tool_use(
                "a1",
                "2026-01-01T00:00:01.000Z",
                "toolu_fail",
                "Agent",
                {"description": "sub task", "prompt": "go do x", "subagent_type": "general"},
                session_id,
            ),
            tool_result_entry(
                "t1",
                "2026-01-01T00:00:02.000Z",
                session_id,
                "toolu_fail",
                "boom",
                is_error=True,
            ),
            assistant_text("a2", "2026-01-01T00:00:03.000Z", "wrapped up", session_id),
        ],
    )

    import_transcripts(store, config)

    delegation = store.get_task_by_external_id("toolu_fail")
    assert delegation["status"] == "failed"
    assert delegation["result"] == "boom"


def test_async_delegation_left_for_notification(store, config):
    """An async launch receipt (toolUseResult.isAsync) must not close the delegation;
    only the later <task-notification> does."""
    session_id = "session-import-12"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
            assistant_tool_use(
                "a1",
                "2026-01-01T00:00:01.000Z",
                "toolu_async",
                "Agent",
                {"description": "sub task", "prompt": "go do x", "subagent_type": "general"},
                session_id,
            ),
            tool_result_entry(
                "t1",
                "2026-01-01T00:00:02.000Z",
                session_id,
                "toolu_async",
                "launched",
                is_async=True,
            ),
            notification_entry(
                "n1", "2026-01-01T00:00:05.000Z", session_id, "a1", "toolu_async",
                "completed", "real result",
            ),
            assistant_text("a2", "2026-01-01T00:00:06.000Z", "wrapped up", session_id),
        ],
    )

    import_transcripts(store, config)

    delegation = store.get_task_by_external_id("toolu_async")
    assert delegation["status"] == "done"
    assert delegation["result"] == "real result"


def test_delegation_still_running_at_end_of_file_becomes_done(store, config):
    """M1: history cannot still be running: an unresolved delegation is closed at EOF."""
    session_id = "session-import-13"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
            assistant_tool_use(
                "a1",
                "2026-01-01T00:00:01.000Z",
                "toolu_orphan",
                "Agent",
                {"description": "sub task", "prompt": "go do x", "subagent_type": "general"},
                session_id,
            ),
        ],
    )

    import_transcripts(store, config)

    delegation = store.get_task_by_external_id("toolu_orphan")
    assert delegation["status"] == "done"
    assert delegation["result"] is None


def test_invalid_utf8_byte_does_not_abort_import(store, config):
    """M2: an undecodable byte on one line must not crash the whole import."""
    session_id = "session-import-14"
    project_dir = config.claude_config_dir / "projects" / PROJECT
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    valid_user = json.dumps(
        user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id)
    )
    valid_assistant = json.dumps(
        assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id)
    )
    with path.open("wb") as f:
        f.write(b"\xff\xfe not valid utf-8 or json\n")
        f.write(valid_user.encode("utf-8") + b"\n")
        f.write(valid_assistant.encode("utf-8") + b"\n")

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 1
    tasks = store.list_tasks(session_id=session_id, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"
    assert tasks[0]["result"] == "result a"


def test_null_message_entry_is_skipped(store, config):
    """M2: `"message": null` must be skipped, not raise, and must not count as a bad line."""
    session_id = "session-import-15"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            {
                "type": "user",
                "uuid": "u-null",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "sessionId": session_id,
                "cwd": CWD,
                "message": None,
            },
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 0
    tasks = store.list_tasks(session_id=session_id, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"
    assert tasks[0]["result"] == "result a"


def test_assistant_text_block_without_text_is_skipped(store, config):
    """M2: a text block missing the "text" key must be ignored, not raise a KeyError."""
    session_id = "session-import-16"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            {
                "type": "assistant",
                "uuid": "a-broken",
                "timestamp": "2026-01-01T00:00:02.000Z",
                "sessionId": session_id,
                "cwd": CWD,
                "message": {"role": "assistant", "content": [{"type": "text"}]},
            },
            user_entry("u2", "2026-01-01T00:01:00.000Z", "do task b", session_id),
        ],
    )

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 0
    tasks = sorted(
        store.list_tasks(session_id=session_id, kind="prompt"), key=lambda t: t["created_at"]
    )
    assert tasks[0]["status"] == "done"
    assert tasks[0]["result"] is None


def test_worker_session_is_skipped(store, config):
    """M3: a session recorded live by the worker must not be duplicated by import."""
    session_id = "session-import-17"
    store.upsert_session(session_id, source="worker")
    store.create_task(
        kind="prompt",
        body="write docs",
        status="done",
        source="ui",
        session_id=session_id,
    )
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    result = import_transcripts(store, config)

    assert result["skipped_sessions"] == 1
    assert result["tasks"] == 0
    tasks = store.list_tasks(session_id=session_id)
    assert len(tasks) == 1
    assert tasks[0]["source"] == "ui"


def test_imported_session_state_is_ended(store, config):
    """M9: an imported session must not read as active in the session pickers."""
    session_id = "session-import-18"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    import_transcripts(store, config)

    session = store.get_session(session_id)
    assert session["state"] == "ended"


def test_entries_without_uuid_do_not_merge(store, config):
    """LOW 'import:None merge': entries missing uuid must not collapse into one task."""
    session_id = "session-import-19"
    entries = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": session_id,
            "cwd": CWD,
            "message": {"role": "user", "content": "task one"},
        },
        assistant_text("a1", "2026-01-01T00:00:01.000Z", "result one", session_id),
        {
            "type": "user",
            "timestamp": "2026-01-01T00:01:00.000Z",
            "sessionId": session_id,
            "cwd": CWD,
            "message": {"role": "user", "content": "task two"},
        },
        assistant_text("a2", "2026-01-01T00:01:01.000Z", "result two", session_id),
    ]
    _write_transcript(config, session_id, entries)

    result = import_transcripts(store, config)

    assert result["tasks"] == 2
    tasks = sorted(
        store.list_tasks(session_id=session_id, kind="prompt"), key=lambda t: t["created_at"]
    )
    assert len(tasks) == 2
    assert [t["result"] for t in tasks] == ["result one", "result two"]
    assert len({t["external_id"] for t in tasks}) == 2


def test_directory_named_jsonl_does_not_abort_import(store, config):
    """Decision 1: an unreadable file (here, a directory) is counted and skipped, not fatal."""
    project_dir = config.claude_config_dir / "projects" / PROJECT
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "x.jsonl").mkdir()

    good_session = "session-import-21"
    _write_transcript(
        config,
        good_session,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", good_session),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", good_session),
        ],
    )

    result = import_transcripts(store, config)

    assert result["files"] == 2
    assert result["bad_lines"] == 1
    tasks = store.list_tasks(session_id=good_session, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"


def test_dangling_symlink_does_not_abort_import(store, config):
    """Decision 1: a dangling symlink is counted and skipped, not fatal."""
    project_dir = config.claude_config_dir / "projects" / PROJECT
    project_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(project_dir / "does-not-exist.txt", project_dir / "e.jsonl")

    good_session = "session-import-22"
    _write_transcript(
        config,
        good_session,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", good_session),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", good_session),
        ],
    )

    result = import_transcripts(store, config)

    assert result["files"] == 2
    assert result["bad_lines"] == 1
    tasks = store.list_tasks(session_id=good_session, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"


def test_cwd_as_list_is_ignored_not_an_error(store, config):
    """Decision 3: a non-string cwd is ignored, the import still completes."""
    session_id = "session-import-23"
    entry = user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id)
    entry["cwd"] = ["not", "a", "string"]
    _write_transcript(
        config,
        session_id,
        [entry, assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id)],
    )

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 0
    tasks = store.list_tasks(session_id=session_id, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"
    assert tasks[0]["cwd"] is None


def test_timestamp_as_object_is_ignored_not_an_error(store, config):
    """Decision 3: a non-string timestamp is ignored, the import still completes."""
    session_id = "session-import-24"
    entry = user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id)
    entry["timestamp"] = {"a": 1}
    _write_transcript(
        config,
        session_id,
        [entry, assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id)],
    )

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 0
    tasks = store.list_tasks(session_id=session_id, kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["created_at"] is not None


def test_tool_use_with_string_input_does_not_crash(store, config):
    """Decision 3: a non-dict tool_use `input` is ignored; title/body fall back."""
    session_id = "session-import-25"
    entries = [
        user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": session_id,
            "cwd": CWD,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_str", "name": "Agent", "input": "oops"}
                ],
            },
        },
        assistant_text("a2", "2026-01-01T00:00:02.000Z", "wrapped up", session_id),
    ]
    _write_transcript(config, session_id, entries)

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 0
    delegation = store.get_task_by_external_id("toolu_str")
    assert delegation is not None
    assert delegation["body"] == ""
    assert delegation["title"] == ""
    prompt = store.get_task_by_external_id("import:u1")
    assert prompt["status"] == "done"


def test_injected_failure_leaves_no_running_rows(store, config, monkeypatch):
    """Decision 1/2: a mid-file store failure never leaves a task `running`."""
    session_id = "session-import-26"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "please delegate this", session_id),
            assistant_tool_use(
                "a1",
                "2026-01-01T00:00:01.000Z",
                "toolu_flaky",
                "Agent",
                {"description": "sub task", "prompt": "go do x", "subagent_type": "general"},
                session_id,
            ),
        ],
    )

    real_create_task = store.create_task
    calls = {"n": 0}

    def flaky_create_task(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("database is locked")
        return real_create_task(*args, **kwargs)

    monkeypatch.setattr(store, "create_task", flaky_create_task)

    result = import_transcripts(store, config)

    assert result["bad_lines"] == 1
    assert result["tasks"] == 1
    tasks = store.list_tasks(session_id=session_id)
    assert tasks
    assert all(t["status"] != "running" for t in tasks)
    assert store.get_task_by_external_id("toolu_flaky") is None


def test_rerun_repairs_a_task_manually_left_running(store, config):
    """Decision 2: `_finalize_span` decides by current status, so a rerun repairs a stuck row."""
    session_id = "session-import-27"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    import_transcripts(store, config)
    task = store.get_task_by_external_id("import:u1")
    assert task["status"] == "done"
    store.update_task(task["id"], status="running")

    second_result = import_transcripts(store, config)

    repaired = store.get_task(task["id"])
    assert repaired["status"] == "done"
    assert repaired["result"] == "result a"
    assert second_result["sessions"] == 0


def test_rerun_does_not_touch_a_task_in_another_terminal_state(store, config):
    """Decision 2: `_finalize_span` only acts on a task that is still `running`; it must not
    override a status a later step (e.g. a user cancelling it) already settled."""
    session_id = "session-import-27b"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    import_transcripts(store, config)
    task = store.get_task_by_external_id("import:u1")
    store.update_task(task["id"], status="cancelled", result="cancelled by user")

    import_transcripts(store, config)

    unchanged = store.get_task(task["id"])
    assert unchanged["status"] == "cancelled"
    assert unchanged["result"] == "cancelled by user"


def test_counters_accurate_on_idempotent_rerun(store, config):
    """Decision 4: `tasks`/`sessions` only count rows actually created."""
    session_id = "session-import-28"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "result a", session_id),
        ],
    )

    first_result = import_transcripts(store, config)
    assert first_result["sessions"] == 1
    assert first_result["tasks"] == 1

    second_result = import_transcripts(store, config)
    assert second_result["sessions"] == 0
    assert second_result["tasks"] == 0


def test_token_in_result_is_redacted(store, config):
    """Decision 5: the dashboard access link is never imported as a task result."""
    session_id = "session-import-29"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "open the dashboard", session_id),
            assistant_text(
                "a1",
                "2026-01-01T00:00:05.000Z",
                "http://127.0.0.1:7777/#token=super-secret-abc123",
                session_id,
            ),
        ],
    )

    import_transcripts(store, config)

    task = store.get_task_by_external_id("import:u1")
    assert task["result"] == "http://127.0.0.1:7777/#token=<redacted>"
    assert "super-secret-abc123" not in task["result"]


def test_sidechain_assistant_entries_are_skipped(store, config):
    """LOW 'Sidechain entries': an inline subagent turn must not overwrite the parent result."""
    session_id = "session-import-20"
    _write_transcript(
        config,
        session_id,
        [
            user_entry("u1", "2026-01-01T00:00:00.000Z", "do task a", session_id),
            assistant_text(
                "a-side", "2026-01-01T00:00:01.000Z", "sidechain noise", session_id,
                is_sidechain=True,
            ),
            assistant_text("a1", "2026-01-01T00:00:05.000Z", "real result", session_id),
        ],
    )

    result = import_transcripts(store, config)

    assert result["tasks"] == 1
    tasks = store.list_tasks(session_id=session_id, kind="prompt")
    assert tasks[0]["status"] == "done"
    assert tasks[0]["result"] == "real result"
