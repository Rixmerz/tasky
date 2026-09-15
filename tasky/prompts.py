"""Classification of Claude Code prompt text into tasks, notifications or noise."""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_NOTIFICATION_BLOCK_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)

_IGNORE_PREFIXES = (
    "<local-command-",
    "Caveat:",
    "<system-reminder>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "[Request interrupted",
)


@dataclass(frozen=True)
class Classified:
    kind: str  # "task" | "notification" | "ignore"
    text: str


def classify(text: str) -> Classified:
    if not text or not text.strip():
        return Classified(kind="ignore", text=text)
    if text.startswith("<task-notification>"):
        return Classified(kind="notification", text=text)
    if text.startswith(_IGNORE_PREFIXES):
        return Classified(kind="ignore", text=text)
    if text.startswith("<command-message>"):
        args_match = _COMMAND_ARGS_RE.search(text)
        args = args_match.group(1).strip() if args_match else ""
        if not args:
            return Classified(kind="ignore", text=text)
        name_match = _COMMAND_NAME_RE.search(text)
        name = name_match.group(1).strip() if name_match else ""
        return Classified(kind="task", text=f"{name} {args}".strip())
    return Classified(kind="task", text=text)


def _extract_tag(block: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    return match.group(1).strip() if match else None


def parse_notifications(text: str) -> list[dict]:
    notifications = []
    for block in _NOTIFICATION_BLOCK_RE.findall(text):
        notifications.append(
            {
                "task_id": _extract_tag(block, "task-id"),
                "tool_use_id": _extract_tag(block, "tool-use-id"),
                "status": _extract_tag(block, "status"),
                "result": _extract_tag(block, "result"),
            }
        )
    return notifications


def queue_request(text: str, prefix: str) -> str | None:
    if not prefix or not text.startswith(prefix):
        return None
    return text[len(prefix) :].strip()


def make_title(text: str, limit: int = 140) -> str:
    for line in text.splitlines():
        collapsed = " ".join(line.split())
        if not collapsed:
            continue
        if len(collapsed) > limit:
            return collapsed[: limit - 1].rstrip() + "…"
        return collapsed
    return ""
