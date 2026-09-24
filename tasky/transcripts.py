"""Copy every message of Claude Code transcripts into the ledger.

Tasky's tasks keep a prompt and its final reply; the transcript also holds what
was said in between, every tool call and its result. Claude Code deletes
transcripts after 30 days by default, so the messages are copied, not linked.

Reading is incremental: each file's byte offset is remembered and only new
complete lines are parsed, with a per-call byte budget so a hook never runs
long on a huge file (the rest is read on the next call). Messages are keyed by
their transcript uuid, so re-reading is harmless. Secrets are scrubbed before
anything is stored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tasky.config import Config
from tasky.store import Store

HOOK_BUDGET = 8 * 1024 * 1024
TEXT_CHARS = 8_000
TOOL_RESULT_CHARS = 4_000
TOOL_INPUT_CHARS = 1_000
_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read")
_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_RECAP_FOOTER_RE = re.compile(r"\s*\(disable recaps in /config\)\s*$")

# Scrubbed before storing: provider keys, tokens, private keys and obvious
# `password=`/`secret:` assignments. Deliberately broad; a false positive only
# hides a string from search.
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"#token=[A-Za-z0-9_\-]+"),
    re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)"
        r"(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"
    ),
]


def scrub(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
        elif pattern.groups == 1:
            text = pattern.sub(lambda m: f"{m.group(1)} [redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)
    return text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " […]"


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _tool_input_summary(name: str, data: Any) -> tuple[str, str | None]:
    """(searchable text, file path) for a tool call."""
    if not isinstance(data, dict):
        return "", None
    path = data.get("file_path") or data.get("notebook_path")
    path = path if isinstance(path, str) else None
    if name == "Bash" and isinstance(data.get("command"), str):
        return data["command"], None
    if name in ("Edit", "MultiEdit"):
        new = data.get("new_string") or ""
        return (new if isinstance(new, str) else ""), path
    if name == "Write":
        return "", path
    try:
        return json.dumps(data, ensure_ascii=False), path
    except (TypeError, ValueError):
        return "", path


def _queued_prompt(entry: dict) -> str | None:
    """Text the user typed while Claude was working, delivered inside the running turn."""
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    origin = attachment.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None
    prompt = attachment.get("prompt")
    return prompt if isinstance(prompt, str) and prompt.strip() else None


def _recap_text(entry: dict) -> str | None:
    """The recap Claude Code writes when you come back to a session after a while."""
    if entry.get("subtype") != "away_summary" or not isinstance(entry.get("content"), str):
        return None
    text = _RECAP_FOOTER_RE.sub("", entry["content"]).strip()
    return text or None


def parse_usage(entry: dict, session_id: str, prompt_id: str | None) -> dict | None:
    """Token usage of one assistant line, keyed by its API message id."""
    if entry.get("type") != "assistant":
        return None
    message = entry.get("message") or {}
    usage = message.get("usage")
    message_id = message.get("id")
    if not isinstance(usage, dict) or not isinstance(message_id, str):
        return None

    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and value > 0 else 0

    return {
        "message_id": message_id,
        "session_id": session_id,
        "prompt_id": prompt_id,
        "model": message.get("model") if isinstance(message.get("model"), str) else None,
        "input": count("input_tokens"),
        "output": count("output_tokens"),
        "cache_read": count("cache_read_input_tokens"),
        "cache_write": count("cache_creation_input_tokens"),
        "ts": entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None,
        "sidechain": bool(entry.get("isSidechain")),
    }


def parse_entry(entry: dict, session_id: str) -> list[dict]:
    """The messages one transcript line contributes (none for bookkeeping lines)."""
    etype = entry.get("type")
    if not isinstance(entry.get("uuid"), str):
        return []
    recap = _recap_text(entry) if etype == "system" else None
    if recap is not None:
        return [
            {
                "session_id": session_id,
                "cwd": entry.get("cwd") if isinstance(entry.get("cwd"), str) else None,
                "prompt_id": None,
                "role": "system",
                "ts": entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None,
                "sidechain": bool(entry.get("isSidechain")),
                "uuid": f"{entry['uuid']}:0",
                "kind": "recap",
                "text": _clip(scrub(recap), TEXT_CHARS),
            }
        ]
    queued = _queued_prompt(entry) if etype == "attachment" else None
    if queued is not None:
        return [
            {
                "session_id": session_id,
                "cwd": entry.get("cwd") if isinstance(entry.get("cwd"), str) else None,
                "prompt_id": None,
                "role": "user",
                "ts": entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None,
                "sidechain": bool(entry.get("isSidechain")),
                "uuid": f"{entry['uuid']}:0",
                "kind": "text",
                "text": _clip(scrub(queued), TEXT_CHARS),
            }
        ]
    if etype not in ("user", "assistant"):
        return []
    message = entry.get("message") or {}
    content = message.get("content")
    base = {
        "session_id": session_id,
        "cwd": entry.get("cwd") if isinstance(entry.get("cwd"), str) else None,
        "prompt_id": entry.get("promptId") if isinstance(entry.get("promptId"), str) else None,
        "role": etype,
        "ts": entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None,
        "sidechain": bool(entry.get("isSidechain")),
    }
    rows: list[dict] = []
    if isinstance(content, str):
        blocks: list[Any] = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = content
    else:
        return []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        row = {**base, "uuid": f"{entry['uuid']}:{index}"}
        if kind == "text":
            text = _REMINDER_RE.sub("", block.get("text") or "").strip()
            if not text or entry.get("isMeta"):
                continue
            rows.append({**row, "kind": "text", "text": _clip(scrub(text), TEXT_CHARS)})
        elif kind == "tool_use":
            name = str(block.get("name") or "")
            text, path = _tool_input_summary(name, block.get("input"))
            rows.append(
                {
                    **row,
                    "kind": "tool_use",
                    "tool_name": name,
                    "file_path": path if name in _FILE_TOOLS else None,
                    "text": _clip(scrub(text), TOOL_INPUT_CHARS),
                }
            )
        elif kind == "tool_result":
            text = _block_text(block.get("content"))
            if text.strip():
                rows.append(
                    {
                        **row,
                        "kind": "tool_result",
                        "text": _clip(scrub(text), TOOL_RESULT_CHARS),
                    }
                )
    return rows


def ingest_file(store: Store, path: str | Path, *, budget: int | None = None) -> int:
    """Read new complete lines of one transcript; returns messages added.

    Assistant lines carry no prompt id, so each takes the id of the user
    prompt before it; the id in force is saved with the offset so an
    incremental read picks up where the last one stopped.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    key = str(path)
    offset, known_size = store.transcript_offset(key)
    prompt_id = store.transcript_prompt(key)
    if size < known_size or offset > size:
        offset, prompt_id = 0, None  # rewritten or truncated: uuids keep the re-read idempotent
    if offset >= size:
        return 0
    rows: list[dict] = []
    usage: list[dict] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(budget if budget is not None else size - offset)
    end = chunk.rfind(b"\n")
    if end < 0:
        return 0  # no complete line yet
    for raw in chunk[: end + 1].splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            session_id = path.stem
        if entry.get("type") == "user" and isinstance(entry.get("promptId"), str):
            prompt_id = entry["promptId"]
        for row in parse_entry(entry, session_id):
            if row["prompt_id"] is None:
                row["prompt_id"] = prompt_id
            rows.append(row)
        used = parse_usage(entry, session_id, prompt_id)
        if used is not None:
            usage.append(used)
    added = store.add_messages(rows)
    store.add_usage(usage)
    store.set_transcript_offset(key, offset + end + 1, size, prompt_id)
    return added


def subagent_transcripts(path: str | Path) -> list[Path]:
    """Transcripts of the subagents a session ran: ``<session>/subagents/*.jsonl``."""
    path = Path(path)
    return sorted((path.parent / path.stem / "subagents").glob("*.jsonl"))


def ingest_session(store: Store, session_id: str, *, budget: int | None = HOOK_BUDGET) -> int:
    session = store.get_session(session_id)
    if not session or not session.get("transcript_path"):
        return 0
    return ingest_path(store, session["transcript_path"], budget=budget)


def ingest_path(store: Store, path: str | Path, *, budget: int | None = HOOK_BUDGET) -> int:
    """A session transcript, then its subagents' (their tokens count for the turn).

    The byte budget is shared by all of them, so a session with many subagents
    does not make one hook read many times the budget.
    """
    added = 0
    for each in (Path(path), *subagent_transcripts(path)):
        if budget is not None and budget <= 0:
            break
        before = store.transcript_offset(str(each))[0]
        added += ingest_file(store, each, budget=budget)
        if budget is not None:
            budget -= max(store.transcript_offset(str(each))[0] - before, 0)
    return added


def backfill(store: Store, config: Config) -> dict:
    """Read every transcript on disk; returns counts."""
    counts = {"files": 0, "messages": 0}
    projects = config.claude_config_dir / "projects"
    if not projects.is_dir():
        return counts
    for path in sorted(projects.glob("*/*.jsonl")):
        for each in (path, *subagent_transcripts(path)):
            counts["files"] += 1
            counts["messages"] += ingest_file(store, each)
    # With the whole history copied, turns merged or cancelled earlier can be told apart.
    counts.update(store.repair())
    return counts
