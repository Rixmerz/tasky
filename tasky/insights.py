"""Project history: milestones, problems and dead ends distilled by a small model.

Everything else in Tasky costs zero tokens; this is the one feature that calls a
model, and only when the user asks for a sync. A sync reads the tasks recorded
for one project since the previous sync, in batches, together with the records
already kept and the project's git log for the same days, and asks the model
what to add or correct. Every record cites the task ids it came from, and ids
the model was not shown are dropped, so a record can always be checked against
what was actually said.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tasky.config import Config
from tasky.store import INSIGHT_KINDS, Store

BATCH_TASKS = 40
BATCH_CHARS = 60_000
PROMPT_CHARS = 1_200
REPLY_CHARS = 2_500
KNOWN_RECORDS = 150
GIT_COMMITS = 150
CALL_TIMEOUT_S = 300
STALE_SYNC_S = 30 * 60
_TITLE_CHARS = 160
_TEXT_CHARS = 1_000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROBLEM_STATES = ("open", "solved")

SYSTEM_PROMPT = """\
You keep the history of one software project from the log of what its developer asked an AI \
coding agent and what the agent answered. You are given the records kept so far and new tasks \
(plus the git log of the same days when there is one). Return only what the new tasks change.

Record kinds:
- milestone: a step that changed where the project stands (a capability shipped or working, a \
decision taken, a migration phase reached, a direction abandoned). Not routine questions, not \
every commit, not version numbers by themselves.
- problem: something that went wrong or blocked the work. detail = the cause once known. \
solution = what fixed it, only once a task or commit shows it was actually applied; an idea, \
plan or proposal is not a solution (leave solution empty and mention the idea in detail). \
state = "solved" only when a later task or commit shows the fix was applied and held, \
otherwise "open".
- dead_end: a fix or approach that was believed correct and later turned out wrong (a later \
task says it did not work, it was reverted, or the real cause was something else). title = \
what was tried; detail = why it was wrong; solution = what worked instead, if known. These \
exist so nobody reapplies them: be specific enough that the same mistake is recognisable.

Rules:
- Cite in task_ids every task a record is based on, only from the new tasks shown.
- happened_on is the date (YYYY-MM-DD) of the task where it happened.
- When a new task changes an existing record (a problem gets solved, its solution turns out \
wrong, a milestone is refined), put it in updates with that record's id instead of adding a \
duplicate. When a known solution turns out wrong: update the problem with the new solution \
and add a dead_end for the old one.
- Write in the language the developer writes in. Be concise: titles under 12 words.
- Returning nothing is fine when the new tasks change nothing.
"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "new": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(INSIGHT_KINDS)},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "solution": {"type": "string"},
                    "state": {"type": "string", "enum": list(_PROBLEM_STATES)},
                    "happened_on": {"type": "string"},
                    "task_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["kind", "title", "task_ids"],
            },
        },
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "solution": {"type": "string"},
                    "state": {"type": "string", "enum": list(_PROBLEM_STATES)},
                    "task_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["id"],
            },
        },
    },
    "required": ["new", "updates"],
}

Runner = Callable[..., subprocess.CompletedProcess]


class SyncError(Exception):
    """A sync could not run or a model call failed."""


def sync(config: Config, cwd: str, *, run: Runner = subprocess.run) -> dict:
    """Bring one project's history up to date; returns a summary.

    Raises SyncError when another sync of the project is running. A failed
    batch stops the sync but keeps what earlier batches saved; the cursor only
    moves past a batch once its changes are stored.
    """
    with Store.open(config) as store:
        if not store.begin_insight_sync(cwd, stale_after_s=STALE_SYNC_S):
            raise SyncError("a sync of this project is already running")
    summary = {"batches": 0, "tasks": 0, "added": 0, "updated": 0, "cost_usd": 0.0}
    error: str | None = None
    try:
        for _ in range(config.insights_max_batches):
            with Store.open(config) as store:
                sync_row = store.insight_sync(cwd)
                after = sync_row["last_task_id"] if sync_row else 0
                tasks = _batch(store.tasks_for_insights(cwd, after, BATCH_TASKS))
                if not tasks:
                    break
                known = store.list_insights(cwd)[-KNOWN_RECORDS:]
            prompt = build_prompt(known, tasks, git_log(cwd, tasks))
            output, cost = call_model(config, prompt, run=run)
            summary["cost_usd"] += cost
            with Store.open(config) as store:
                added, updated = apply_output(store, cwd, output, {t["id"] for t in tasks})
                store.advance_insight_cursor(cwd, max(t["id"] for t in tasks))
            summary["batches"] += 1
            summary["tasks"] += len(tasks)
            summary["added"] += added
            summary["updated"] += updated
    except SyncError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the sync row, never left 'running'
        error = f"{type(exc).__name__}: {exc}"
    finally:
        with Store.open(config) as store:
            store.finish_insight_sync(cwd, cost_usd=summary["cost_usd"], error=error)
            summary["pending"] = store.pending_insight_tasks(cwd)
    summary["error"] = error
    return summary


def _batch(tasks: list[dict]) -> list[dict]:
    """The longest prefix of ``tasks`` that fits BATCH_CHARS (always at least one)."""
    kept: list[dict] = []
    used = 0
    for task in tasks:
        size = min(len(task["body"] or ""), PROMPT_CHARS) + min(
            len(task["result"] or ""), REPLY_CHARS
        )
        if kept and used + size > BATCH_CHARS:
            break
        kept.append(task)
        used += size
    return kept


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " […]"


def _day(task: dict) -> str:
    return (task.get("finished_at") or task.get("created_at") or "")[:10]


def build_prompt(known: list[dict], tasks: list[dict], git: str) -> str:
    records = [
        {
            k: v
            for k, v in {
                "id": r["id"],
                "kind": r["kind"],
                "title": r["title"],
                "detail": _clip(r["detail"], 300),
                "solution": _clip(r["solution"], 300) if r["solution"] else None,
                "state": r["state"],
                "happened_on": r["happened_on"],
            }.items()
            if v not in (None, "")
        }
        for r in known
    ]
    lines = ["<records>", json.dumps(records, ensure_ascii=False), "</records>", "", "<tasks>"]
    for task in tasks:
        lines.append(f'<task id="{task["id"]}" date="{_day(task)}" status="{task["status"]}">')
        lines.append(f"<asked>{_clip(task['body'], PROMPT_CHARS)}</asked>")
        if task["result"]:
            lines.append(f"<answered>{_clip(task['result'], REPLY_CHARS)}</answered>")
        lines.append("</task>")
    lines.append("</tasks>")
    if git:
        lines += ["", "<git_log>", git, "</git_log>"]
    return "\n".join(lines)


def git_log(cwd: str, tasks: list[dict]) -> str:
    days = sorted(d for d in (_day(t) for t in tasks) if _DATE_RE.match(d))
    if not days or not Path(cwd, ".git").exists():
        return ""
    since = date.fromisoformat(days[0]) - timedelta(days=1)
    until = date.fromisoformat(days[-1]) + timedelta(days=1)
    try:
        proc = subprocess.run(
            [
                "git", "-C", cwd, "log", "--all", f"--since={since}", f"--until={until}",
                "--date=short", "--format=%h %ad %s", f"-n{GIT_COMMITS}",
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def call_model(config: Config, prompt: str, *, run: Runner = subprocess.run) -> tuple[dict, float]:
    env = dict(os.environ)
    env["TASKY_HOOKS_OFF"] = "1"
    env.pop("TASKY_TASK_ID", None)
    config.home.mkdir(parents=True, exist_ok=True, mode=0o700)
    cmd = [
        config.claude_bin, "-p",
        "--model", config.insights_model,
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--system-prompt", SYSTEM_PROMPT,
        "--output-format", "json",
        "--json-schema", json.dumps(OUTPUT_SCHEMA),
    ]
    try:
        # Run from Tasky's home, not the project: the project's CLAUDE.md would
        # only add tokens and instructions meant for coding, not for this.
        proc = run(
            cmd, input=prompt, capture_output=True, text=True, env=env,
            cwd=str(config.home), timeout=CALL_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError(f"could not run {config.claude_bin}: {exc}") from exc
    try:
        reply = json.loads(proc.stdout)
    except ValueError as exc:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise SyncError(f"model call failed (exit {proc.returncode}): {detail}") from exc
    if not isinstance(reply, dict):
        raise SyncError("model call returned an unexpected reply")
    cost = reply.get("total_cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) else 0.0
    if reply.get("is_error"):
        raise SyncError(f"model call failed: {str(reply.get('result'))[:300]}")
    output = reply.get("structured_output")
    if not isinstance(output, dict):
        raise SyncError("model reply had no structured output")
    return output, cost


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _cited(value: Any, allowed: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    return [i for i in value if isinstance(i, int) and not isinstance(i, bool) and i in allowed]


def apply_output(store: Store, cwd: str, output: dict, allowed: set[int]) -> tuple[int, int]:
    """Store what the model returned, trusting none of it; returns (added, updated)."""
    added = updated = 0
    for item in output.get("new") or []:
        if not isinstance(item, dict) or item.get("kind") not in INSIGHT_KINDS:
            continue
        title = _text(item.get("title"), _TITLE_CHARS)
        cited = _cited(item.get("task_ids"), allowed)
        if title is None or not cited:
            continue
        happened = item.get("happened_on")
        if not (isinstance(happened, str) and _DATE_RE.match(happened)):
            happened = None
        state = None
        if item["kind"] == "problem":
            state = item.get("state") if item.get("state") in _PROBLEM_STATES else "open"
        store.add_insight(
            cwd=cwd,
            kind=item["kind"],
            title=title,
            detail=_text(item.get("detail"), _TEXT_CHARS) or "",
            happened_on=happened,
            state=state,
            solution=_text(item.get("solution"), _TEXT_CHARS),
            task_ids=cited,
        )
        added += 1
    for item in output.get("updates") or []:
        if not isinstance(item, dict):
            continue
        insight_id = item.get("id")
        if not isinstance(insight_id, int) or isinstance(insight_id, bool):
            continue
        current = store.get_insight(insight_id)
        if current is None or current["cwd"] != cwd:
            continue
        fields: dict[str, Any] = {}
        limits = (("title", _TITLE_CHARS), ("detail", _TEXT_CHARS), ("solution", _TEXT_CHARS))
        for key, limit in limits:
            value = _text(item.get(key), limit)
            if value is not None:
                fields[key] = value
        if current["kind"] == "problem" and item.get("state") in _PROBLEM_STATES:
            fields["state"] = item["state"]
        cited = _cited(item.get("task_ids"), allowed)
        if cited:
            fields["task_ids"] = [*current["task_ids"], *cited]
        if fields:
            store.update_insight(insight_id, **fields)
            updated += 1
    return added, updated
