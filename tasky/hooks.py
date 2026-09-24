"""Claude Code hook handlers: turn hook payloads into store mutations.

Every event handler is defensive about missing fields because hook payloads
are an external contract Tasky does not control (see design.md's Context
section). `main` never lets an exception reach stdout or a non-zero exit: a
Tasky bug must never break a Claude Code session.
"""

from __future__ import annotations

import contextlib
import json
import os
import traceback
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, TextIO

from tasky import repos, transcripts
from tasky.config import Config, now_iso
from tasky.prompts import classify, parse_notifications, queue_request
from tasky.store import Store

_FAILED_NOTIFICATION_STATUSES = ("failed", "killed")
_REOPENABLE_PARENT_STATUSES = ("running", "done", "interrupted")
_CLAIM_ATTEMPTS = 5
_RESEND_WINDOW = timedelta(minutes=30)
_RESEND_MIN_EDITED_LEN = 20
_RESEND_SIMILARITY = 0.8


def _session_start(
    event: dict, store: Store, config: Config, env: Mapping[str, str]
) -> dict | None:
    session_id = event.get("session_id")
    if not session_id:
        return None
    store.upsert_session(
        session_id, cwd=event.get("cwd"), transcript_path=event.get("transcript_path")
    )
    source = event.get("source")
    blocks = []
    if source in ("resume", "compact"):
        if source == "resume":
            # The previous process is gone: its running tasks (delegations included)
            # will never receive another Stop or PostToolUse, so they would
            # otherwise stay running forever.
            store.interrupt_session(session_id)
        tasks = store.list_tasks(
            status=("running", "interrupted", "queued"),
            session_id=session_id,
            limit=config.context_items,
        )
        if tasks:
            lines = [
                f"Tasky: {len(tasks)} unfinished task(s) recorded for this session. This is "
                "context only: queued tasks are handed to you when it is their turn, do not "
                "start them unasked."
            ]
            lines += [f"#{t['id']} [{t['status']}] {t['title']}" for t in tasks]
            blocks.append("\n".join(lines))
    cwd = event.get("cwd")
    if cwd and config.dead_end_items > 0 and store.has_history():
        dead = store.dead_ends(repos.repo_of(store, cwd), config.dead_end_items)
        if dead:
            blocks.append(_dead_end_context(dead))
    if not blocks:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n".join(blocks),
        }
    }


def _dead_end_context(dead: list[dict]) -> str:
    lines = [
        "Tasky history of this repository: fixes that were applied here, believed correct, and "
        "did not work. Before applying one of these again, say so and check it first. The "
        "tasky MCP tools (search_history, get_problem) have the full chains."
    ]
    for d in dead:
        line = f"- #{d['problem_id']} {d['problem_title']}: tried {d['description'][:200]}"
        when = d["invalidated_on"] or d["believed_from"]
        if when:
            line += f" ({when})"
        if d["why"]:
            line += f"; failed because {d['why'][:200]}"
        if d["worked_instead"]:
            line += f"; what worked: {d['worked_instead'][:200]}"
        lines.append(line)
    return "\n".join(lines)


def _handle_notifications(text: str, prompt_id: str | None, store: Store) -> None:
    for note in parse_notifications(text):
        delegation = None
        tool_use_id = note.get("tool_use_id")
        if tool_use_id:
            delegation = store.get_task_by_external_id(tool_use_id)
        if delegation is None:
            agent_id = note.get("task_id")
            if agent_id:
                delegation = store.find_task_by_agent_id(agent_id)
        if delegation is None or delegation["status"] == "cancelled":
            continue
        status = "failed" if note.get("status") in _FAILED_NOTIFICATION_STATUSES else "done"
        fields: dict[str, Any] = {"status": status}
        result = note.get("result")
        if result is not None:
            fields["result"] = result
        store.update_task(delegation["id"], **fields)
        parent_id = delegation.get("parent_id")
        if parent_id is None:
            continue
        parent = store.get_task(parent_id)
        if parent is not None and parent["status"] in _REOPENABLE_PARENT_STATUSES:
            store.update_task(parent_id, prompt_id=prompt_id, status="running")


def _bind_env_task(
    raw_task_id: str, session_id: str | None, prompt_id: str | None, store: Store
) -> bool:
    try:
        task_id = int(raw_task_id)
    except ValueError:
        return False
    task = store.get_task(task_id)
    if task is None or task["status"] not in ("queued", "running"):
        return False
    if task["session_id"] not in (None, session_id):
        return False
    store.update_task(task_id, session_id=session_id, prompt_id=prompt_id, status="running")
    return True


def _same_request(before: str, after: str) -> bool:
    a = " ".join(before.split()).casefold()
    b = " ".join(after.split()).casefold()
    if a == b:
        return True
    if min(len(a), len(b)) < _RESEND_MIN_EDITED_LEN:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _RESEND_SIMILARITY


def _drop_cancelled_copy(session_id: str, text: str, store: Store) -> None:
    """Forget the previous prompt when this one is that prompt sent again.

    Esc on a submitted prompt puts its text back in the input box; sending it
    again, as is or lightly edited, is one request, not two. The earlier row
    is only dropped while it has nothing to show for itself: no reply, no
    delegations, still running or already marked interrupted, and recent.
    """
    previous = store.latest_prompt_task(session_id)
    if (
        previous is None
        or previous["source"] != "hook"
        or previous["status"] not in ("running", "interrupted")
        or previous["result"]
        or store.has_children(previous["id"])
    ):
        return
    created = datetime.fromisoformat(previous["created_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) - created > _RESEND_WINDOW:
        return
    if _same_request(previous["body"], text):
        store.delete_task(previous["id"])


def _user_prompt_submit(
    event: dict, store: Store, config: Config, env: Mapping[str, str]
) -> dict | None:
    session_id = event.get("session_id")
    if not session_id:
        return None
    cwd = event.get("cwd")
    prompt_id = event.get("prompt_id")
    text = event.get("prompt") or ""

    store.upsert_session(session_id, cwd=cwd, transcript_path=event.get("transcript_path"))

    queued_body = queue_request(text, config.queue_prefix)
    if queued_body is not None:
        if not queued_body:
            return {
                "decision": "block",
                "reason": f'Tasky: usage "{config.queue_prefix} <task text>" to queue a task',
            }
        task = store.create_task(
            kind="prompt",
            body=queued_body,
            status="queued",
            source="chat",
            cwd=cwd,
            session_id=session_id,
        )
        return {"decision": "block", "reason": f"Tasky: queued #{task['id']} — {task['title']}"}

    classified = classify(text)
    if classified.kind == "ignore":
        return None
    if classified.kind == "notification":
        _handle_notifications(text, prompt_id, store)
        return None

    task_id_env = env.get("TASKY_TASK_ID")
    if task_id_env and _bind_env_task(task_id_env, session_id, prompt_id, store):
        return None

    _drop_cancelled_copy(session_id, classified.text, store)
    store.create_task(
        kind="prompt",
        body=classified.text,
        status="running",
        source="hook",
        cwd=cwd,
        session_id=session_id,
        prompt_id=prompt_id,
    )
    # Only a real new prompt turn resets the chain cap: notifications, ignored
    # text, queue-prefix requests and TASKY_TASK_ID binding never reach here.
    store.update_session(session_id, pull_chain=0)
    return None


def _pre_tool_use(event: dict, store: Store, config: Config, env: Mapping[str, str]) -> dict | None:
    if event.get("tool_name") not in ("Agent", "Task"):
        return None
    session_id = event.get("session_id")
    prompt_id = event.get("prompt_id")
    tool_input = event.get("tool_input") or {}
    parent = store.latest_running_task(session_id, prompt_id) if session_id else None
    store.create_task(
        kind="delegation",
        body=tool_input.get("prompt") or "",
        status="running",
        source="hook",
        cwd=event.get("cwd"),
        session_id=session_id,
        parent_id=parent["id"] if parent else None,
        title=tool_input.get("description"),
        prompt_id=prompt_id,
        external_id=event.get("tool_use_id"),
    )
    return None


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p)
    return ""


def _post_tool_use_agent(event: dict, store: Store) -> None:
    tool_use_id = event.get("tool_use_id")
    if not tool_use_id:
        return
    delegation = store.get_task_by_external_id(tool_use_id)
    if delegation is None:
        return
    response = event.get("tool_response")
    if isinstance(response, dict) and response.get("isAsync"):
        agent_id = response.get("agentId")
        if agent_id:
            store.update_task(delegation["id"], agent_id=agent_id)
        return
    store.update_task(delegation["id"], status="done", result=_response_text(response))


def _post_tool_use(
    event: dict, store: Store, config: Config, env: Mapping[str, str]
) -> dict | None:
    if event.get("tool_name") in ("Agent", "Task"):
        _post_tool_use_agent(event, store)
    return None


def _subagent_stop(
    event: dict, store: Store, config: Config, env: Mapping[str, str]
) -> dict | None:
    agent_id = event.get("agent_id")
    session_id = event.get("session_id")
    task = store.find_task_by_agent_id(agent_id) if agent_id else None
    if task is None and session_id:
        candidates = store.list_tasks(status="running", session_id=session_id, kind="delegation")
        task = next((t for t in candidates if not t.get("agent_id")), None)
    if task is None:
        return None
    store.update_task(task["id"], status="done", result=event.get("last_assistant_message"))
    return None


def _env_bound_task(session_id: str, env: Mapping[str, str], store: Store) -> dict | None:
    """The task ``TASKY_TASK_ID`` names, if it is still running in this session.

    A worker binds its task in the first ``UserPromptSubmit`` (``_bind_env_task``).
    If that bind never happens — an ignored body, a hook that errors or is
    killed — the task stays unbound and this is the fallback that finds it
    anyway, so its own Stop/StopFailure does not read as "no target" (N2).
    """
    raw_task_id = env.get("TASKY_TASK_ID")
    if not raw_task_id:
        return None
    try:
        task_id = int(raw_task_id)
    except ValueError:
        return None
    task = store.get_task(task_id)
    if task is None or task["status"] != "running" or task["session_id"] != session_id:
        return None
    return task


def _stop(event: dict, store: Store, config: Config, env: Mapping[str, str]) -> dict | None:
    session_id = event.get("session_id")
    if not session_id:
        return None
    prompt_id = event.get("prompt_id")
    message = event.get("last_assistant_message")

    # A prompt_id pins the target to *this* turn only: falling back to "any
    # running task in the session" hands the result to whatever turn the user
    # interrupted earlier, since Claude Code fires no Stop for those (H4).
    if prompt_id is not None:
        target = store.latest_running_task(session_id, prompt_id)
    else:
        target = store.latest_running_task(session_id)

    env_task = _env_bound_task(session_id, env, store)
    used_fallback = target is None and env_task is not None
    if used_fallback:
        target = env_task

    if target is not None:
        status = "running" if store.running_children(target["id"]) else "done"
        fields: dict[str, Any] = {"result": message, "status": status}
        if used_fallback and target["prompt_id"] is None:
            fields["prompt_id"] = prompt_id
        store.update_task(target["id"], **fields)

    # Any other running prompt task in this session belongs to a turn the user
    # interrupted with Esc; it will never get its own Stop, so stop showing it
    # as running now that this turn has ended. The env-bound worker task is
    # never swept here even if it was not the target (N2).
    exclude_ids = {t["id"] for t in (target, env_task) if t is not None}
    store.interrupt_running(session_id, exclude_ids=exclude_ids)

    session = store.get_session(session_id)
    if not session or not session["auto_pull"] or session["pull_chain"] >= config.max_chain:
        return None

    claimed = None
    for _ in range(_CLAIM_ATTEMPTS):
        candidate = store.next_queued(session_id, event.get("cwd"))
        if candidate is None:
            break
        claimed = store.claim_task(
            candidate["id"], status="running", session_id=session_id, prompt_id=prompt_id
        )
        if claimed is not None:
            break
    if claimed is None:
        return None

    store.update_session(session_id, pull_chain=session["pull_chain"] + 1)
    # Worded as an explicit instruction: a bare "queue #3: ..." label was read by
    # the model as a status notice and acknowledged instead of executed.
    reason = (
        f"Next task from the Tasky queue (#{claimed['id']}). The user queued it for this "
        "session; do it now as if the user had just asked, then end your turn normally.\n\n"
        f"{claimed['body']}"
    )
    return {"decision": "block", "reason": reason}


def _stop_failure(event: dict, store: Store, config: Config, env: Mapping[str, str]) -> dict | None:
    session_id = event.get("session_id")
    if not session_id:
        return None
    prompt_id = event.get("prompt_id")
    target = store.latest_running_task(session_id, prompt_id)
    used_fallback = False
    if target is None:
        target = _env_bound_task(session_id, env, store)
        used_fallback = target is not None
    if target is None:
        return None
    fields: dict[str, Any] = {"status": "failed"}
    if used_fallback and target["prompt_id"] is None:
        fields["prompt_id"] = prompt_id
    error = event.get("error") or event.get("error_message")
    if isinstance(error, str) and error:
        fields["result"] = error
    store.update_task(target["id"], **fields)
    return None


def _session_end(event: dict, store: Store, config: Config, env: Mapping[str, str]) -> dict | None:
    session_id = event.get("session_id")
    if session_id:
        store.end_session(session_id)
    return None


_HANDLERS = {
    "SessionStart": _session_start,
    "UserPromptSubmit": _user_prompt_submit,
    "PreToolUse": _pre_tool_use,
    "PostToolUse": _post_tool_use,
    "SubagentStop": _subagent_stop,
    "Stop": _stop,
    "StopFailure": _stop_failure,
    "SessionEnd": _session_end,
    "PreCompact": lambda *args: None,
}

# Events after which the transcript has new messages worth copying: the end of a
# turn, before a compaction rewrites the context, and the end of the session.
_INGEST_EVENTS = ("Stop", "StopFailure", "SubagentStop", "PreCompact", "SessionEnd")


def handle(event: dict, store: Store, config: Config, env: Mapping[str, str]) -> dict | None:
    name = event.get("hook_event_name")
    handler = _HANDLERS.get(name)
    if handler is None:
        return None
    result = handler(event, store, config, env)
    if name in _INGEST_EVENTS:
        _ingest(event, store, config)
    return result


def _ingest(event: dict, store: Store, config: Config) -> None:
    """Copy the session's new transcript lines; a failure here never costs the event."""
    session_id = event.get("session_id")
    path = event.get("transcript_path")
    if not session_id:
        return
    try:
        # Read the path from the event, never through upsert_session: that would
        # mark a session SessionEnd just ended as active again.
        if isinstance(path, str) and path:
            transcripts.ingest_file(store, path, budget=transcripts.HOOK_BUDGET)
        else:
            transcripts.ingest_session(store, session_id)
    except Exception:  # noqa: BLE001 - the ledger update above already happened
        _log_error(config)


def _log_error(config: Config) -> None:
    with contextlib.suppress(Exception):
        config.home.mkdir(parents=True, exist_ok=True)
        with (config.home / "hook-errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {traceback.format_exc()}\n")


def main(stdin: TextIO, stdout: TextIO, env: Mapping[str, str] | None = None) -> int:
    os.umask(0o077)
    env = os.environ if env is None else env
    # Tasky's own model calls (history sync) set this so they are not recorded
    # as tasks of the project they describe.
    if env.get("TASKY_HOOKS_OFF") == "1":
        return 0
    try:
        config = Config.from_env(env)
    except Exception:  # noqa: BLE001 - no home to log to; a hook must never crash a session
        return 0
    try:
        payload = json.loads(stdin.read())
        with Store.open(config) as store:
            result = handle(payload, store, config, env)
        if result is not None:
            stdout.write(json.dumps(result))
    except Exception:  # noqa: BLE001 - a hook must never crash a session
        _log_error(config)
    return 0
