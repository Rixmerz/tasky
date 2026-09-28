"""Model router: start each launched task on the model and effort its kind of work calls for.

Runs inside the detached supervisor (`tasky.supervise`), never in the HTTP handler or the
scheduler tick, because classifying a task shells out to muscle-memory's `mm-classifier` and can
take seconds. The classifier chain lives in `tasky/router/*.json` as ordinary `mm-classifier`
definitions: keyword rules first, and a final catch-all rule so a task no rule recognises is
`default` without ever contacting a Laya server.

Every path that cannot decide -- router off, no classifier, a failing or slow classifier, a kind
whose models are all switched off -- adds no flags, so the `claude` command stays exactly what it
would be without a router.

Two things reach the dashboard before a task runs. The *preview* is the kind the classifier gives
the task's current text, cached on the task by `refresh_previews` (run from a server background
thread, never a request) and turned into a model and effort at read time by `preview`, so a
switched model or the router itself turning off shows at once without classifying again. The
*pin* is a model the user chose for one task (`pinned_model`, `pinned_effort`): it replaces
classification, and applies whether or not the router is on or that model is switched on.
"""

from __future__ import annotations

import functools
import glob
import hashlib
import json
import os
import re
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

MODELS = ("fable", "opus", "sonnet", "haiku")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLASSIFIER_TIMEOUT_S = 10.0
ROOT_CLASSIFIER = Path(__file__).resolve().parent / "router" / "task-kind.json"
DEFAULT_LAYA_URL = "http://127.0.0.1:8177"
# `pinned_model` value meaning "run on the session default, do not route this task".
SESSION_DEFAULT = "default"


@dataclass(frozen=True)
class Choice:
    model: str
    effort: str | None = None


# Kind -> (first choice, second choice), from Anthropic's model x effort guidance. `high` is the
# CLI default and Haiku takes no effort, so neither is ever passed (see `flags`).
ROUTES: dict[str, tuple[Choice | None, Choice | None]] = {
    "plan": (Choice("opus", "xhigh"), Choice("sonnet", "xhigh")),
    "debug-unclear": (Choice("opus", "xhigh"), Choice("sonnet", "high")),
    "debug-repro": (Choice("sonnet", "high"), Choice("sonnet", "medium")),
    "feature": (Choice("opus", "xhigh"), Choice("sonnet", "high")),
    "implement": (Choice("sonnet", "high"), Choice("sonnet", "medium")),
    "ui": (Choice("sonnet", "high"), Choice("sonnet", "medium")),
    "refactor": (Choice("sonnet", "medium"), None),
    "review-critical": (Choice("opus", "high"), Choice("sonnet", "high")),
    "review-routine": (Choice("sonnet", "medium"), Choice("haiku")),
    "test": (Choice("sonnet", "medium"), Choice("haiku")),
    "docs": (Choice("sonnet", "medium"), Choice("haiku")),
    "explore": (Choice("haiku"), Choice("sonnet", "low")),
    "chat": (Choice("sonnet", "low"), Choice("haiku")),
    "frontier": (Choice("fable", "high"), Choice("opus", "max")),
    "default": (None, None),
}


@dataclass
class Decision:
    kind: str | None = None
    model: str | None = None
    effort: str | None = None
    reason: str = ""
    args: list[str] = field(default_factory=list)
    pinned: bool = False


def flags(choice: Choice) -> list[str]:
    args = ["--model", choice.model]
    if choice.effort not in (None, "high") and choice.model != "haiku":
        args += ["--effort", choice.effort]
    return args


def choose(kind: str, allowed: Mapping[str, bool]) -> tuple[Choice | None, str]:
    """The first choice for `kind` whose model is allowed, else the second, else none."""
    first, second = ROUTES.get(kind, (None, None))
    if first is None and second is None:
        return None, "no route for this kind"
    for choice in (first, second):
        if choice is not None and allowed.get(choice.model, True):
            return choice, ""
    return None, "its models are switched off"


def _version_key(path: str) -> tuple[int, ...]:
    # .../muscle-memory/<version>/bin/mm-classifier.mjs
    version = Path(path).parent.parent.name
    return tuple(int(part) for part in re.findall(r"\d+", version))


def find_classifier(env: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    explicit = env.get("TASKY_MM_CLASSIFIER")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    config_dir = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    pattern = str(config_dir / "plugins" / "cache" / "*" / "muscle-memory" / "*" / "bin")
    found = glob.glob(os.path.join(pattern, "mm-classifier.mjs"))
    return Path(max(found, key=_version_key)) if found else None


def laya_up(env: Mapping[str, str] | None = None, timeout: float = 0.5) -> bool:
    env = os.environ if env is None else env
    url = (env.get("MM_LAYA_URL") or DEFAULT_LAYA_URL).rstrip("/") + "/health"
    if not url.startswith(("http://127.0.0.1", "http://localhost")):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 -- loopback only
            return resp.status == 200
    except OSError:
        return False


Runner = Callable[..., subprocess.CompletedProcess]


def classify(
    task: Mapping[str, object], classifier: Path, runner: Runner = subprocess.run
) -> tuple[str, str]:
    """Return (kind, how it was decided). Raises `RuntimeError` when no kind can be read."""
    payload = json.dumps({"title": task.get("title") or "", "body": task.get("body") or ""})
    try:
        proc = runner(
            ["node", str(classifier), "run", str(ROOT_CLASSIFIER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=CLASSIFIER_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"classifier did not run: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        raise RuntimeError(f"classifier failed: {detail[0]}")
    try:
        decision = json.loads(proc.stdout)
        kind = decision["outcome"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("classifier printed no decision") from exc
    sources = []
    node = decision
    while isinstance(node, dict):
        sources.append("rule" if node.get("reason") == "rule" else "model")
        node = node.get("next")
    how = "rules" if all(s == "rule" for s in sources) else "model"
    # Fable is reserved for work the user tagged; a model's guess never reaches it.
    if kind == "frontier" and sources[0] != "rule":
        return "default", "model chose frontier without the #fable tag"
    return str(kind), how


def pinned_choice(task: Mapping[str, object]) -> tuple[bool, Choice | None]:
    """`(True, choice)` when the user chose a model for this task; a `None` choice is the
    session default. A pin naming a model Tasky no longer knows is ignored."""
    model = task.get("pinned_model")
    if model == SESSION_DEFAULT:
        return True, None
    if model not in MODELS:
        return False, None
    effort = task.get("pinned_effort")
    if model == "haiku" or effort not in EFFORTS:
        effort = None
    return True, Choice(str(model), effort)


def _applied_effort(choice: Choice) -> str | None:
    return None if choice.model == "haiku" else (choice.effort or "high")


def _pinned_decision(choice: Choice | None) -> Decision:
    if choice is None:
        return Decision(reason="session default, your choice", pinned=True)
    effort = _applied_effort(choice)
    return Decision(
        model=choice.model,
        effort=effort,
        reason=f"{choice.model}" + (f" {effort}" if effort else "") + ", your choice",
        args=flags(choice),
        pinned=True,
    )


def decide(
    task: Mapping[str, object],
    enabled: bool,
    allowed: Mapping[str, bool],
    *,
    env: Mapping[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> Decision:
    pinned, choice = pinned_choice(task)
    if pinned:
        # The user's own choice for this task: no classifier, no switches, router on or off.
        return _pinned_decision(choice)
    if not enabled:
        return Decision()
    classifier = find_classifier(env)
    if classifier is None:
        return Decision(reason="no route: mm-classifier.mjs not found (install muscle-memory)")
    try:
        kind, how = classify(task, classifier, runner)
    except RuntimeError as exc:
        return Decision(reason=f"no route: {exc}")
    choice, why_not = choose(kind, allowed)
    if choice is None:
        return Decision(kind=kind, reason=f"{kind} ({how}): no route, {why_not}")
    effort = _applied_effort(choice)
    return Decision(
        kind=kind,
        model=choice.model,
        effort=effort,
        reason=f"{kind} ({how}) -> {choice.model}" + (f" {effort}" if effort else ""),
        args=flags(choice),
    )


@functools.cache
def _rules() -> str:
    """A digest of the committed classifier definitions, so new rules invalidate old previews."""
    digest = hashlib.sha256()
    for path in sorted(ROOT_CLASSIFIER.parent.glob("*.json")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def preview_key(task: Mapping[str, object], classifier: Path | None) -> str:
    """What a cached preview depends on: the text the classifier reads and the classifier."""
    material = json.dumps(
        [task.get("title") or "", task.get("body") or "", str(classifier or ""), _rules()]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def refresh_previews(
    store,
    settings: Mapping[str, object],
    *,
    env: Mapping[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> int:
    """Classify every waiting task whose cached kind is missing or stale; return how many.

    Slow (one `node` run per task), so it only ever runs off the request path. A failure is
    cached like a kind, under the same key, so a broken classifier is not retried every poll;
    a new classifier path, new rules or new task text all change the key.
    """
    if not settings.get("enabled"):
        return 0
    classifier = find_classifier(env)
    count = 0
    for task in store.list_tasks(status="queued"):
        if pinned_choice(task)[0]:
            continue
        key = preview_key(task, classifier)
        if task.get("preview_key") == key:
            continue
        if classifier is None:
            kind, note = None, "mm-classifier.mjs not found (install muscle-memory)"
        else:
            try:
                kind, note = classify(task, classifier, runner)
            except RuntimeError as exc:
                kind, note = None, str(exc)
        try:
            store.update_task(task["id"], preview_key=key, preview_kind=kind, preview_note=note)
        except KeyError:  # deleted while it was being classified
            continue
        count += 1
    return count


def preview(
    task: Mapping[str, object], settings: Mapping[str, object], classifier: Path | None
) -> dict | None:
    """What this waiting task would run on if it started now, or None when nothing would change.

    `source` is `pinned` (the user's choice), `router` (from the cached kind; a null `model`
    means the session default) or `pending` (not classified yet, or its text changed since).
    """
    pinned, choice = pinned_choice(task)
    if pinned:
        if choice is None:
            return {"source": "pinned", "model": None, "effort": None, "reason": "your choice"}
        return {
            "source": "pinned",
            "model": choice.model,
            "effort": _applied_effort(choice),
            "reason": "your choice",
        }
    if not settings.get("enabled"):
        return None
    if task.get("preview_key") != preview_key(task, classifier):
        return {"source": "pending", "model": None, "effort": None, "reason": "classifying"}
    kind = task.get("preview_kind")
    if kind is None:
        reason = f"no route: {task.get('preview_note') or 'no kind'}"
        return {"source": "router", "model": None, "effort": None, "reason": reason}
    routed, why_not = choose(str(kind), settings.get("models") or {})
    how = task.get("preview_note") or "rules"
    if routed is None:
        reason = f"{kind} ({how}): {why_not}"
        return {"source": "router", "model": None, "effort": None, "reason": reason}
    return {
        "source": "router",
        "model": routed.model,
        "effort": _applied_effort(routed),
        "reason": f"{kind} ({how})",
    }


def load_settings(store) -> dict:
    """`{"enabled": bool, "models": {model: allowed}}`, from the store's `meta` flags."""
    return {
        "enabled": store.meta_flag("router_enabled", False),
        "models": {m: store.meta_flag(f"router_model_{m}", True) for m in MODELS},
    }


def save_settings(store, enabled: bool | None = None, models: Mapping[str, bool] | None = None):
    updates: dict[str, bool] = {}
    if enabled is not None:
        updates["router_enabled"] = enabled
    for model, allowed in (models or {}).items():
        updates[f"router_model_{model}"] = allowed
    if updates:
        store.set_meta_flags(updates)
    return load_settings(store)
