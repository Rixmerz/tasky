"""Classification of Claude Code prompt text into tasks, notifications or noise."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_NOTIFICATION_BLOCK_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
_PASTE_BLOCK_RE = re.compile(r"<pasted_content[^>]*>.*?</pasted_content>", re.DOTALL)
_PASTE_TAG_RE = re.compile(r"</?pasted_content[^>]*>")

# Commands that manage the session rather than ask for work: /compact with
# instructions is still not a task.
SESSION_COMMANDS = frozenset(
    {
        "add-dir", "agents", "artifacts", "bug", "clear", "compact", "config", "context", "cost",
        "doctor", "effort", "exit", "export", "fast", "feedback", "help", "hooks", "ide", "login",
        "logout", "mcp", "memory", "model", "output-style", "permissions", "plugin", "plugins",
        "privacy-settings", "quit", "release-notes", "rename", "resume", "rewind", "status",
        "statusline", "terminal-setup", "theme", "todos", "upgrade", "usage", "vim", "workflows",
    }
)

_IGNORE_PREFIXES = (
    "<local-command-",
    "Caveat:",
    "<system-reminder>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "[Request interrupted",
    # A subagent's message to its parent reaches the hook as if the user typed it.
    "<agent-message",
)

# Below this length an edited resend is compared as exact text only: short
# prompts ("yes", "go on") repeat for real.
_SAME_MIN_EDITED_LEN = 20
_SAME_SIMILARITY = 0.8


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
    # Claude Code has written both orders: message then name, and name then message.
    if text.startswith(("<command-message>", "<command-name>")):
        args_match = _COMMAND_ARGS_RE.search(text)
        args = args_match.group(1).strip() if args_match else ""
        if not args:
            return Classified(kind="ignore", text=text)
        name_match = _COMMAND_NAME_RE.search(text)
        name = name_match.group(1).strip() if name_match else ""
        if name.lstrip("/") in SESSION_COMMANDS:
            return Classified(kind="ignore", text=text)
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
    """The first line with text; what the user wrote around a paste goes before the paste."""
    outside = _PASTE_BLOCK_RE.sub(" ", text)
    text = outside if outside.strip() else _PASTE_TAG_RE.sub(" ", text)
    for line in text.splitlines():
        collapsed = " ".join(line.split())
        if not collapsed:
            continue
        if len(collapsed) > limit:
            return collapsed[: limit - 1].rstrip() + "…"
        return collapsed
    return ""


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


# Whole-message nudges that only push an existing, already-known turn along
# and name no new work of their own -- "next" and "dale" are left out on
# purpose, since real Tasky history shows both used as one-word commands that
# each do distinct work ("next" stepping through a list of items).
_CONTINUATION_PHRASES = frozenset(
    {
        "--continue",
        "continue",
        "please continue",
        "keep going",
        "go on",
        "carry on",
        "resume",
        "sigue",
        "continua",
        "continúa",
    }
)

# "vuelve a <one word>" ("open it again", "vuelve a abrila") and the English
# "try (it/that) again" / "do it/that again" -- anchored so a real request that
# only happens to start the same way ("vuelve a rediseñar la alpaca...") does
# not match: the whole message must be nothing but the nudge.
_CONTINUATION_RE = re.compile(r"^(vuelve a \S+|try( it| that)? again|do (it|that) again)$")


def is_continuation_nudge(text: str) -> bool:
    """A short message that only says "keep going", naming no new work.

    Used to fold a message like "--continue" or "vuelve a abrila" into the
    task it nudges instead of letting it become a task of its own.
    """
    stripped = normalize(text).strip(" .!?¡¿")
    if not stripped:
        return False
    return stripped in _CONTINUATION_PHRASES or bool(_CONTINUATION_RE.match(stripped))


def same_request(before: str, after: str) -> bool:
    """Whether ``after`` is ``before`` sent again, as is, extended or lightly edited."""
    a, b = normalize(before), normalize(after)
    if a == b:
        return True
    if min(len(a), len(b)) < _SAME_MIN_EDITED_LEN:
        return False
    if b.startswith(a) or a.startswith(b):
        return True
    return SequenceMatcher(None, a, b).ratio() >= _SAME_SIMILARITY
