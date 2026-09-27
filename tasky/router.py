"""Model router: start each launched task on the model and effort its kind of work calls for.

Runs inside the detached supervisor (`tasky.supervise`), never in the HTTP handler or the
scheduler tick, because classifying a task shells out to muscle-memory's `mm-classifier` and can
take seconds. The classifier chain lives in `tasky/router/*.json` as ordinary `mm-classifier`
definitions: keyword rules first, and a final catch-all rule so a task no rule recognises is
`default` without ever contacting a Laya server.

Every path that cannot decide -- router off, no classifier, a failing or slow classifier, a kind
whose models are all switched off -- adds no flags, so the `claude` command stays exactly what it
would be without a router.
"""

from __future__ import annotations

import glob
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


def decide(
    task: Mapping[str, object],
    enabled: bool,
    allowed: Mapping[str, bool],
    *,
    env: Mapping[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> Decision:
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
    effort = None if choice.model == "haiku" else (choice.effort or "high")
    return Decision(
        kind=kind,
        model=choice.model,
        effort=effort,
        reason=f"{kind} ({how}) -> {choice.model}" + (f" {effort}" if effort else ""),
        args=flags(choice),
    )


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
