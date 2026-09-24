"""Smart search: ask Haiku which tasks answer a question the plain search missed.

The plain search matches words. When the words are not the ones used back
then ("the login bug" for a task titled "fix 401 on refresh"), the model reads
a compact index of the ledger (prompt start, reply start, date, project) and
names the tasks that match. It sees nothing else and cannot run tools. Ids it
names that were not in the index are dropped.
"""

from __future__ import annotations

import subprocess

from tasky import history
from tasky.config import Config
from tasky.store import Store

MODEL = "haiku"
MAX_TASKS = 600
# The index of recent tasks goes in the system prompt, the same for every
# question, so Claude Code's prompt cache serves it on the next searches; the
# tasks that match the question's words go with the question.
MAX_INDEX_CHARS = 60_000
MAX_EXTRA_CHARS = 15_000
MAX_MATCHES = 10
_PROMPT_CHARS = 160
_FOLLOWUP_CHARS = 100
_REPLY_CHARS = 160

INSTRUCTIONS = """You find tasks in a developer's ledger of Claude Code prompts and replies.
You get an index (below, and more lines with the question): one line per task
with its id, date, project, the start of the prompt (P:), messages added
during the task (+:) and the start of the reply (R:). Name the tasks that
answer the question or are about what it describes, best first, at most 10.
Match meaning, not words: synonyms, other languages, the symptom for the fix.
Give each a reason of one short sentence, in the question's language, saying
what in that task matches. Name no task that is not in the index. If nothing
matches, return an empty list."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
            },
        }
    },
    "required": ["matches"],
}


class SmartSearchError(Exception):
    pass


def _flat(text: str | None, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _index_line(task: dict) -> str:
    when = (task.get("finished_at") or task.get("created_at") or "")[:10]
    project = task.get("project") or "?"
    line = f"#{task['id']} {when} [{project}] P: {_flat(task['body'], _PROMPT_CHARS)}"
    for extra in task.get("followups") or []:
        line += f" +: {_flat(extra.get('text'), _FOLLOWUP_CHARS)}"
    if task.get("result"):
        line += f" R: {_flat(task['result'], _REPLY_CHARS)}"
    return line


def _lines(tasks, budget: int, listed: dict[int, dict]) -> list[str]:
    lines: list[str] = []
    size = 0
    for task in tasks:
        if task["kind"] != "prompt" or task["id"] in listed:
            continue
        line = _index_line(task)
        if size + len(line) > budget:
            break
        lines.append(line)
        listed[task["id"]] = task
        size += len(line) + 1
    return lines


def build_index(
    store: Store, query: str, cwd: str | None
) -> tuple[str, str, dict[int, dict]]:
    """(recent index, lines matching the question's words, every task listed)."""
    listed: dict[int, dict] = {}
    newest = store.list_tasks(kind="prompt", cwd=cwd, limit=MAX_TASKS)
    recent = _lines(newest, MAX_INDEX_CHARS, listed)
    hits: dict[int, dict] = {}
    for word in query.split()[:8]:
        if len(word) >= 3:
            for task in store.search_tasks(word, cwd=cwd, limit=40):
                hits.setdefault(task["id"], task)
    extra = _lines(hits.values(), MAX_EXTRA_CHARS, listed)
    return "\n".join(recent), "\n".join(extra), listed


def search(
    config: Config,
    store: Store,
    query: str,
    *,
    cwd: str | None = None,
    run: history.Runner = subprocess.run,
) -> dict:
    query = query.strip()
    if not query:
        raise SmartSearchError("the question is empty")
    index, extra, listed = build_index(store, query, cwd)
    if not listed:
        return {"results": [], "cost_usd": 0.0, "model": MODEL, "scanned": 0}
    system_prompt = f"{INSTRUCTIONS}\n\nIndex:\n{index}"
    prompt = f"Question: {query}"
    if extra:
        prompt += f"\n\nMore index lines, matching the question's words:\n{extra}"
    try:
        output, cost = history.call_model(
            config, MODEL, prompt, run=run, system_prompt=system_prompt, schema=OUTPUT_SCHEMA
        )
    except history.SyncError as exc:
        raise SmartSearchError(str(exc)) from exc
    results: list[dict] = []
    seen: set[int] = set()
    for match in output.get("matches") or []:
        if not isinstance(match, dict):
            continue
        task_id = match.get("id")
        if not isinstance(task_id, int) or task_id not in listed or task_id in seen:
            continue
        seen.add(task_id)
        reason = match.get("reason") if isinstance(match.get("reason"), str) else ""
        results.append({"task": listed[task_id], "reason": reason.strip()[:300]})
        if len(results) >= MAX_MATCHES:
            break
    return {"results": results, "cost_usd": cost, "model": MODEL, "scanned": len(listed)}
