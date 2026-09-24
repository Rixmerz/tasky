"""MCP server (stdio) over Tasky's ledger and project history.

Lets the agent in any session look up what was already tried for a problem,
across the current repository or all of them, and record an attempt the
moment it learns whether it worked, at no extra model cost. JSON-RPC 2.0,
one message per line, standard library only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, TextIO

from tasky import __version__, repos
from tasky.config import Config
from tasky.store import Store

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_MARK = {"worked": "✓", "failed": "✗", "partial": "◐", "pending": "…"}
_SCOPE = {
    "type": "string",
    "enum": ["repo", "all"],
    "description": "repo = this repository (default), all = every repository",
}

INSTRUCTIONS = (
    "Tasky keeps, per repository, problems with the ordered chain of fixes tried and why each "
    "failed, plus milestones. Before fixing a bug, search_history for it: a fix that already "
    "failed must not be applied again. After a fix is confirmed to work or not, record_attempt."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_history",
        "description": "Find problems (with every fix tried, in order, and why each failed) "
        "and milestones matching words.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "scope": _SCOPE},
            "required": ["query"],
        },
    },
    {
        "name": "get_problem",
        "description": "One problem with its full attempt chain and evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "dead_ends",
        "description": "Fixes that were applied, believed correct, and did not work.",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": _SCOPE, "limit": {"type": "integer"}},
        },
    },
    {
        "name": "search_tasks",
        "description": "Search past prompts and replies recorded by Tasky.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "scope": _SCOPE},
            "required": ["query"],
        },
    },
    {
        "name": "search_conversations",
        "description": "Search every message of past sessions (what was said, tools run, "
        "files touched), with the turns around each hit.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "scope": _SCOPE},
            "required": ["query"],
        },
    },
    {
        "name": "last_session",
        "description": "What the most recent sessions in this repository did: their tasks, "
        "outcomes and suggested /compact.",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": _SCOPE, "count": {"type": "integer"}},
        },
    },
    {
        "name": "record_attempt",
        "description": "Record a fix applied to a problem and its outcome. Give problem_id, or "
        "problem_title to open a new problem. failed_attempt_id marks an earlier attempt failed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "what was changed"},
                "outcome": {"type": "string", "enum": ["pending", "worked", "failed", "partial"]},
                "why": {"type": "string", "description": "why it failed, if it did"},
                "evidence": {"type": "string", "description": "error text or test output"},
                "problem_id": {"type": "integer"},
                "problem_title": {"type": "string"},
                "symptom": {"type": "string"},
                "failed_attempt_id": {"type": "integer"},
                "failed_because": {"type": "string"},
            },
            "required": ["description", "outcome"],
        },
    },
]


class ToolError(Exception):
    pass


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _scope_repo(store: Store, args: dict) -> str | None:
    if args.get("scope") == "all":
        return None
    return repos.repo_of(store, os.getcwd())


def _format_problem(p: dict) -> str:
    head = f"#{p['id']} [{p['state']}] {p['title']} (repo {p['name']}"
    head += f", topic {p['topic']})" if p.get("topic") else ")"
    lines = [head]
    if p.get("symptom"):
        lines.append(f"  symptom: {p['symptom']}")
    if p.get("cause"):
        lines.append(f"  cause: {p['cause']}")
    for a in p.get("attempts", []):
        line = f"  {a['seq']}. {_MARK.get(a['outcome'], '?')} {a['outcome']}: {a['description']}"
        if a.get("why"):
            line += f" | why: {a['why']}"
        dates = " → ".join(d for d in (a.get("believed_from"), a.get("invalidated_on")) if d)
        if dates:
            line += f" ({dates})"
        if a.get("evidence"):
            line += f' | evidence: "{a["evidence"]}"'
        if a.get("task_ids"):
            line += " | tasks " + ", ".join(f"#{i}" for i in a["task_ids"])
        line += f" [attempt {a['id']}{', by agent' if a.get('source') == 'agent' else ''}]"
        lines.append(line)
    return "\n".join(lines)


def _format_milestone(m: dict) -> str:
    line = f"milestone {m.get('happened_on') or '?'} {m['title']} (repo {m['name']})"
    return line + (f": {m['detail']}" if m.get("detail") else "")


def _format_hit(store: Store, hit: dict) -> str:
    session = store.get_session(hit["session_id"]) or {}
    where = f"session {session.get('title') or hit['session_id'][:8]} {(hit['ts'] or '')[:10]}"
    what = hit["kind"] + (f" {hit['tool_name']}" if hit.get("tool_name") else "")
    if hit.get("file_path"):
        what += f" {hit['file_path']}"
    lines = [f"[{where}] {hit['role']} {what}: {hit['text'][:600]}"]
    for c in hit.get("context", []):
        lines.append(f"  · {c['role']}: {c['text'][:240]}")
    return "\n".join(lines)


def _format_session(store: Store, session: dict) -> str:
    title = session.get("title") or session["id"][:8]
    lines = [
        f"session {title} ({session['state']}, {session['started_at'][:10]} → "
        f"{session['last_seen_at'][:10]}, {session.get('cwd') or '?'})"
    ]
    for t in store.list_tasks(session_id=session["id"], kind="prompt", limit=15):
        reply = " ".join((t.get("result") or "").split())[:200]
        line = f"  #{t['id']} [{t['status']}] {t['title']}"
        lines.append(line + (f" → {reply}" if reply else ""))
    if session.get("compact_prompt"):
        lines.append(f"  suggested /compact: {session['compact_prompt']}")
    return "\n".join(lines)


def call_tool(store: Store, name: str, args: dict) -> str:
    if name == "search_history":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        found = store.search_history(query, _scope_repo(store, args))
        parts = [_format_problem(p) for p in found["problems"]]
        parts += [_format_milestone(m) for m in found["milestones"]]
        return "\n\n".join(parts) or "No matching problems or milestones."
    if name == "get_problem":
        problem = store.get_problem(_int(args, "id"))
        if problem is None:
            raise ToolError("no such problem")
        return _format_problem(problem)
    if name == "dead_ends":
        limit = min(max(_int(args, "limit", 10), 1), 50)
        dead = store.dead_ends(_scope_repo(store, args), limit)
        if not dead:
            return "No failed attempts recorded."
        return "\n".join(
            f"- problem #{d['problem_id']} {d['problem_title']} (repo {d['name']}): "
            f"{d['description']}"
            + (f" | why: {d['why']}" if d["why"] else "")
            + (f" | what worked: {d['worked_instead']}" if d["worked_instead"] else "")
            for d in dead
        )
    if name == "search_tasks":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        repo = _scope_repo(store, args)
        cwds = None if repo is None else set(store.repo_cwds(repo))
        tasks = [
            t for t in store.search_tasks(query, limit=60) if cwds is None or t["cwd"] in cwds
        ][:15]
        if not tasks:
            return "No matching tasks."
        return "\n\n".join(
            f"task #{t['id']} {(t['finished_at'] or t['created_at'])[:10]} [{t['status']}] "
            f"{t['title']}\n  reply: {(t['result'] or '').strip()[:500]}"
            for t in tasks
        )
    if name == "record_attempt":
        return _record_attempt(store, args)
    if name == "search_conversations":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        repo = _scope_repo(store, args)
        cwds = None if repo is None else store.repo_cwds(repo)
        hits = store.search_messages(query, cwds, limit=8)
        if not hits:
            return "No matching messages."
        return "\n\n".join(_format_hit(store, h) for h in hits)
    if name == "last_session":
        count = min(max(_int(args, "count", 1), 1), 5)
        repo = _scope_repo(store, args)
        cwds = None if repo is None else store.repo_cwds(repo)
        sessions = store.last_sessions(cwds, count)
        if not sessions:
            return "No sessions recorded."
        return "\n\n".join(_format_session(store, s) for s in sessions)
    raise ToolError(f"unknown tool {name!r}")


def _int(args: dict, key: str, default: int | None = None) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{key} must be an integer")
    return value


def _record_attempt(store: Store, args: dict) -> str:
    description = str(args.get("description") or "").strip()
    outcome = args.get("outcome")
    if not description or outcome not in _MARK:
        raise ToolError("description and a valid outcome are required")
    today = _today()
    cwd = os.getcwd()
    if args.get("problem_id") is not None:
        problem = store.get_problem(_int(args, "problem_id"))
        if problem is None:
            raise ToolError("no such problem")
        problem_id = problem["id"]
    else:
        title = str(args.get("problem_title") or "").strip()
        if not title:
            raise ToolError("give problem_id or problem_title")
        repos.ensure(store, [cwd])
        problem_id = store.add_problem(
            cwd=cwd,
            title=title[:160],
            symptom=str(args.get("symptom") or "")[:1000],
            first_seen=today,
            last_seen=today,
            task_ids=[],
        )
    if args.get("failed_attempt_id") is not None:
        earlier = store.get_attempt(_int(args, "failed_attempt_id"))
        if earlier is None or earlier["problem_id"] != problem_id:
            raise ToolError("failed_attempt_id is not an attempt of this problem")
        store.update_attempt(
            earlier["id"],
            outcome="failed",
            invalidated_on=today,
            why=str(args.get("failed_because") or earlier["why"] or "")[:1000],
        )
    store.add_attempt(
        problem_id,
        description=description[:1000],
        outcome=outcome,
        why=str(args.get("why") or "")[:1000],
        evidence=str(args.get("evidence") or "")[:300],
        believed_from=today,
        invalidated_on=today if outcome == "failed" else None,
        task_ids=[],
        commits=[],
        source="agent",
    )
    fields: dict[str, Any] = {"last_seen": today}
    if outcome == "worked":
        fields["state"] = "solved"
    store.update_problem(problem_id, **fields)
    problem = store.get_problem(problem_id)
    assert problem is not None
    return "Recorded.\n" + _format_problem(problem)


def handle(config: Config, message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None  # a notification (initialized, cancelled): nothing to answer
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        result: dict[str, Any] = {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tasky", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        args = params.get("arguments") or {}
        try:
            with Store.open(config) as store:
                text = call_tool(
                    store, str(params.get("name")), args if isinstance(args, dict) else {}
                )
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(config: Config, stdin: TextIO, stdout: TextIO) -> int:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply: dict | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        else:
            try:
                reply = handle(config, message) if isinstance(message, dict) else None
            except Exception as exc:  # noqa: BLE001 - one bad call must not end the server
                reply = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
                }
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
