"""Compact dashboard: a repository's finished tasks grouped into issue-tracker cards.

A card is one piece of work, the way Jira would hold it: title, kind, status, objective,
description, acceptance criteria, and the tasks, commits, problems and milestones behind it.
Haiku writes them, only on demand and only once the history sync is up to date, because the
sync already did the judging (what failed, what was decided) and the cards just group and
word it. Like the sync, nothing it returns is trusted: tasks, commits, problems and milestones
must be ones it was shown, and a criterion counts as stated by the developer only when its quote
appears verbatim in one of the card's prompts; every other criterion is marked inferred. The
files of a card come from the edits its tasks recorded, never from the model. The language
the cards are written in is the one the developer's prompts are in, detected without a model,
named to Haiku outright and checked on every card it returns; a card that comes back in another
language is sent back for translation alone.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from typing import Any

from tasky import areas as area_rules
from tasky import history, language
from tasky.config import Config
from tasky.store import Store

MODEL = "haiku"
KINDS = ("story", "bug", "chore", "spike")
STATUSES = ("done", "in_progress", "blocked", "dropped")
BATCH_TASKS = 60
BATCH_CHARS = 45_000
PROMPT_CHARS = 700
REPLY_CHARS = 900
KNOWN_CARDS = 40
KNOWN_PROBLEMS = 40
KNOWN_MILESTONES = 30
TASK_FILES = 10
CARD_FILES = 25
MAX_CRITERIA = 6
MAX_BATCHES = 10
STALE_RUN_S = 20 * 60
_TITLE = 120
_TEXT = 600
_CRITERION = 200
_QUOTE = 200

SYSTEM_PROMPT = """\
You turn a developer's log of Claude Code tasks in one software repository into issue-tracker \
cards, like Jira: one card per piece of work (a feature, a fix, a chore, an investigation), \
grouping the tasks that worked on it. You receive the cards kept so far, the new tasks with the \
files each one edited, the repository's areas, the problems and milestones its history already \
records, and the git log of the same days.

- Put every new task in exactly one card. When a task continues the work of a card listed in \
<cards>, add it there (give that card's existing_id); otherwise open a new card. Tasks about the \
same thing share one card; a question that changed nothing joins the card it was about.
- title: the work, like an issue title, under 10 words.
- kind: "story" (a new capability), "bug" (something broken, fixed or not), "chore" \
(maintenance, configuration, docs, refactoring), "spike" (investigation, question, plan).
- status: "done" when the tasks show the work finished; "in_progress" when work remains; \
"blocked" when an open problem stops it; "dropped" when it was abandoned.
- objective: one sentence, why the work was done. description: what was done, 1 to 3 concrete \
sentences (names, paths, behaviour), based only on what you were shown.
- criteria: the acceptance criteria the work must meet, at most 5, each a condition someone \
could check on the result ("a coupon cannot be applied twice", "the build fails below 30% \
coverage"), not a summary of what happened. When the developer stated a condition in a task, \
copy their exact words into quote (under 150 characters); otherwise leave quote empty and the \
criterion counts as inferred.
- area: the name of an area in <areas> the work belongs to, if one fits.
- task_ids: only ids of new tasks shown in <tasks>. problem_ids and milestone_ids: only ids \
listed in <problems> and <milestones>. commits: short hashes from <git_log>, only when a commit \
clearly belongs to this work.
- For an existing card, return only what changes: its existing_id, the new task_ids, and any \
field whose value should now be different (for example its status).
- Write title, objective, description and every criterion's text in the language named in \
<language>: the developer's. The replies, code and git log you are shown may be in English; \
that does not change the language of the cards. A quote stays exactly as the developer wrote \
it. Never invent ids.
"""

TRANSLATE_PROMPT = """\
You translate issue-tracker cards. Rewrite every field you are given in the language the \
request names, keeping its meaning. Leave names, file paths, identifiers, commands, code and \
product names exactly as they are. Return each card with the same index, the same fields and \
its criteria in the same order.
"""

TRANSLATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                    "description": {"type": "string"},
                    "criteria": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index"],
            },
        }
    },
    "required": ["cards"],
}
_WORDED = ("title", "objective", "description")

_CRITERION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
    "required": ["text"],
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "objective": {"type": "string"},
                    "description": {"type": "string"},
                    "area": {"type": "string"},
                    "criteria": {"type": "array", "items": _CRITERION_SCHEMA},
                    "task_ids": {"type": "array", "items": {"type": "integer"}},
                    "problem_ids": {"type": "array", "items": {"type": "integer"}},
                    "milestone_ids": {"type": "array", "items": {"type": "integer"}},
                    "commits": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["task_ids"],
            },
        }
    },
    "required": ["cards"],
}


class CardsError(Exception):
    """Cards cannot be made now (history behind, a run in progress) or a call failed."""


def _day(task: dict) -> str:
    return (task.get("finished_at") or task.get("created_at") or "")[:10]


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + " […]"


def _text(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _ints(value: Any, allowed: set[int]) -> list[int]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        i for i in value if isinstance(i, int) and not isinstance(i, bool) and i in allowed
    ))


def _asked(task: dict) -> str:
    """What the developer wrote: the prompt and anything added while it ran."""
    extra = [f.get("text") or "" for f in task.get("followups") or [] if isinstance(f, dict)]
    return "\n".join([task.get("body") or "", *extra])


def developer_language(tasks: list[dict]) -> str | None:
    """The language code the developer's prompts are in, None when they do not say."""
    return language.detect("\n".join(_asked(t) for t in tasks))


def _card_text(item: dict) -> str:
    criteria = item.get("criteria") if isinstance(item.get("criteria"), list) else []
    texts = [item.get(k) for k in _WORDED]
    texts += [c.get("text") for c in criteria if isinstance(c, dict)]
    return "\n".join(t for t in texts if isinstance(t, str))


def off_language(output: dict, code: str | None) -> list[int]:
    """Indexes of the returned cards written in a language other than the developer's."""
    if code is None:
        return []
    items = output.get("cards") if isinstance(output.get("cards"), list) else []
    return [
        i for i, item in enumerate(items)
        if isinstance(item, dict) and language.detect(_card_text(item)) not in (None, code)
    ]


def translate(
    config: Config, output: dict, indexes: list[int], code: str, *, run: history.Runner
) -> tuple[int, float]:
    """Rewrite those cards' wording in the developer's language, in place.

    Only the wording goes back to the model: which tasks a card holds was already decided and is
    not asked again. A quote is the developer's own words and never leaves. Returns how many cards
    came back in the right language, and the cost.
    """
    items = output["cards"]
    asked = []
    for i in indexes:
        item = items[i]
        entry: dict[str, Any] = {"index": i}
        entry.update({k: item[k] for k in _WORDED if isinstance(item.get(k), str)})
        criteria = [c for c in item.get("criteria") or [] if isinstance(c, dict)]
        if criteria:
            entry["criteria"] = [str(c.get("text") or "") for c in criteria]
        asked.append(entry)
    prompt = (
        f"Translate into {language.name(code)}.\n\n<cards>\n"
        + "\n".join(json.dumps(e, ensure_ascii=False) for e in asked)
        + f"\n</cards>\n\nEvery field in {language.name(code)}."
    )
    result, cost = history.call_model(
        config, MODEL, prompt, run=run, system_prompt=TRANSLATE_PROMPT, schema=TRANSLATE_SCHEMA,
    )
    fixed = 0
    wanted = set(indexes)
    for back in result.get("cards") or []:
        if not isinstance(back, dict) or back.get("index") not in wanted:
            continue
        item = items[back["index"]]
        candidate = dict(item)
        for key in _WORDED:
            if isinstance(item.get(key), str) and _text(back.get(key), 10_000):
                candidate[key] = back[key]
        texts = back.get("criteria")
        criteria = [c for c in item.get("criteria") or [] if isinstance(c, dict)]
        if isinstance(texts, list) and len(texts) == len(criteria):
            candidate["criteria"] = [
                {**c, "text": t} if isinstance(t, str) and t.strip() else c
                for c, t in zip(criteria, texts, strict=True)
            ]
        if language.detect(_card_text(candidate)) in (None, code):
            items[back["index"]] = candidate
            fixed += 1
        wanted.discard(back["index"])
    return fixed, cost


def _batch(tasks: list[dict]) -> list[dict]:
    kept: list[dict] = []
    used = 0
    for task in tasks:
        size = min(len(_asked(task)), PROMPT_CHARS) + min(len(task["result"] or ""), REPLY_CHARS)
        if kept and used + size > BATCH_CHARS:
            break
        kept.append(task)
        used += size + 200
    return kept


def _task_files(store: Store, repo: str, tasks: list[dict]) -> dict[int, list[str]]:
    """Task id → repo-relative files it edited."""
    roots = store.repo_roots(repo)
    edited = store.edited_files_by_prompt(t["prompt_id"] for t in tasks if t.get("prompt_id"))
    files: dict[int, list[str]] = {}
    for task in tasks:
        rels = (area_rules.relative(f, roots) for f in edited.get(task.get("prompt_id"), []))
        files[task["id"]] = list(dict.fromkeys(r for r in rels if r))
    return files


def build_prompt(
    tasks: list[dict],
    files: dict[int, list[str]],
    task_areas: dict[int, list[dict]],
    cards: list[dict],
    problems: list[dict],
    milestones: list[dict],
    areas: list[dict],
    git: str,
    code: str | None = None,
) -> str:
    written_in = language.name(code) or "the language the developer writes the tasks in"
    lines: list[str] = [f"<language>{written_in}</language>", ""]
    if areas:
        lines += ["<areas>", ", ".join(a["name"] for a in areas), "</areas>", ""]
    lines.append("<cards>")
    for card in cards:
        lines.append(json.dumps({
            "existing_id": card["id"], "title": card["title"], "kind": card["kind"],
            "status": card["status"], "area": card["area"], "last_on": card["last_on"],
            "tasks": len(card["task_ids"]),
        }, ensure_ascii=False))
    lines += ["</cards>", "", "<problems>"]
    for p in problems:
        lines.append(f'#{p["id"]} [{p["state"]}] {p["title"]} (tasks {p["task_ids"][-8:]})')
    lines += ["</problems>", "", "<milestones>"]
    for m in milestones:
        when = m["happened_on"] or "?"
        lines.append(f'#{m["id"]} {when} {m["title"]} (tasks {m["task_ids"][-8:]})')
    lines += ["</milestones>", "", "<tasks>"]
    for task in tasks:
        head = f'<task id="{task["id"]}" date="{_day(task)}" status="{task["status"]}"'
        names = [a["name"] for a in task_areas.get(task["id"], [])]
        if names:
            head += f' areas="{", ".join(names)}"'
        lines.append(head + ">")
        lines.append(f"<asked>{_clip(_asked(task), PROMPT_CHARS)}</asked>")
        if task["result"]:
            lines.append(f"<answered>{_clip(task['result'], REPLY_CHARS)}</answered>")
        edited = files.get(task["id"]) or []
        if edited:
            more = f" (+{len(edited) - TASK_FILES} more)" if len(edited) > TASK_FILES else ""
            lines.append(f"<edited>{', '.join(edited[:TASK_FILES])}{more}</edited>")
        lines.append("</task>")
    lines.append("</tasks>")
    if git:
        lines += ["", "<git_log>", git, "</git_log>"]
    lines += ["", f"Write every card in {written_in}."]
    return "\n".join(lines)


class _Shown:
    """What one batch showed the model, to check what it returns."""

    def __init__(self, tasks, cards, problems, milestones, areas, git) -> None:
        self.tasks = {t["id"]: t for t in tasks}
        self.cards = {c["id"] for c in cards}
        self.problems = {p["id"] for p in problems}
        self.milestones = {m["id"] for m in milestones}
        self.areas = areas
        self.commits = history._Batch(tasks, git).commits

    def area(self, value: Any) -> str | None:
        area_id = area_rules.by_name(value, self.areas) if isinstance(value, str) else None
        return next((a["name"] for a in self.areas if a["id"] == area_id), None)

    def criteria(self, value: Any, task_ids: list[int]) -> list[dict]:
        """Checked criteria: "stated" only when the quote is in one of these tasks' prompts."""
        if not isinstance(value, list):
            return []
        asked = [history._norm(_asked(self.tasks[i])) for i in task_ids if i in self.tasks]
        kept: list[dict] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            text = _text(item.get("text"), _CRITERION)
            if text is None:
                continue
            quote = _text(item.get("quote"), _QUOTE) or ""
            needle = history._norm(quote.strip("\"'“”«» …."))
            if len(needle) >= 8 and any(needle in a for a in asked):
                kept.append({"text": text, "source": "stated", "quote": quote})
            else:
                kept.append({"text": text, "source": "inferred"})
            if len(kept) >= MAX_CRITERIA:
                break
        return kept


def apply_output(store: Store, repo: str, output: dict, shown: _Shown) -> dict[str, Any]:
    """Store the cards the model returned, trusting none of it."""
    added = updated = 0
    placed: set[int] = set()
    for item in output.get("cards") or []:
        if not isinstance(item, dict):
            continue
        task_ids = [i for i in _ints(item.get("task_ids"), set(shown.tasks)) if i not in placed]
        days = sorted(d for d in (_day(shown.tasks[i]) for i in task_ids) if d)
        fields: dict[str, Any] = {
            "title": _text(item.get("title"), _TITLE),
            "kind": item.get("kind") if item.get("kind") in KINDS else None,
            "status": item.get("status") if item.get("status") in STATUSES else None,
            "objective": _text(item.get("objective"), _TEXT),
            "description": _text(item.get("description"), _TEXT),
            "area": shown.area(item.get("area")),
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        criteria = shown.criteria(item.get("criteria"), task_ids)
        problem_ids = _ints(item.get("problem_ids"), shown.problems)
        milestone_ids = _ints(item.get("milestone_ids"), shown.milestones)
        commits = shown.commits(item.get("commits"))
        existing = item.get("existing_id")
        current = store.get_card(existing) if existing in shown.cards else None
        if current is not None and current["repo"] == repo:
            merged = {c["text"] for c in current["criteria"] if isinstance(c, dict)}
            new_criteria = [c for c in criteria if c["text"] not in merged]
            changes = {
                **fields,
                "task_ids": [*current["task_ids"], *task_ids],
                "commits": [*current["commits"], *commits],
                "problem_ids": [*current["problem_ids"], *problem_ids],
                "milestone_ids": [*current["milestone_ids"], *milestone_ids],
                "criteria": [*current["criteria"], *new_criteria][: MAX_CRITERIA + 2],
            }
            if days:
                changes["first_on"] = min(filter(None, [current["first_on"], days[0]]))
                changes["last_on"] = max(filter(None, [current["last_on"], days[-1]]))
            for key in ("task_ids", "commits", "problem_ids", "milestone_ids"):
                changes[key] = list(dict.fromkeys(changes[key]))
            store.update_card(current["id"], **changes)
            placed.update(task_ids)
            updated += 1
            continue
        if not task_ids or "title" not in fields:
            continue
        store.add_card(
            repo, **{"kind": "story", "status": "done", **fields}, criteria=criteria,
            task_ids=task_ids, commits=commits, problem_ids=problem_ids,
            milestone_ids=milestone_ids, first_on=days[0] if days else None,
            last_on=days[-1] if days else None,
        )
        placed.update(task_ids)
        added += 1
    return {"added": added, "updated": updated, "placed": placed}


def readiness(store: Store, repo: str) -> dict[str, Any]:
    """Whether cards can be made now, and why not."""
    cwds = store.repo_cwds(repo)
    sync = store.history_sync(repo)
    run = store.card_run(repo)
    return {
        "history_pending": store.pending_history_tasks(cwds),
        "history_running": bool(sync and sync["state"] == "running"),
        "pending": store.pending_card_tasks(repo),
        "running": bool(run and run["state"] == "running"),
        "run": run,
    }


def compact(
    config: Config, repo: str, *, run: history.Runner = subprocess.run
) -> dict[str, Any]:
    """Turn the repo's finished tasks past the cursor into cards; returns a summary."""
    with Store.open(config) as store:
        cwds = store.repo_cwds(repo)
        if not cwds:
            raise CardsError(f"no recorded folders belong to {repo}")
        ready = readiness(store, repo)
        if ready["history_running"]:
            raise CardsError("the history of this repository is syncing; wait for it to finish")
        if ready["history_pending"]:
            raise CardsError(
                f"sync the history first: {ready['history_pending']} task(s) are not synced"
            )
        if not store.begin_card_run(repo, stale_after_s=STALE_RUN_S):
            raise CardsError("cards of this repository are already being made")
    summary: dict[str, Any] = {"batches": 0, "tasks": 0, "added": 0, "updated": 0,
                               "left_out": 0, "cost_usd": 0.0, "model": MODEL,
                               "language": None, "translated": 0, "untranslated": 0}
    error: str | None = None
    try:
        for _ in range(MAX_BATCHES):
            with Store.open(config) as store:
                tasks = _batch(store.tasks_for_cards(repo, BATCH_TASKS))
                if not tasks:
                    break
                ids = {t["id"] for t in tasks}
                files = _task_files(store, repo, tasks)
                task_areas = store.task_areas(tasks)
                cards = store.cards(repo)[:KNOWN_CARDS]
                problems = [p for p in store.problems(repo)
                            if ids & set(p["task_ids"]) or p["state"] != "solved"]
                problems = problems[:KNOWN_PROBLEMS]
                milestones = [m for m in store.milestones(repo) if ids & set(m["task_ids"])]
                milestones = milestones[-KNOWN_MILESTONES:]
                areas = store.areas(repo)
            git = history.git_log(cwds[0], tasks)
            code = developer_language(tasks)
            summary["language"] = language.name(code) or summary["language"]
            prompt = build_prompt(tasks, files, task_areas, cards, problems, milestones, areas,
                                  git, code)
            try:
                output, cost = history.call_model(
                    config, MODEL, prompt, run=run, system_prompt=SYSTEM_PROMPT,
                    schema=OUTPUT_SCHEMA,
                )
            except history.SyncError as exc:
                raise CardsError(str(exc)) from exc
            summary["cost_usd"] += cost
            wrong = off_language(output, code)
            if wrong and code is not None:
                try:
                    fixed, cost = translate(config, output, wrong, code, run=run)
                except history.SyncError:
                    fixed, cost = 0, 0.0  # the cards are kept as written; the count says so
                summary["cost_usd"] += cost
                summary["translated"] += fixed
                summary["untranslated"] += len(wrong) - fixed
            shown = _Shown(tasks, cards, problems, milestones, areas, git)
            with Store.open(config) as store:
                result = apply_output(store, repo, output, shown)
                store.advance_card_cursor(repo, max(ids))
            summary["batches"] += 1
            summary["tasks"] += len(tasks)
            summary["added"] += result["added"]
            summary["updated"] += result["updated"]
            summary["left_out"] += len(ids - result["placed"])
    except CardsError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the run row, never left 'running'
        error = f"{type(exc).__name__}: {exc}"
    finally:
        with Store.open(config) as store:
            store.finish_card_run(repo, cost_usd=summary["cost_usd"], error=error)
            summary["pending"] = store.pending_card_tasks(repo)
    summary["error"] = error
    return summary


def view_one(store: Store, card_id: int) -> list[dict]:
    """One card, filled in like view() fills them; empty when it does not exist."""
    card = store.get_card(card_id)
    return [] if card is None else _fill(store, [card], card["repo"])


def view(store: Store, repo: str | None) -> list[dict]:
    """Cards with what the ledger knows about them: files, tasks, problems, milestones."""
    return _fill(store, store.cards(repo), repo)


def _fill(store: Store, cards: list[dict], repo: str | None) -> list[dict]:
    if not cards:
        return []
    task_ids = {i for c in cards for i in c["task_ids"]}
    tasks = {t["id"]: t for t in (store.get_task(i) for i in task_ids) if t is not None}
    problems = {p["id"]: p for p in store.problems(repo)}
    milestones = {m["id"]: m for m in store.milestones(repo)}
    areas_by_task = store.task_areas(list(tasks.values()))
    files: dict[int, list[str]] = {}
    for card_repo in {c["repo"] for c in cards}:
        repo_tasks = [tasks[i] for c in cards if c["repo"] == card_repo
                      for i in c["task_ids"] if i in tasks]
        files.update(_task_files(store, card_repo, repo_tasks))
    for card in cards:
        mine = [tasks[i] for i in card["task_ids"] if i in tasks]
        counts: Counter[str] = Counter(f for t in mine for f in files.get(t["id"], []))
        card["files"] = [f for f, _ in counts.most_common(CARD_FILES)]
        card["files_total"] = len(counts)
        card["tasks"] = [
            {"id": t["id"], "title": t["title"], "status": t["status"], "date": _day(t)}
            for t in mine
        ]
        card["problems"] = [
            {"id": p["id"], "title": p["title"], "state": p["state"]}
            for p in (problems.get(i) for i in card["problem_ids"]) if p is not None
        ]
        card["milestones"] = [
            {"id": m["id"], "title": m["title"], "happened_on": m["happened_on"]}
            for m in (milestones.get(i) for i in card["milestone_ids"]) if m is not None
        ]
        if not card["area"]:
            votes: Counter[str] = Counter(
                a["name"] for t in mine for a in areas_by_task.get(t["id"], [])
            )
            card["area"] = votes.most_common(1)[0][0] if votes else None
    return cards
