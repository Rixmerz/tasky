"""Project history: problems with their chain of attempts, and milestones.

Everything else in Tasky costs zero tokens; a history sync is the one thing
that calls a model, and only when the user asks. A sync reads the tasks
recorded in one repository's folders since the previous sync, in batches,
together with the history already kept and the git log of the same days, and
asks the model what to add or correct.

Nothing the model returns is trusted as is: a record must cite tasks it was
shown, an evidence quote is kept only if it appears verbatim in a cited task,
commit hashes only if they were in the git log it was given, and an update
can only touch records of the repository being synced.
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

from tasky import repos
from tasky.config import Config, now_iso
from tasky.store import Store

# No Haiku: the sync judges whether a fix really failed across tasks days apart, which Haiku
# got wrong in testing, and its saving is a few cents per sync (the fixed prompt dominates).
MODELS = ("sonnet", "opus")
OUTCOMES = ("pending", "worked", "failed", "partial")
STATES = ("open", "solved", "recurring")
BATCH_TASKS = 40
BATCH_CHARS = 60_000
PROMPT_CHARS = 1_200
REPLY_CHARS = 2_500
KNOWN_PROBLEMS = 80
KNOWN_MILESTONES = 80
GIT_COMMITS = 150
CALL_TIMEOUT_S = 600
STALE_SYNC_S = 45 * 60
_TITLE = 160
_TEXT = 1_000
_QUOTE = 300
_COMPACT = 1_200
_TOPIC = 40
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")

SYSTEM_PROMPT = """\
You keep the engineering history of one software repository, distilled from the log of what \
its developer asked an AI coding agent and what the agent answered. You receive the history \
kept so far and new tasks (plus the git log of the same days when there is one). Return only \
what the new tasks add or change.

The point of this history is traceability: when a fix that was believed correct turns out \
not to work, nobody should apply it again, and anyone should be able to see the whole chain \
of what was tried for a problem, in order, and why each step failed.

problems: something that went wrong or blocked the work.
- symptom: what was observed. cause: the cause once known.
- state: "open"; "solved" only when a task or commit shows a fix was applied and held; \
"recurring" when a problem believed solved came back.
- attempts: the ordered chain of fixes or approaches actually applied to it. An idea, plan or \
proposal that was not applied is not an attempt (mention it in cause if useful).
  outcome: "pending" (applied, verdict not known yet), "worked", "failed", "partial".
  why: for failed or partial, why it did not work.
  believed_from: date it was applied; invalidated_on: date a later task or commit showed it \
did not work.
  evidence: an exact quote (copied character for character, under 200 characters) from the \
asked or answered text of a cited task that shows the outcome. Leave it empty rather than \
paraphrase.
- When a new task shows an existing attempt failed: return that problem with its \
existing_id, return that attempt with its existing_id, outcome "failed", why, invalidated_on \
and evidence, and add the new attempt (without existing_id) that replaced it.

milestones: a step that changed where the project stands (a capability working, a decision \
taken, a migration phase reached, a direction abandoned). Not routine questions, not every \
commit, not version numbers by themselves.

Rules:
- existing_id is only for changing a record listed in <history>, copied from it. New records \
have no existing_id: never number them yourself.
- task_ids: every new task the record or change is based on, only from the new tasks shown.
- commits: short hashes from the git log shown, only when a commit is the evidence.
- topic: a short lowercase area name (for example "auth", "deploy", "ui"); reuse the topics \
already in use.
- Dates are YYYY-MM-DD, the date of the task where it happened.
- Write problems and milestones in the language the developer writes in. Titles under 12 words.

compact: for each session listed in <sessions>, write the instructions to give Claude Code's \
/compact for that session, so its summary keeps what matters: the goal in progress, decisions \
taken and why, open problems and the attempts that already failed (so they are not retried), \
the files and components being changed, constraints and preferences the developer stated, and \
the next step; and drops what no longer matters (resolved tangents, tool output already acted \
on, abandoned ideas). Always in English, whatever language the session uses, keeping names, \
paths, ids and quotes verbatim. Telegraphic imperative: fragments, no articles or filler, under \
100 words, based only on what you were shown. End with this line, as is: "Summary style: terse \
fragments, no filler; keep exact names, paths, ids; keep every decision's reason and every \
failed attempt's cause in full."
- Returning empty lists is fine when the new tasks change nothing.
"""

_ATTEMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "existing_id": {"type": "integer"},
        "description": {"type": "string"},
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "why": {"type": "string"},
        "evidence": {"type": "string"},
        "believed_from": {"type": "string"},
        "invalidated_on": {"type": "string"},
        "task_ids": {"type": "array", "items": {"type": "integer"}},
        "commits": {"type": "array", "items": {"type": "string"}},
    },
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "symptom": {"type": "string"},
                    "cause": {"type": "string"},
                    "topic": {"type": "string"},
                    "state": {"type": "string", "enum": list(STATES)},
                    "happened_on": {"type": "string"},
                    "task_ids": {"type": "array", "items": {"type": "integer"}},
                    "attempts": {"type": "array", "items": _ATTEMPT_SCHEMA},
                },
            },
        },
        "compact": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["session_id", "instructions"],
            },
        },
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "topic": {"type": "string"},
                    "happened_on": {"type": "string"},
                    "task_ids": {"type": "array", "items": {"type": "integer"}},
                    "commits": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    "required": ["problems", "milestones", "compact"],
}

Runner = Callable[..., subprocess.CompletedProcess]


class SyncError(Exception):
    """A sync could not run or a model call failed."""


def sync(
    config: Config, repo: str, *, model: str | None = None, run: Runner = subprocess.run
) -> dict:
    """Bring one repository's history up to date; returns a summary.

    Raises SyncError when the model is unknown or another sync of the repo is
    running. A failed batch stops the sync but keeps earlier batches; a
    folder's cursor only moves past a batch once its changes are stored.
    """
    model = model or config.history_model
    if model not in MODELS:
        raise SyncError(f"unknown model {model!r}; use one of {', '.join(MODELS)}")
    with Store.open(config) as store:
        cwds = store.repo_cwds(repo)
        if not cwds:
            raise SyncError(f"no recorded folders belong to {repo}")
        if not store.begin_history_sync(repo, model, stale_after_s=STALE_SYNC_S):
            raise SyncError("a sync of this repository is already running")
    summary: dict[str, Any] = {
        "batches": 0,
        "compact": 0,
        "tasks": 0,
        "added": 0,
        "updated": 0,
        "cost_usd": 0.0,
        "model": model,
    }
    error: str | None = None
    try:
        for _ in range(config.history_max_batches):
            with Store.open(config) as store:
                tasks = _batch(store.tasks_for_history(cwds, BATCH_TASKS))
                if not tasks:
                    break
                known_problems = store.problems(repo)[:KNOWN_PROBLEMS]
                known_milestones = store.milestones(repo)[-KNOWN_MILESTONES:]
                sessions = _active_sessions(store, tasks)
            git = git_log(cwds[0], tasks)
            prompt = build_prompt(known_problems, known_milestones, tasks, git, sessions)
            output, cost = call_model(config, model, prompt, run=run)
            summary["cost_usd"] += cost
            with Store.open(config) as store:
                added, updated = apply_output(store, repo, output, tasks, git)
                summary["compact"] += apply_compact(store, output, sessions)
                for cwd in {t["cwd"] for t in tasks}:
                    store.advance_history_cursor(
                        cwd, max(t["id"] for t in tasks if t["cwd"] == cwd)
                    )
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
            store.finish_history_sync(repo, cost_usd=summary["cost_usd"], error=error)
            summary["pending"] = store.pending_history_tasks(cwds)
    summary["error"] = error
    return summary


def sync_folder(config: Config, cwd: str, **kwargs: Any) -> dict:
    with Store.open(config) as store:
        repos.ensure(store, store.task_cwds())
        repo = repos.repo_of(store, cwd)
    return sync(config, repo, **kwargs)


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


def _compact(item: dict, keys: tuple[str, ...]) -> dict:
    return {k: item[k] for k in keys if item.get(k) not in (None, "", [])}


def _as_existing(item: dict, keys: tuple[str, ...]) -> dict:
    compact = _compact(item, keys)
    return {"existing_id": compact.pop("id"), **compact}


def _active_sessions(store: Store, tasks: list[dict]) -> list[dict]:
    """Sessions of this batch that are still open: the only ones a /compact can help."""
    seen: dict[str, dict] = {}
    for task in tasks:
        sid = task.get("session_id")
        if sid and sid not in seen:
            session = store.get_session(sid)
            if session and session["state"] == "active" and session["source"] != "worker":
                seen[sid] = session
    return list(seen.values())


def apply_compact(store: Store, output: dict, sessions: list[dict]) -> int:
    allowed = {s["id"] for s in sessions}
    stored = 0
    for item in output.get("compact") or []:
        if not isinstance(item, dict) or item.get("session_id") not in allowed:
            continue
        text = _text(item.get("instructions"), _COMPACT)
        if text:
            store.update_session(item["session_id"], compact_prompt=text, compact_at=now_iso())
            stored += 1
    return stored


def build_prompt(
    problems: list[dict],
    milestones: list[dict],
    tasks: list[dict],
    git: str,
    sessions: list[dict] | None = None,
) -> str:
    known = {
        "problems": [
            {
                **_as_existing(p, ("id", "title", "state", "topic", "cause")),
                "attempts": [
                    _as_existing(a, ("id", "seq", "description", "outcome", "why"))
                    for a in p["attempts"]
                ],
            }
            for p in problems
        ],
        "milestones": [
            _as_existing(m, ("id", "happened_on", "title", "topic")) for m in milestones
        ],
    }
    lines = ["<history>", json.dumps(known, ensure_ascii=False), "</history>", "", "<tasks>"]
    for task in tasks:
        lines.append(f'<task id="{task["id"]}" date="{_day(task)}" status="{task["status"]}">')
        lines.append(f"<asked>{_clip(task['body'], PROMPT_CHARS)}</asked>")
        if task["result"]:
            lines.append(f"<answered>{_clip(task['result'], REPLY_CHARS)}</answered>")
        lines.append("</task>")
    lines.append("</tasks>")
    if sessions:
        lines += ["", "<sessions>"]
        lines += [
            f'<session id="{s["id"]}" title="{(s.get("title") or "").replace(chr(34), "")}"/>'
            for s in sessions
        ]
        lines.append("</sessions>")
    if git:
        lines += ["", "<git_log>", git, "</git_log>"]
    return "\n".join(lines)


def git_log(cwd: str, tasks: list[dict]) -> str:
    days = sorted(d for d in (_day(t) for t in tasks) if _DATE_RE.match(d))
    if not days or not Path(cwd).is_dir():
        return ""
    since = date.fromisoformat(days[0]) - timedelta(days=1)
    until = date.fromisoformat(days[-1]) + timedelta(days=1)
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                cwd,
                "log",
                "--all",
                f"--since={since}",
                f"--until={until}",
                "--date=short",
                "--format=%h %ad %s",
                f"-n{GIT_COMMITS}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def call_model(
    config: Config, model: str, prompt: str, *, run: Runner = subprocess.run
) -> tuple[dict, float]:
    env = dict(os.environ)
    env["TASKY_HOOKS_OFF"] = "1"
    env.pop("TASKY_TASK_ID", None)
    config.home.mkdir(parents=True, exist_ok=True, mode=0o700)
    cmd = [
        config.claude_bin,
        "-p",
        "--model",
        model,
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(OUTPUT_SCHEMA),
    ]
    try:
        # Run from Tasky's home, not the project: the project's CLAUDE.md would
        # only add tokens and instructions meant for coding, not for this.
        proc = run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(config.home),
            timeout=CALL_TIMEOUT_S,
            check=False,
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


# -- applying the model's output ---------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]


def _date(value: Any) -> str | None:
    return value if isinstance(value, str) and _DATE_RE.match(value) else None


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


class _Batch:
    """What the model was shown, used to check what it returns."""

    def __init__(self, tasks: list[dict], git: str) -> None:
        self.tasks = {t["id"]: t for t in tasks}
        self.hashes = {line.split(" ", 1)[0] for line in git.splitlines() if line}

    def cited(self, value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        return [
            i for i in value if isinstance(i, int) and not isinstance(i, bool) and i in self.tasks
        ]

    def commits(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            c
            for c in value
            if isinstance(c, str)
            and _HASH_RE.match(c)
            and any(h.startswith(c) or c.startswith(h) for h in self.hashes)
        ]

    def quote(self, value: Any, task_ids: list[int]) -> str:
        """The quote if it appears verbatim (modulo whitespace and case) in a cited task."""
        text = _text(value, _QUOTE)
        if text is None:
            return ""
        needle = _norm(text.strip("\"'“”«» …."))
        if len(needle) < 8:
            return ""
        for task_id in task_ids or list(self.tasks):
            task = self.tasks.get(task_id)
            if task and needle in _norm(f"{task['body'] or ''}\n{task['result'] or ''}"):
                return text
        return ""

    def first_cwd(self, task_ids: list[int]) -> str:
        return self.tasks[task_ids[0]]["cwd"]


def apply_output(
    store: Store, repo: str, output: dict, tasks: list[dict], git: str
) -> tuple[int, int]:
    """Store what the model returned, trusting none of it; returns (added, updated)."""
    batch = _Batch(tasks, git)
    in_repo = set(store.repo_cwds(repo))
    added = updated = 0
    for item in output.get("milestones") or []:
        if isinstance(item, dict):
            a, u = _apply_milestone(store, batch, in_repo, item)
            added, updated = added + a, updated + u
    for item in output.get("problems") or []:
        if isinstance(item, dict):
            a, u = _apply_problem(store, batch, in_repo, item)
            added, updated = added + a, updated + u
    return added, updated


def _existing_id(item: dict) -> int | None:
    value = item.get("existing_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _apply_milestone(store: Store, batch: _Batch, in_repo: set[str], item: dict) -> tuple[int, int]:
    cited = batch.cited(item.get("task_ids"))
    fields = {
        "title": _text(item.get("title"), _TITLE),
        "detail": _text(item.get("detail"), _TEXT),
        "topic": _text(item.get("topic"), _TOPIC),
        "happened_on": _date(item.get("happened_on")),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    commits = batch.commits(item.get("commits"))
    milestone_id = _existing_id(item)
    current = store.get_milestone(milestone_id) if milestone_id is not None else None
    if current is not None and current["cwd"] not in in_repo:
        current = None  # an id the model was not shown: the record is new
    if current is not None:
        assert milestone_id is not None
        if cited:
            fields["task_ids"] = [*current["task_ids"], *cited]
        if commits:
            fields["commits"] = [*current["commits"], *commits]
        if not fields:
            return 0, 0
        store.update_milestone(milestone_id, **fields)
        return 0, 1
    if "title" not in fields or not cited:
        return 0, 0
    store.add_milestone(
        cwd=batch.first_cwd(cited),
        task_ids=cited,
        commits=commits,
        **{"detail": "", **fields},
    )
    return 1, 0


def _apply_problem(store: Store, batch: _Batch, in_repo: set[str], item: dict) -> tuple[int, int]:
    cited = batch.cited(item.get("task_ids"))
    attempts = [a for a in item.get("attempts") or [] if isinstance(a, dict)]
    attempt_cites = [i for a in attempts for i in batch.cited(a.get("task_ids"))]
    happened = _date(item.get("happened_on"))
    fields = {
        "title": _text(item.get("title"), _TITLE),
        "symptom": _text(item.get("symptom"), _TEXT),
        "cause": _text(item.get("cause"), _TEXT),
        "topic": _text(item.get("topic"), _TOPIC),
        "state": item.get("state") if item.get("state") in STATES else None,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    problem_id = _existing_id(item)
    current = store.get_problem(problem_id) if problem_id is not None else None
    if current is not None and current["cwd"] not in in_repo:
        current = None  # an id the model was not shown: the record is new
    added = updated = 0
    if current is not None:
        assert problem_id is not None
        all_cited = [*cited, *attempt_cites]
        if all_cited:
            fields["task_ids"] = [*current["task_ids"], *all_cited]
        if happened and (current["last_seen"] or "") < happened:
            fields["last_seen"] = happened
        if fields:
            store.update_problem(problem_id, **fields)
            updated += 1
        known_attempts = {a["id"] for a in current["attempts"]}
    else:
        evidence_ids = [*cited, *attempt_cites]
        if "title" not in fields or not evidence_ids:
            return 0, 0
        problem_id = store.add_problem(
            cwd=batch.first_cwd(evidence_ids),
            first_seen=happened,
            last_seen=happened,
            task_ids=evidence_ids,
            **{"symptom": "", "cause": "", "state": "open", **fields},
        )
        added += 1
        known_attempts = set()
    worked = False
    for attempt in attempts:
        a, u, ok = _apply_attempt(store, batch, problem_id, known_attempts, attempt, cited)
        added, updated, worked = added + a, updated + u, worked or ok
    if worked and "state" not in fields:
        store.update_problem(problem_id, state="solved")
    return added, updated


def _apply_attempt(
    store: Store,
    batch: _Batch,
    problem_id: int,
    known: set[int],
    item: dict,
    problem_cites: list[int],
) -> tuple[int, int, bool]:
    cited = batch.cited(item.get("task_ids"))
    outcome = item.get("outcome") if item.get("outcome") in OUTCOMES else None
    fields = {
        "description": _text(item.get("description"), _TEXT),
        "outcome": outcome,
        "why": _text(item.get("why"), _TEXT),
        "believed_from": _date(item.get("believed_from")),
        "invalidated_on": _date(item.get("invalidated_on")),
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    evidence = batch.quote(item.get("evidence"), cited or problem_cites)
    if evidence:
        fields["evidence"] = evidence
    commits = batch.commits(item.get("commits"))
    attempt_id = _existing_id(item)
    worked = outcome == "worked"
    current = store.get_attempt(attempt_id) if attempt_id in known else None
    if current is not None:
        assert attempt_id is not None
        if cited:
            fields["task_ids"] = [*current["task_ids"], *cited]
        if commits:
            fields["commits"] = [*current["commits"], *commits]
        if not fields:
            return 0, 0, False
        store.update_attempt(attempt_id, **fields)
        return 0, 1, worked
    if "description" not in fields or not (cited or problem_cites):
        return 0, 0, False
    store.add_attempt(
        problem_id,
        task_ids=cited or problem_cites,
        commits=commits,
        source="sync",
        **{"outcome": "pending", "why": "", "evidence": "", **fields},
    )
    return 1, 0, worked
