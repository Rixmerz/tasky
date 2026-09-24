"""MCP server (stdio) over Tasky's ledger and project history.

Lets the agent in any session look up what was already tried for a problem,
across the current repository or all of them, and record an attempt the
moment it learns whether it worked, at no extra model cost. JSON-RPC 2.0,
one message per line, standard library only.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, TextIO

from tasky import __version__, cards, repos
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
    "failed must not be applied again. After a fix is confirmed to work or not, record_attempt. "
    "get_architecture names the repository's areas, their folders, specs and open problems. "
    "search_cards finds the work items (issue-tracker cards with acceptance criteria) the tasks "
    "were grouped into."
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
        "name": "get_architecture",
        "description": "This repository's areas (business and technical parts, with their "
        "folders, aliases and specs), and for one area: its specs' requirements, problems with "
        "failed fixes, milestones and recent tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "description": "an area name or alias for detail"}
            },
        },
    },
    {
        "name": "search_cards",
        "description": "Issue-tracker cards the repository's tasks were grouped into (Compact "
        "dashboard): title, kind, status, area, objective. Words match title, objective, "
        "description, area and criteria; no query lists the latest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "status": {"type": "string", "enum": list(cards.STATUSES)},
                "scope": _SCOPE,
            },
        },
    },
    {
        "name": "get_card",
        "description": "One card in full: objective, description, acceptance criteria (stated "
        "by the developer or inferred), tasks, commits, problems, milestones and files edited.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
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
    if p.get("specs"):
        lines.append(f"  specs: {', '.join(p['specs'])}")
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
    line += f": {m['detail']}" if m.get("detail") else ""
    return line + (f" | specs: {', '.join(m['specs'])}" if m.get("specs") else "")


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


def _format_task(t: dict) -> str:
    lines = [
        f"task #{t['id']} {(t['finished_at'] or t['created_at'])[:10]} [{t['status']}] {t['title']}"
    ]
    lines += [f"  also asked: {f['text'][:300]}" for f in t.get("followups") or []]
    lines.append(f"  reply: {(t['result'] or '').strip()[:500]}")
    return "\n".join(lines)


def _format_session(store: Store, session: dict) -> str:
    title = session.get("title") or session["id"][:8]
    lines = [
        f"session {title} ({session['state']}, {session['started_at'][:10]} → "
        f"{session['last_seen_at'][:10]}, {session.get('cwd') or '?'})"
    ]
    recap = store.recaps(session_id=session["id"], limit=1)
    if recap:
        lines.append(f"  latest recap ({(recap[0]['ts'] or '')[:16]}): {recap[0]['text']}")
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
        return _search_history(store, query, _scope_repo(store, args))
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
            t
            for t in store.search_tasks(query, limit=60, include_hidden=True)
            if cwds is None or t["cwd"] in cwds
        ][:15]
        if not tasks:
            return "No matching tasks."
        return "\n\n".join(_format_task(t) for t in tasks)
    if name == "record_attempt":
        return _record_attempt(store, args)
    if name == "get_architecture":
        return _architecture(store, str(args.get("area") or "").strip())
    if name == "search_cards":
        if args.get("status") not in (None, "", *cards.STATUSES):
            raise ToolError(f"status must be one of {', '.join(cards.STATUSES)}")
        return _search_cards(store, str(args.get("query") or "").strip(),
                             args.get("status"), _scope_repo(store, args))
    if name == "get_card":
        card = next((c for c in cards.view_one(store, _int(args, "id"))), None)
        if card is None:
            raise ToolError("no such card")
        return _format_card(card)
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


HISTORY_HITS = 20
CARD_HITS = 20


def _fold(text: str) -> str:
    """Lower case without accents, so "sesion" finds "sesión"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _card_line(card: dict) -> str:
    when = " → ".join(dict.fromkeys(d for d in (card["first_on"], card["last_on"]) if d))
    facts = [card["area"] or "", when, f"{len(card['task_ids'])} task(s)"]
    line = f"card #{card['id']} [{card['status']}] {card['kind']}: {card['title']}"
    line += f" ({', '.join(f for f in facts if f)})"
    return line + (f"\n  objective: {card['objective']}" if card.get("objective") else "")


def _search_cards(store: Store, query: str, status: object, repo: str | None) -> str:
    """Every word, then any word, over the cards' own text; no query lists the latest."""
    found = [c for c in store.cards(repo) if status in (None, "", c["status"])]
    if not found:
        return "No cards yet: they are made from the History tab (Compact dashboard)."
    header = ""
    if query:
        words = _fold(query).split()
        texts = {
            c["id"]: _fold(" ".join([
                c["title"], c["objective"] or "", c["description"] or "", c["area"] or "",
                *(k.get("text") or "" for k in c["criteria"] if isinstance(k, dict)),
            ]))
            for c in found
        }
        every = [c for c in found if all(w in texts[c["id"]] for w in words)]
        if not every:
            every = [c for c in found if any(w in texts[c["id"]] for w in words)]
            header = "No card has every word; these have some of them." if every else ""
        if not every:
            counts = Counter(c["status"] for c in found)
            return "No matching cards. Cards here: " + ", ".join(
                f"{n} {s}" for s, n in counts.most_common()
            )
        found = every
    lines = [_card_line(c) for c in found[:CARD_HITS]]
    if len(found) > CARD_HITS:
        lines.append(f"(+{len(found) - CARD_HITS} more)")
    return "\n".join([header, *lines] if header else lines)


def _format_card(card: dict) -> str:
    lines = [_card_line(card).split("\n")[0] + f" [repo {card['repo']}]"]
    if card.get("objective"):
        lines.append(f"objective: {card['objective']}")
    if card.get("description"):
        lines.append(f"description: {card['description']}")
    if card["criteria"]:
        lines.append("acceptance criteria:")
        for c in card["criteria"]:
            if c.get("source") == "stated":
                lines.append(f'  - {c["text"]} (stated: "{c.get("quote", "")}")')
            else:
                lines.append(f"  - {c['text']} (inferred)")
    if card["tasks"]:
        lines.append("tasks: " + "; ".join(
            f"#{t['id']} {t['date']} [{t['status']}] {t['title']}" for t in card["tasks"]
        ))
    if card["commits"]:
        lines.append("commits: " + ", ".join(card["commits"]))
    for p in card["problems"]:
        lines.append(f"problem #{p['id']} [{p['state']}] {p['title']}")
    for m in card["milestones"]:
        lines.append(f"milestone {m['happened_on'] or '?'} {m['title']}")
    if card["files"]:
        more = card["files_total"] - len(card["files"])
        lines.append("files: " + ", ".join(card["files"]) + (f" (+{more} more)" if more else ""))
    return "\n".join(lines)


def _search_history(store: Store, query: str, repo: str | None) -> str:
    """A fixed ladder of free steps, so a search never turns into a hunt.

    1. every word, as written (full text, accent-insensitive), plus the
       problems and milestones of the areas the question names by name or alias;
    2. only when that finds nothing: any word;
    3. still nothing: the areas with how much history each has, to ask by area.
    """
    from tasky import architecture
    from tasky import areas as area_rules

    found = store.search_history(query, repo, HISTORY_HITS, any_word=False)
    problems, milestones = found["problems"], found["milestones"]
    header = ""
    views = [architecture.overview(store, r) for r in ([repo] if repo else store.area_repos())]
    named = [
        (area, phrase)
        for view in views
        for area_id, phrase in area_rules.query_areas(query, view["areas"])
        for area in view["areas"]
        if area["id"] == area_id
    ]
    for area, _phrase in named:
        for kind, items, get in (
            ("problems", problems, store.get_problem),
            ("milestones", milestones, store.get_milestone),
        ):
            have = {i["id"] for i in items}
            for brief in area[kind]:
                if brief["id"] in have or len(problems) + len(milestones) >= HISTORY_HITS:
                    continue
                record = get(brief["id"])
                if record is not None:
                    items.append(record)
    if named:
        header = "Areas named: " + ", ".join(
            area["name"]
            + ("" if area_rules.key(phrase) == area_rules.key(area["name"]) else f' ("{phrase}")')
            for area, phrase in named
        )
    if not problems and not milestones:
        found = store.search_history(query, repo, HISTORY_HITS)
        problems, milestones = found["problems"], found["milestones"]
        if problems or milestones:
            header = "No record has every word; these have some of them."
    parts = [_format_problem(p) for p in problems]
    parts += [_format_milestone(m) for m in milestones]
    if parts:
        return "\n\n".join([header, *parts] if header else parts)
    areas = [
        f"{a['name']} ({len(a['problems'])} problem(s), {len(a['milestones'])} milestone(s))"
        + (f" aka {', '.join(a['aliases'][:4])}" if a["aliases"] else "")
        for view in views for a in view["areas"] if a["problems"] or a["milestones"]
    ]
    if not areas:
        return "No matching problems or milestones."
    return (
        "No matching problems or milestones. Areas with history: " + "; ".join(areas[:30])
        + ". Search again with an area name, or call get_architecture with area=<name>."
    )


def _int(args: dict, key: str, default: int | None = None) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{key} must be an integer")
    return value


def _area_line(area: dict) -> str:
    line = f"- {area['name']} [{area['kind']}]"
    if area["description"]:
        line += f" {area['description']}"
    if area["paths"]:
        line += f" | paths: {', '.join(area['paths'][:6])}"
    if area["aliases"]:
        line += f" | aka: {', '.join(area['aliases'][:6])}"
    open_problems = sum(1 for p in area["problems"] if p["state"] != "solved")
    line += f" | {area['turns']} turn(s) edited {area['files']} file(s)"
    if area["last_edit"]:
        line += f", last {area['last_edit']}"
    if area["problems"]:
        line += f", {len(area['problems'])} problem(s) ({open_problems} open)"
    if area["spec_items"]:
        line += f", {len(area['spec_items'])} spec(s)"
    return line


def _architecture(store: Store, wanted: str) -> str:
    from tasky import architecture
    from tasky import areas as area_rules

    repo = repos.repo_of(store, os.getcwd())
    view = architecture.overview(store, repo)
    scan = view["scan"] or {}
    if not view["areas"] and not view["specs"]:
        return (
            f"No architecture recorded for {repo}. Open the Tasky dashboard, Architecture tab, "
            "and scan or map the areas."
        )
    specs = {s["path"]: s for s in view["specs"]}
    if wanted:
        area_id = area_rules.by_name(wanted, view["areas"])
        area = next((a for a in view["areas"] if a["id"] == area_id), None)
        if area is None:
            raise ToolError(
                f"no area {wanted!r}; areas: {', '.join(a['name'] for a in view['areas'])}"
            )
        lines = [_area_line(area)]
        for path in area["spec_items"]:
            spec = specs[path]
            status = f", {spec['status']}" if spec["status"] else ""
            lines.append(f"spec {path} [{spec['kind']}{status}] {spec['title']}: {spec['summary']}")
            lines += [f"  · {item}" for item in spec["items"][:15]]
            lines += [
                f"  problem #{p['id']} [{p['state']}] {p['title']}" for p in spec["problems"][:5]
            ]
            lines += [
                f"  milestone {m['happened_on'] or '?'}: {m['title']}"
                for m in spec["milestones"][:5]
            ]
        for problem in area["problems"][:10]:
            chain = store.get_problem(problem["id"])
            if chain is not None:
                lines.append(_format_problem(chain))
        for milestone in area["milestones"][:8]:
            lines.append(f"milestone {milestone['happened_on'] or '?'}: {milestone['title']}")
        for task in area["recent_tasks"]:
            lines.append(f"task #{task['id']} {task['date']} [{task['status']}] {task['title']}")
        return "\n".join(lines)
    lines = [
        f"Areas of {repo}"
        + (f" (scanned {scan['scanned_at'][:10]})" if scan.get("scanned_at") else "")
        + ":",
        *(_area_line(a) for a in view["areas"]),
    ]
    if view["unlinked_specs"]:
        lines.append("Specs in no area: " + ", ".join(view["unlinked_specs"][:20]))
    if view["unplaced_topics"]:
        lines.append("History topics in no area: " + ", ".join(view["unplaced_topics"][:20]))
    lines.append("Call get_architecture with area=<name> for its specs, problems and tasks.")
    return "\n".join(lines)


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
