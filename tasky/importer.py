"""Backfill sessions and tasks from Claude Code transcript files.

Scans ``<claude_config_dir>/projects/<slug>/<session-uuid>.jsonl`` transcripts (never the
nested ``<slug>/<session-uuid>/subagents/`` transcripts) and replays each session's user
prompts, assistant replies and delegated-agent tool uses into the store, using the same
text classification (`tasky.prompts.classify`) as live hook capture.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tasky.config import Config
from tasky.prompts import classify, parse_notifications
from tasky.store import Store

_AGENT_TOOLS = ("Agent", "Task")
_FAILED_NOTIFICATION_STATUSES = ("failed", "killed")
_TOKEN_RE = re.compile(r"#token=[A-Za-z0-9_-]+")

# A damaged file must not abort the rest of the import (decision 1): every stage that
# touches the file, the store, or values pulled out of untrusted JSON is guarded with this.
_FILE_ERRORS = (OSError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error)

_Counters = dict[str, int]


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _redact_token(text: str | None) -> str | None:
    """Never import the dashboard access link as a stored result (decision 5)."""
    if text is None or "#token=" not in text:
        return text
    return _TOKEN_RE.sub("#token=<redacted>", text)


def import_transcripts(store: Store, config: Config, *, dry_run: bool = False) -> dict:
    """Import every transcript under `config.claude_config_dir`.

    With `dry_run=True`, tallies what would be created without calling any store method
    that writes (`upsert_session`, `create_task`, `update_task`): reads (existence checks,
    idempotency) still happen, nothing is persisted.
    """
    counters: _Counters = {
        "files": 0,
        "sessions": 0,
        "tasks": 0,
        "skipped_sessions": 0,
        "bad_lines": 0,
    }
    projects_dir = config.claude_config_dir / "projects"
    if not projects_dir.is_dir():
        return counters

    fake_ids = itertools.count(-1, -1)
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        for path in sorted(project_dir.glob("*.jsonl")):
            counters["files"] += 1
            _import_file(store, path, dry_run=dry_run, counters=counters, fake_ids=fake_ids)
    return counters


def _import_file(
    store: Store,
    path: Path,
    *,
    dry_run: bool,
    counters: _Counters,
    fake_ids: Iterator[int],
) -> None:
    session_id = path.stem
    existing_session = store.get_session(session_id)
    already_live = existing_session is not None and existing_session["source"] != "import"
    if already_live or store.session_has_tasks_not_from(session_id, "import"):
        counters["skipped_sessions"] += 1
        return
    session_is_new = existing_session is None

    span: dict[str, Any] | None = None
    delegation_ids: set[int] = set()
    last_ts: str | None = None
    entries: list[dict] = []
    processed = 0
    session_written = False
    try:
        line_numbers: list[int] = []
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                counters["bad_lines"] += 1
                continue
            if not isinstance(parsed, dict):
                counters["bad_lines"] += 1
                continue
            entries.append(parsed)
            line_numbers.append(line_number)
        if not entries:
            return

        first_ts = next(
            (e["timestamp"] for e in entries if isinstance(e.get("timestamp"), str)), None
        )
        cwd = next((e["cwd"] for e in entries if isinstance(e.get("cwd"), str)), None)
        if not dry_run:
            store.upsert_session(
                session_id, cwd=cwd, transcript_path=str(path), source="import", at=first_ts
            )
            session_written = True
        if session_is_new:
            counters["sessions"] += 1

        for line_number, entry in zip(line_numbers, entries, strict=True):
            etype = entry.get("type")
            ts = _as_str(entry.get("timestamp"))
            if ts:
                last_ts = ts
            if etype == "user":
                span = _handle_user_entry(
                    store, entry, ts, session_id, span, line_number,
                    dry_run=dry_run, counters=counters, fake_ids=fake_ids,
                )
            elif etype == "assistant":
                _handle_assistant_entry(
                    store, entry, ts, session_id, span,
                    dry_run=dry_run, counters=counters, fake_ids=fake_ids,
                    delegation_ids=delegation_ids,
                )
            processed += 1
    except _FILE_ERRORS:
        # This file's damage stops here: the rest of it is counted as not imported, and
        # the next file still runs (decision 1).
        counters["bad_lines"] += max(len(entries) - processed, 1)

    # Whether the file finished cleanly or failed part-way, never leave imported rows
    # running (decision 2). Each step is guarded on its own so a second failure here
    # cannot re-abort the import.
    with contextlib.suppress(*_FILE_ERRORS):
        _finalize_span(store, span, is_last=True, dry_run=dry_run)
    with contextlib.suppress(*_FILE_ERRORS):
        _close_dangling_delegations(store, delegation_ids, last_ts, dry_run=dry_run)
    if session_written:
        with contextlib.suppress(*_FILE_ERRORS):
            store.update_session(session_id, state="ended")


def _handle_user_entry(
    store: Store,
    entry: dict,
    ts: str | None,
    session_id: str,
    span: dict[str, Any] | None,
    line_number: int,
    *,
    dry_run: bool,
    counters: _Counters,
    fake_ids: Iterator[int],
) -> dict[str, Any] | None:
    _maybe_close_delegation(store, entry, ts, dry_run=dry_run)
    if _is_meta_entry(entry):
        return span
    text = _extract_text(entry)
    if text is None:
        return span

    classified = classify(text)
    if classified.kind == "ignore":
        return span
    if classified.kind == "notification":
        _apply_notifications(store, classified.text, ts, dry_run=dry_run)
        return span

    # kind == "task": a real prompt closes the previous span and opens a new one.
    _finalize_span(store, span, is_last=False, dry_run=dry_run)
    uuid = _as_str(entry.get("uuid"))
    external_id = f"import:{uuid}" if uuid else f"import:{session_id}:{line_number}"
    task, _is_new = _get_or_create_task(
        store,
        external_id,
        dry_run=dry_run,
        counters=counters,
        fake_ids=fake_ids,
        kind="prompt",
        body=classified.text,
        status="running",
        source="import",
        cwd=_as_str(entry.get("cwd")),
        session_id=session_id,
        created_at=ts,
        started_at=ts,
    )
    return {
        "task": task,
        "last_text": None,
        "last_text_ts": None,
        "created_at": ts,
    }


def _maybe_close_delegation(
    store: Store, entry: dict, ts: str | None, *, dry_run: bool
) -> None:
    """Close a foreground `Agent`/`Task` delegation from its matching `tool_result` (M1).

    An async delegation's launch receipt (`toolUseResult.isAsync`) is not a result: the
    matching `<task-notification>` closes that one later, via `_apply_notifications`.
    """
    tool_use_result = entry.get("toolUseResult")
    if isinstance(tool_use_result, dict) and tool_use_result.get("isAsync"):
        return
    message = entry.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id")
        if not tool_use_id:
            continue
        delegation = store.get_task_by_external_id(tool_use_id)
        if (
            delegation is None
            or delegation["kind"] != "delegation"
            or delegation["status"] != "running"
        ):
            continue
        status = "failed" if block.get("is_error") else "done"
        result = _tool_result_text(block.get("content"))
        if not dry_run:
            store.update_task(delegation["id"], status=status, result=result, finished_at=ts)


def _tool_result_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _handle_assistant_entry(
    store: Store,
    entry: dict,
    ts: str | None,
    session_id: str,
    span: dict[str, Any] | None,
    *,
    dry_run: bool,
    counters: _Counters,
    fake_ids: Iterator[int],
    delegation_ids: set[int],
) -> None:
    if entry.get("isSidechain"):
        return
    message = entry.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and span is not None:
            text_value = block.get("text")
            if isinstance(text_value, str) and text_value:
                span["last_text"] = text_value
                span["last_text_ts"] = ts
        elif block_type == "tool_use" and block.get("name") in _AGENT_TOOLS:
            tool_input = block.get("input")
            tool_input = tool_input if isinstance(tool_input, dict) else {}
            parent_id = span["task"]["id"] if span is not None else None
            task, _is_new = _get_or_create_task(
                store,
                block.get("id"),
                dry_run=dry_run,
                counters=counters,
                fake_ids=fake_ids,
                kind="delegation",
                title=tool_input.get("description"),
                body=tool_input.get("prompt", ""),
                status="running",
                source="import",
                session_id=session_id,
                parent_id=parent_id,
                created_at=ts,
                started_at=ts,
            )
            delegation_ids.add(task["id"])


def _is_meta_entry(entry: dict) -> bool:
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return True
    if entry.get("toolUseResult") is not None:
        return True
    message = entry.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False


def _extract_text(entry: dict) -> str | None:
    message = entry.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _apply_notifications(store: Store, text: str, ts: str | None, *, dry_run: bool) -> None:
    if dry_run:
        return
    for note in parse_notifications(text):
        tool_use_id = note.get("tool_use_id")
        if not tool_use_id:
            continue
        delegation = store.get_task_by_external_id(tool_use_id)
        if delegation is None or delegation["status"] != "running":
            continue
        status = "failed" if note.get("status") in _FAILED_NOTIFICATION_STATUSES else "done"
        store.update_task(
            delegation["id"], status=status, result=note.get("result"), finished_at=ts
        )


def _get_or_create_task(
    store: Store,
    external_id: str | None,
    *,
    dry_run: bool,
    counters: _Counters,
    fake_ids: Iterator[int],
    **fields: Any,
) -> tuple[dict[str, Any], bool]:
    existing = store.get_task_by_external_id(external_id) if external_id else None
    if existing is not None:
        return existing, False
    if dry_run:
        counters["tasks"] += 1
        return {"id": next(fake_ids), "external_id": external_id}, True
    task = store.create_task(external_id=external_id, **fields)
    counters["tasks"] += 1
    return task, True


def _finalize_span(
    store: Store, span: dict[str, Any] | None, *, is_last: bool, dry_run: bool
) -> None:
    """Close the open prompt span if its task is still `running`.

    Deciding by the task's current status, not by whether it was created in this run
    (decision 2), means a rerun repairs a row an earlier, interrupted run left `running`.
    """
    if span is None or dry_run:
        return
    current = store.get_task(span["task"]["id"])
    if current is None or current["status"] != "running":
        return
    if span["last_text"] is not None:
        status = "done"
        result = _redact_token(span["last_text"])
        finished_at = span["last_text_ts"]
    else:
        status = "interrupted" if is_last else "done"
        result, finished_at = None, span["created_at"]
    store.update_task(span["task"]["id"], status=status, result=result, finished_at=finished_at)


def _close_dangling_delegations(
    store: Store, delegation_ids: set[int], finished_at: str | None, *, dry_run: bool
) -> None:
    """History cannot still be running (M1): close whatever this file left open."""
    if dry_run:
        return
    for task_id in delegation_ids:
        task = store.get_task(task_id)
        if task is not None and task["status"] == "running":
            store.update_task(task_id, status="done", result=None, finished_at=finished_at)
