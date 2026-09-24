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


def parse_entry(entry: dict, session_id: str) -> list[dict]:
    """The messages one transcript line contributes (none for bookkeeping lines)."""
    etype = entry.get("type")
    if etype not in ("user", "assistant") or not isinstance(entry.get("uuid"), str):
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
    """Read new complete lines of one transcript; returns messages added."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    key = str(path)
    offset, known_size = store.transcript_offset(key)
    if size < known_size or offset > size:
        offset = 0  # rewritten or truncated: uuids keep the re-read idempotent
    if offset >= size:
        return 0
    session_id = path.stem
    rows: list[dict] = []
    read_to = offset
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
        if isinstance(entry, dict):
            rows.extend(parse_entry(entry, session_id))
    read_to = offset + end + 1
    added = store.add_messages(rows)
    store.set_transcript_offset(key, read_to, size)
    return added


def ingest_session(store: Store, session_id: str, *, budget: int | None = HOOK_BUDGET) -> int:
    session = store.get_session(session_id)
    if not session or not session.get("transcript_path"):
        return 0
    return ingest_file(store, session["transcript_path"], budget=budget)


def backfill(store: Store, config: Config) -> dict:
    """Read every transcript on disk; returns counts."""
    counts = {"files": 0, "messages": 0}
    projects = config.claude_config_dir / "projects"
    if not projects.is_dir():
        return counts
    for path in sorted(projects.glob("*/*.jsonl")):
        counts["files"] += 1
        counts["messages"] += ingest_file(store, path)
    return counts
