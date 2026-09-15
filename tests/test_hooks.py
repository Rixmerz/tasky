from __future__ import annotations

import dataclasses
import io
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tasky.config import Config
from tasky.hooks import handle, main
from tasky.store import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKY_HOOK = REPO_ROOT / "bin" / "tasky-hook"


def _event(hook_event_name: str, **fields) -> dict:
    base = {
        "hook_event_name": hook_event_name,
        "session_id": "s1",
        "cwd": "/work/app",
        "transcript_path": "/x/s1.jsonl",
    }
    base.update(fields)
    return base


# -- SessionStart -------------------------------------------------------


def test_session_start_startup_produces_no_context(store, config):
    result = handle(_event("SessionStart", source="startup"), store, config, {})
    assert result is None
    assert store.get_session("s1") is not None


@pytest.mark.parametrize("source", ["resume", "compact"])
def test_session_start_with_pending_tasks_injects_context(store, config, source):
    store.upsert_session("s1", cwd="/work/app")
    store.create_task(kind="prompt", body="running one", status="running", source="hook",
                       session_id="s1", cwd="/work/app")
    store.create_task(kind="prompt", body="queued one", status="queued", source="chat",
                       session_id="s1", cwd="/work/app")
    store.create_task(kind="prompt", body="queued two", status="queued", source="chat",
                       session_id="s1", cwd="/work/app")

    result = handle(_event("SessionStart", source=source), store, config, {})

    assert result is not None
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "running one" in context
    assert "queued one" in context
    assert "queued two" in context
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_session_start_resume_with_nothing_pending_returns_none(store, config):
    store.upsert_session("s1", cwd="/work/app")
    result = handle(_event("SessionStart", source="resume"), store, config, {})
    assert result is None


def test_session_start_resume_interrupts_stale_running_tasks(store, config):
    store.upsert_session("s1", cwd="/work/app")
    stale = store.create_task(kind="prompt", body="stuck", status="running", source="hook",
                               session_id="s1", cwd="/work/app")

    handle(_event("SessionStart", source="resume"), store, config, {})

    assert store.get_task(stale["id"])["status"] == "interrupted"


def test_session_start_resume_interrupts_a_prompt_and_its_running_delegation(store, config):
    # N3: the previous process is gone, so a delegation that was still running
    # will never finish and never get a SubagentStop — the resume sweep must
    # not skip the prompt just because a child looked "still working".
    store.upsert_session("s1", cwd="/work/app")
    prompt = store.create_task(kind="prompt", body="delegate it", status="running",
                                source="hook", session_id="s1", cwd="/work/app")
    delegation = store.create_task(kind="delegation", body="x", status="running",
                                    source="hook", session_id="s1", parent_id=prompt["id"])

    handle(_event("SessionStart", source="resume"), store, config, {})

    assert store.get_task(prompt["id"])["status"] == "interrupted"
    assert store.get_task(delegation["id"])["status"] == "interrupted"


def test_session_start_compact_does_not_interrupt_running_tasks(store, config):
    store.upsert_session("s1", cwd="/work/app")
    running = store.create_task(kind="prompt", body="still going", status="running",
                                 source="hook", session_id="s1", cwd="/work/app")

    handle(_event("SessionStart", source="compact"), store, config, {})

    assert store.get_task(running["id"])["status"] == "running"


# -- UserPromptSubmit: plain prompts -------------------------------------


def test_plain_prompt_is_recorded(store, config):
    event = _event("UserPromptSubmit", prompt_id="p1", prompt="refactor the login flow")
    result = handle(event, store, config, {})

    assert result is None
    tasks = store.list_tasks(kind="prompt")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "running"
    assert tasks[0]["title"] == "refactor the login flow"
    assert tasks[0]["cwd"] == "/work/app"
    assert tasks[0]["source"] == "hook"


def test_slash_command_with_arguments_is_recorded(store, config):
    text = (
        "<command-message>review</command-message>\n"
        "<command-name>/review</command-name>\n"
        "<command-args>the payment module</command-args>"
    )
    event = _event("UserPromptSubmit", prompt_id="p1", prompt=text)
    handle(event, store, config, {})

    tasks = store.list_tasks(kind="prompt")
    assert len(tasks) == 1
    assert "/review the payment module" in tasks[0]["title"]


def test_system_text_is_ignored(store, config):
    event = _event(
        "UserPromptSubmit", prompt_id="p1", prompt="<system-reminder>noop</system-reminder>"
    )
    result = handle(event, store, config, {})

    assert result is None
    assert store.list_tasks() == []


def test_notification_with_no_matching_delegation_creates_no_task(store, config):
    text = (
        "<task-notification>\n<task-id>unknown-agent</task-id>\n"
        "<tool-use-id>unknown-tool</tool-use-id>\n<status>completed</status>\n"
        "<summary>nothing to match</summary>\n<result>n/a</result>\n</task-notification>"
    )
    event = _event("UserPromptSubmit", prompt_id="p1", prompt=text)
    result = handle(event, store, config, {})

    assert result is None
    assert store.list_tasks() == []


# -- UserPromptSubmit: queue prefix ---------------------------------------


def test_prefixed_prompt_is_queued_and_blocked(store, config):
    event = _event("UserPromptSubmit", prompt_id="p1", prompt="++ write the migration for invoices")
    result = handle(event, store, config, {})

    tasks = store.list_tasks(status="queued")
    assert len(tasks) == 1
    assert tasks[0]["body"] == "write the migration for invoices"
    assert result["decision"] == "block"
    assert f"#{tasks[0]['id']}" in result["reason"]


def test_empty_queue_request_blocks_with_usage_hint_and_creates_nothing(store, config):
    event = _event("UserPromptSubmit", prompt_id="p1", prompt="++")
    result = handle(event, store, config, {})

    assert store.list_tasks() == []
    assert result["decision"] == "block"
    assert result["reason"]


def test_task_bound_to_env_task_id_is_reused(store, config):
    queued = store.create_task(kind="prompt", body="pulled work", status="queued", source="chat",
                                cwd="/work/app")
    event = _event("UserPromptSubmit", prompt_id="p9", prompt="pulled work")
    result = handle(event, store, config, {"TASKY_TASK_ID": str(queued["id"])})

    assert result is None
    task = store.get_task(queued["id"])
    assert task["status"] == "running"
    assert task["session_id"] == "s1"
    assert task["prompt_id"] == "p9"
    assert store.list_tasks(kind="prompt", status="running") == [task]


def test_env_task_id_owned_by_another_session_is_not_stolen(store, config):
    worker_task = store.create_task(kind="prompt", body="worker job", status="running",
                                     source="hook", session_id="worker-session",
                                     cwd="/work/app", prompt_id="wp1")
    event = _event("UserPromptSubmit", session_id="nested-session", prompt_id="np1",
                    prompt="claude -p hello")
    result = handle(event, store, config, {"TASKY_TASK_ID": str(worker_task["id"])})

    assert result is None
    assert store.get_task(worker_task["id"])["session_id"] == "worker-session"
    nested_tasks = store.list_tasks(session_id="nested-session")
    assert len(nested_tasks) == 1
    assert nested_tasks[0]["body"] == "claude -p hello"


# -- Delegations ------------------------------------------------------------


def test_agent_call_is_recorded_as_delegation_of_the_prompt_task(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="do the thing"), store, config, {})
    prompt_task = store.list_tasks(kind="prompt")[0]

    pre = _event(
        "PreToolUse",
        prompt_id="p1",
        tool_name="Agent",
        tool_use_id="toolu_1",
        tool_input={
            "description": "Answer math",
            "prompt": "What is 2+2?",
            "subagent_type": "general-purpose",
        },
    )
    handle(pre, store, config, {})

    delegation = store.get_task_by_external_id("toolu_1")
    assert delegation is not None
    assert delegation["kind"] == "delegation"
    assert delegation["status"] == "running"
    assert delegation["parent_id"] == prompt_task["id"]
    assert delegation["title"] == "Answer math"
    assert delegation["body"] == "What is 2+2?"


def test_pre_tool_use_ignores_unmatched_tool(store, config):
    result = handle(_event("PreToolUse", tool_name="Bash", tool_input={}), store, config, {})
    assert result is None
    assert store.list_tasks() == []


def test_background_subagent_completes(store, config):
    delegation = store.create_task(kind="delegation", body="What is 2+2?", status="running",
                                    source="hook", session_id="s1", external_id="toolu_1")
    post = _event(
        "PostToolUse",
        tool_name="Agent",
        tool_use_id="toolu_1",
        tool_response={"isAsync": True, "status": "async_launched", "agentId": "a1",
                       "description": "Answer math", "prompt": "What is 2+2?",
                       "outputFile": "/tmp/a1.output"},
    )
    handle(post, store, config, {})

    reloaded = store.get_task(delegation["id"])
    assert reloaded["agent_id"] == "a1"
    assert reloaded["status"] == "running"

    stop = _event("SubagentStop", agent_id="a1", agent_type="general-purpose",
                  stop_hook_active=False, agent_transcript_path="/x/a1.jsonl",
                  last_assistant_message="four")
    handle(stop, store, config, {})

    reloaded = store.get_task(delegation["id"])
    assert reloaded["status"] == "done"
    assert reloaded["result"] == "four"


def test_post_tool_use_sync_response_marks_delegation_done(store, config):
    store.create_task(kind="delegation", body="x", status="running", source="hook",
                       session_id="s1", external_id="toolu_2")
    post = _event(
        "PostToolUse",
        tool_name="Task",
        tool_use_id="toolu_2",
        tool_response={"content": [{"type": "text", "text": "answer part"}]},
    )
    handle(post, store, config, {})

    task = store.get_task_by_external_id("toolu_2")
    assert task["status"] == "done"
    assert task["result"] == "answer part"


def test_subagent_stop_fallback_finds_delegation_without_agent_id(store, config):
    delegation = store.create_task(kind="delegation", body="x", status="running", source="hook",
                                    session_id="s1", external_id="toolu_3")
    stop = _event("SubagentStop", agent_id="unknown-agent", last_assistant_message="done")
    handle(stop, store, config, {})

    reloaded = store.get_task(delegation["id"])
    assert reloaded["status"] == "done"
    assert reloaded["result"] == "done"


def test_todo_write_is_not_mirrored(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="track work"), store, config, {})
    event = _event(
        "PostToolUse",
        prompt_id="p1",
        tool_name="TodoWrite",
        tool_input={"todos": [{"content": "item one", "status": "pending"}]},
    )
    result = handle(event, store, config, {})

    assert result is None
    # TodoWrite is unhandled: only the prompt task exists, nothing was mirrored.
    assert len(store.list_tasks()) == 1


# -- Turn completion ----------------------------------------------------


def test_turn_finishes(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="do it"), store, config, {})
    stop = _event("Stop", prompt_id="p1", stop_hook_active=False,
                  last_assistant_message="Done, tests pass", background_tasks=[])
    handle(stop, store, config, {})

    task = store.list_tasks(kind="prompt")[0]
    assert task["status"] == "done"
    assert task["result"] == "Done, tests pass"


def test_turn_finishes_while_delegation_still_running(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="delegate it"), store, config, {})
    prompt_task = store.list_tasks(kind="prompt")[0]
    store.create_task(kind="delegation", body="x", status="running", source="hook",
                       session_id="s1", parent_id=prompt_task["id"], external_id="toolu_4")

    stop = _event("Stop", prompt_id="p1", stop_hook_active=False,
                  last_assistant_message="working on it", background_tasks=[])
    handle(stop, store, config, {})

    reloaded = store.get_task(prompt_task["id"])
    assert reloaded["status"] == "running"
    assert reloaded["result"] == "working on it"


def test_turn_fails(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="do it"), store, config, {})
    failure = _event("StopFailure", prompt_id="p1", error="upstream 500")
    handle(failure, store, config, {})

    task = store.list_tasks(kind="prompt")[0]
    assert task["status"] == "failed"
    assert task["result"] == "upstream 500"


def test_stop_with_unknown_prompt_id_does_not_fall_back_to_another_task(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="do it"), store, config, {})
    prompt_task = store.list_tasks(kind="prompt")[0]
    # A running delegation keeps interrupt_running from touching this task too,
    # isolating the assertion to "no fallback picked it".
    store.create_task(kind="delegation", body="x", status="running", source="hook",
                       session_id="s1", parent_id=prompt_task["id"], external_id="toolu_9")

    result = handle(_event("Stop", prompt_id="does-not-exist", stop_hook_active=False,
                            last_assistant_message="stray", background_tasks=[]), store, config, {})

    assert result is None
    reloaded = store.get_task(prompt_task["id"])
    assert reloaded["status"] == "running"
    assert reloaded["result"] is None


def test_stop_leaves_esc_interrupted_prompt_alone_and_marks_it_interrupted(store, config):
    handle(_event("UserPromptSubmit", prompt_id="pA", prompt="refactor login"), store, config, {})
    task_a = store.list_tasks(kind="prompt")[0]

    # Claude Code fires no Stop for the interrupted turn A; the next prompt (B)
    # starts a fresh turn, and only its own Stop should complete anything.
    handle(_event("UserPromptSubmit", prompt_id="pB", prompt="/tasky:ui"), store, config, {})
    task_b = next(t for t in store.list_tasks(kind="prompt") if t["id"] != task_a["id"])

    handle(_event("Stop", prompt_id="pB", stop_hook_active=False,
                  last_assistant_message="http://127.0.0.1:7733/", background_tasks=[]),
           store, config, {})

    reloaded_b = store.get_task(task_b["id"])
    assert reloaded_b["status"] == "done"
    assert reloaded_b["result"] == "http://127.0.0.1:7733/"

    reloaded_a = store.get_task(task_a["id"])
    assert reloaded_a["status"] == "interrupted"
    assert reloaded_a["result"] is None


def test_stop_falls_back_to_env_task_id_when_never_bound_by_a_prompt(store, config):
    # N2: the worker's own UserPromptSubmit never ran _bind_env_task (ignored body,
    # a hook that errored or was killed), so the task still has no prompt_id. Its
    # own Stop must still find it via TASKY_TASK_ID instead of reading "no target".
    worker_task = store.create_task(kind="prompt", body="worker job", status="running",
                                     source="hook", session_id="worker-session",
                                     cwd="/work/app")

    stop = _event("Stop", session_id="worker-session", prompt_id="wp1",
                  stop_hook_active=False, last_assistant_message="CLAUDE.md written",
                  background_tasks=[])
    result = handle(stop, store, config, {"TASKY_TASK_ID": str(worker_task["id"])})

    assert result is None
    reloaded = store.get_task(worker_task["id"])
    assert reloaded["status"] == "done"
    assert reloaded["result"] == "CLAUDE.md written"
    assert reloaded["prompt_id"] == "wp1"


def test_stop_failure_falls_back_to_env_task_id_when_never_bound_by_a_prompt(store, config):
    worker_task = store.create_task(kind="prompt", body="worker job", status="running",
                                     source="hook", session_id="worker-session",
                                     cwd="/work/app")

    failure = _event("StopFailure", session_id="worker-session", prompt_id="wp1",
                      error="upstream 500")
    result = handle(failure, store, config, {"TASKY_TASK_ID": str(worker_task["id"])})

    assert result is None
    reloaded = store.get_task(worker_task["id"])
    assert reloaded["status"] == "failed"
    assert reloaded["result"] == "upstream 500"
    assert reloaded["prompt_id"] == "wp1"


def test_stop_never_interrupts_the_env_bound_task_even_when_it_is_not_the_target(store, config):
    handle(_event("UserPromptSubmit", session_id="worker-session", prompt_id="p1",
                   prompt="do it"), store, config, {})
    normal_task = store.list_tasks(kind="prompt")[0]
    # Bound separately, no prompt_id yet, and not the Stop's target (prompt_id p1
    # resolves to normal_task): the interrupt_running sweep must still skip it.
    worker_task = store.create_task(kind="prompt", body="worker job", status="running",
                                     source="hook", session_id="worker-session",
                                     cwd="/work/app")

    stop = _event("Stop", session_id="worker-session", prompt_id="p1",
                  stop_hook_active=False, last_assistant_message="done", background_tasks=[])
    handle(stop, store, config, {"TASKY_TASK_ID": str(worker_task["id"])})

    assert store.get_task(normal_task["id"])["status"] == "done"
    assert store.get_task(worker_task["id"])["status"] == "running"


# -- Notifications --------------------------------------------------------


def test_notification_reopens_the_parent(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="delegate it"), store, config, {})
    prompt_task = store.list_tasks(kind="prompt")[0]

    handle(_event("PreToolUse", prompt_id="p1", tool_name="Agent", tool_use_id="toolu_1",
                  tool_input={"description": "Answer math", "prompt": "What is 2+2?",
                              "subagent_type": "general-purpose"}), store, config, {})
    handle(_event("PostToolUse", tool_name="Agent", tool_use_id="toolu_1",
                  tool_response={"isAsync": True, "status": "async_launched", "agentId": "a1"}),
           store, config, {})
    handle(_event("SubagentStop", agent_id="a1", last_assistant_message="four"), store, config, {})
    handle(_event("Stop", prompt_id="p1", stop_hook_active=False,
                  last_assistant_message="Delegated it", background_tasks=[]), store, config, {})

    notification_text = (
        "<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n"
        "<status>completed</status>\n<summary>Agent \"Answer math\" finished</summary>\n"
        "<result>four</result>\n</task-notification>"
    )
    handle(_event("UserPromptSubmit", prompt_id="p2", prompt=notification_text), store, config, {})

    reopened = store.get_task(prompt_task["id"])
    assert reopened["status"] == "running"
    assert reopened["prompt_id"] == "p2"

    handle(_event("Stop", prompt_id="p2", stop_hook_active=False,
                  last_assistant_message="Summary ready", background_tasks=[]), store, config, {})

    final = store.get_task(prompt_task["id"])
    assert final["status"] == "done"
    assert final["result"] == "Summary ready"


def test_notification_does_not_reopen_a_cancelled_parent(store, config):
    parent = store.create_task(kind="prompt", body="do it", status="cancelled", source="hook",
                                session_id="s1")
    delegation = store.create_task(kind="delegation", body="x", status="running", source="hook",
                                    session_id="s1", parent_id=parent["id"],
                                    external_id="toolu_5")

    notification_text = (
        "<task-notification>\n<task-id>a5</task-id>\n<tool-use-id>toolu_5</tool-use-id>\n"
        "<status>completed</status>\n<summary>done</summary>\n<result>ok</result>\n"
        "</task-notification>"
    )
    handle(_event("UserPromptSubmit", prompt_id="p2", prompt=notification_text), store, config, {})

    assert store.get_task(parent["id"])["status"] == "cancelled"
    assert store.get_task(delegation["id"])["status"] == "done"


def test_notification_without_result_keeps_the_stored_result(store, config):
    delegation = store.create_task(kind="delegation", body="x", status="running", source="hook",
                                    session_id="s1", external_id="toolu_6", result="partial")

    notification_text = (
        "<task-notification>\n<task-id>a6</task-id>\n<tool-use-id>toolu_6</tool-use-id>\n"
        "<status>killed</status>\n<summary>killed early</summary>\n</task-notification>"
    )
    handle(_event("UserPromptSubmit", prompt_id="p2", prompt=notification_text), store, config, {})

    reloaded = store.get_task(delegation["id"])
    assert reloaded["status"] == "failed"
    assert reloaded["result"] == "partial"


# -- Session lifecycle ----------------------------------------------------


def test_session_ends_mid_task(store, config):
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="do it"), store, config, {})
    handle(_event("SessionEnd", reason="other"), store, config, {})

    task = store.list_tasks(kind="prompt")[0]
    assert task["status"] == "interrupted"
    assert store.get_session("s1")["state"] == "ended"


# -- Unknown events -------------------------------------------------------


def test_unknown_event_returns_none(store, config):
    assert handle({"hook_event_name": "SomethingElse"}, store, config, {}) is None
    assert handle({}, store, config, {}) is None


# -- End-to-end sequence ----------------------------------------------------


def test_end_to_end_observed_sequence(store, config):
    handle(_event("SessionStart", source="startup"), store, config, {})
    handle(_event("UserPromptSubmit", prompt_id="p1", prompt="delegate the math question"),
           store, config, {})
    prompt_task = store.list_tasks(kind="prompt")[0]

    handle(_event("PreToolUse", prompt_id="p1", tool_name="Agent", tool_use_id="toolu_1",
                  tool_input={"description": "Answer math", "prompt": "What is 2+2?",
                              "subagent_type": "general-purpose"}), store, config, {})
    handle(_event("PostToolUse", tool_name="Agent", tool_use_id="toolu_1",
                  tool_response={"isAsync": True, "status": "async_launched", "agentId": "a1",
                                 "description": "Answer math", "prompt": "What is 2+2?",
                                 "outputFile": "/tmp/a1.output"}), store, config, {})
    handle(_event("SubagentStop", agent_id="a1", agent_type="general-purpose",
                  stop_hook_active=False, agent_transcript_path="/x/a1.jsonl",
                  last_assistant_message="four"), store, config, {})
    handle(_event("Stop", prompt_id="p1", stop_hook_active=False,
                  last_assistant_message="Delegated to a subagent", background_tasks=[]),
           store, config, {})

    notification_text = (
        "<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n"
        "<status>completed</status>\n<summary>Agent \"Answer math\" finished</summary>\n"
        "<result>four</result>\n</task-notification>"
    )
    handle(_event("UserPromptSubmit", prompt_id="p2", prompt=notification_text), store, config, {})
    handle(_event("Stop", prompt_id="p2", stop_hook_active=False,
                  last_assistant_message="All finished", background_tasks=[]), store, config, {})
    handle(_event("SessionEnd", reason="other"), store, config, {})

    delegation = store.get_task_by_external_id("toolu_1")
    assert delegation["status"] == "done"
    assert delegation["result"] == "four"

    final_prompt = store.get_task(prompt_task["id"])
    assert final_prompt["status"] == "done"
    assert final_prompt["result"] == "All finished"


# -- Auto-pull queue draining -----------------------------------------------


def test_pull_next_task_on_stop_when_auto_pull_enabled(store, config):
    store.upsert_session("s1", cwd="/work/app")
    store.update_session("s1", auto_pull=True)
    prompt_task = store.create_task(kind="prompt", body="original", status="running", source="hook",
                                     session_id="s1", cwd="/work/app", prompt_id="p1")
    queued = store.create_task(kind="prompt", body="task A", status="queued", source="chat",
                                session_id="s1", cwd="/work/app")

    result = handle(_event("Stop", prompt_id="p1", stop_hook_active=False,
                            last_assistant_message="original result", background_tasks=[]),
                     store, config, {})

    assert result["decision"] == "block"
    assert f"#{queued['id']}" in result["reason"]
    pulled = store.get_task(queued["id"])
    assert pulled["status"] == "running"
    assert pulled["session_id"] == "s1"
    assert pulled["prompt_id"] == "p1"
    assert store.get_task(prompt_task["id"])["result"] == "original result"


def test_chained_result_lands_on_the_pulled_task(store, config):
    store.upsert_session("s1", cwd="/work/app")
    store.update_session("s1", auto_pull=True)
    original = store.create_task(kind="prompt", body="original", status="running", source="hook",
                                  session_id="s1", cwd="/work/app", prompt_id="p1")
    store.create_task(kind="prompt", body="task A", status="queued", source="chat",
                       session_id="s1", cwd="/work/app")

    handle(_event("Stop", prompt_id="p1", stop_hook_active=False,
                  last_assistant_message="original result", background_tasks=[]), store, config, {})

    chained = handle(_event("Stop", prompt_id="p1", stop_hook_active=True,
                             last_assistant_message="Migration written", background_tasks=[]),
                      store, config, {})

    pulled = store.list_tasks(kind="prompt", status="done")
    pulled = [t for t in pulled if t["body"] == "task A"][0]
    assert pulled["status"] == "done"
    assert pulled["result"] == "Migration written"
    assert store.get_task(original["id"])["result"] == "original result"
    # chain cap default is 5; one pull leaves room, so no second pull was attempted here
    assert chained is None  # no queued task left, so nothing more to pull


def test_chain_cap_is_respected(store, config):
    capped = dataclasses.replace(config, max_chain=1)
    store.upsert_session("s1", cwd="/work/app")
    store.update_session("s1", auto_pull=True, pull_chain=1)
    store.create_task(kind="prompt", body="original", status="running", source="hook",
                       session_id="s1", cwd="/work/app", prompt_id="p1")
    queued = store.create_task(kind="prompt", body="still queued", status="queued", source="chat",
                                session_id="s1", cwd="/work/app")

    result = handle(_event("Stop", prompt_id="p1", stop_hook_active=True,
                            last_assistant_message="done", background_tasks=[]),
                     store, capped, {})

    assert result is None
    assert store.get_task(queued["id"])["status"] == "queued"


def test_run_queue_task_is_not_pulled(store, config):
    """task-queue spec: "Run queue task is not pulled" -- a task in a
    project's run queue (lane='serial') belongs to the scheduler only."""
    store.upsert_session("s1", cwd="/work/app")
    store.update_session("s1", auto_pull=True)
    store.create_task(kind="prompt", body="original", status="running", source="hook",
                       session_id="s1", cwd="/work/app", prompt_id="p1")
    queued = store.create_task(kind="prompt", body="queue task", status="queued", source="cli",
                                cwd="/work/app")
    store.enqueue_task(queued["id"], "default")

    result = handle(_event("Stop", prompt_id="p1", stop_hook_active=True,
                            last_assistant_message="done", background_tasks=[]), store, config, {})

    assert result is None
    assert store.get_task(queued["id"])["status"] == "queued"


def test_chain_cap_holds_across_a_notification_between_pulls(store, config):
    capped = dataclasses.replace(config, max_chain=1)
    store.upsert_session("s1", cwd="/work/app")
    store.update_session("s1", auto_pull=True)
    store.create_task(kind="prompt", body="original", status="running", source="hook",
                       session_id="s1", cwd="/work/app", prompt_id="p1")
    store.create_task(kind="prompt", body="task A", status="queued", source="chat",
                       session_id="s1", cwd="/work/app")
    task_b = store.create_task(kind="prompt", body="task B", status="queued", source="chat",
                                session_id="s1", cwd="/work/app")

    handle(_event("Stop", prompt_id="p1", stop_hook_active=False,
                  last_assistant_message="original result", background_tasks=[]), store, capped, {})
    assert store.get_session("s1")["pull_chain"] == 1

    # A background-agent notification is itself a UserPromptSubmit; it must not
    # reset the pull chain back to 0 (M5).
    notification_text = (
        "<task-notification>\n<task-id>unknown</task-id>\n<tool-use-id>unknown</tool-use-id>\n"
        "<status>completed</status>\n<summary>noop</summary>\n<result>n/a</result>\n"
        "</task-notification>"
    )
    handle(_event("UserPromptSubmit", prompt_id="p2", prompt=notification_text), store, capped, {})
    assert store.get_session("s1")["pull_chain"] == 1

    result = handle(_event("Stop", prompt_id="p1", stop_hook_active=True,
                            last_assistant_message="task A done", background_tasks=[]),
                     store, capped, {})

    assert result is None
    assert store.get_task(task_b["id"])["status"] == "queued"


def test_auto_pull_disabled_produces_no_continuation(store, config):
    store.upsert_session("s1", cwd="/work/app")
    store.create_task(kind="prompt", body="original", status="running", source="hook",
                       session_id="s1", cwd="/work/app", prompt_id="p1")
    queued = store.create_task(kind="prompt", body="task A", status="queued", source="chat",
                                session_id="s1", cwd="/work/app")

    result = handle(_event("Stop", prompt_id="p1", stop_hook_active=False,
                            last_assistant_message="done", background_tasks=[]), store, config, {})

    assert result is None
    assert store.get_task(queued["id"])["status"] == "queued"


def _race_stop_worker(home: Path, session_id: str, prompt_id: str, barrier, results) -> None:
    config = Config.from_env({"TASKY_HOME": str(home)})
    with Store.open(config) as store:
        barrier.wait()
        event = {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "cwd": "/work/app",
            "prompt_id": prompt_id,
            "stop_hook_active": False,
            "last_assistant_message": "done",
            "background_tasks": [],
        }
        results.append(handle(event, store, config, {}))


def test_concurrent_stop_hooks_claim_a_shared_queued_task_exactly_once(tmp_path):
    home = tmp_path / "race-home"
    config = Config.from_env({"TASKY_HOME": str(home)})
    with Store.open(config) as store:
        for session_id in ("s1", "s2"):
            store.upsert_session(session_id, cwd="/work/app")
            store.update_session(session_id, auto_pull=True)
            store.create_task(kind="prompt", body="running", status="running", source="hook",
                               session_id=session_id, cwd="/work/app", prompt_id="p1")
        store.create_task(kind="prompt", body="shared queued task", status="queued",
                           source="chat", cwd="/work/app")

    manager = multiprocessing.Manager()
    results = manager.list()
    barrier = multiprocessing.Barrier(2)
    procs = [
        multiprocessing.Process(target=_race_stop_worker, args=(home, sid, "p1", barrier, results))
        for sid in ("s1", "s2")
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)

    blocks = [r for r in results if r is not None and r.get("decision") == "block"]
    assert len(blocks) == 1

    with Store.open(config) as store:
        assert store.list_tasks(status="queued") == []
        running_prompts = store.list_tasks(kind="prompt", status="running")
        assert len(running_prompts) == 1


# -- main() -----------------------------------------------------------------


def test_main_writes_json_for_blocking_result(config):
    payload = json.dumps(_event("UserPromptSubmit", prompt_id="p1", prompt="++ queue this"))
    stdout = io.StringIO()
    env = {"TASKY_HOME": str(config.home)}

    code = main(io.StringIO(payload), stdout, env)

    assert code == 0
    result = json.loads(stdout.getvalue())
    assert result["decision"] == "block"


def test_main_returns_zero_and_logs_on_invalid_json(config):
    stdout = io.StringIO()
    env = {"TASKY_HOME": str(config.home)}

    code = main(io.StringIO("not json"), stdout, env)

    assert code == 0
    assert stdout.getvalue() == ""
    log_path = config.home / "hook-errors.log"
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8").strip() != ""


def test_main_returns_zero_and_logs_on_store_failure(config):
    config.home.mkdir(parents=True, exist_ok=True)
    (config.home / "tasky.db").mkdir()  # a directory where the db file should be
    stdout = io.StringIO()
    env = {"TASKY_HOME": str(config.home)}

    code = main(io.StringIO(json.dumps(_event("SessionEnd"))), stdout, env)

    assert code == 0
    assert stdout.getvalue() == ""
    assert (config.home / "hook-errors.log").exists()


def test_main_swallows_a_failure_it_cannot_even_log(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    env = {"TASKY_HOME": str(blocker / "tasky-home")}
    stdout = io.StringIO()

    code = main(io.StringIO("not json"), stdout, env)

    assert code == 0
    assert stdout.getvalue() == ""


def test_bin_tasky_hook_subprocess_exits_zero_and_silent(tmp_path):
    env = {**os.environ, "TASKY_HOME": str(tmp_path / "home")}
    payload = json.dumps(_event("SessionEnd"))

    proc = subprocess.run(
        [sys.executable, str(TASKY_HOOK)],
        input=payload, capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0
    assert proc.stdout == ""


def test_bin_tasky_hook_subprocess_corrupt_input_exits_zero_and_logs(tmp_path):
    home = tmp_path / "home"
    env = {**os.environ, "TASKY_HOME": str(home)}

    proc = subprocess.run(
        [sys.executable, str(TASKY_HOOK)],
        input="not json", capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0
    assert proc.stdout == ""
    assert (home / "hook-errors.log").exists()


def test_bin_tasky_hook_subprocess_emits_block_json(tmp_path):
    env = {**os.environ, "TASKY_HOME": str(tmp_path / "home")}
    payload = json.dumps(_event("UserPromptSubmit", prompt_id="p1", prompt="++ queue this"))

    proc = subprocess.run(
        [sys.executable, str(TASKY_HOOK)],
        input=payload, capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert result["decision"] == "block"
