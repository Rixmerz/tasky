"""Which repository a working folder belongs to.

History is kept per repository, not per folder: worktrees and clones of one
repo share it. A folder's repo is its `origin` remote (normalised to
host/owner/name), else the main checkout of its git repo, else the folder
itself. Answers are cached in the ``repos`` table for an hour, so hooks pay
for git at most once per folder per hour.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path

from tasky.config import now_iso

MAX_AGE_S = 3600
_GIT_TIMEOUT_S = 5
_SCP_LIKE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


def normalize_remote(url: str) -> str:
    """`git@github.com:Foo/bar.git` and `https://u:p@github.com/Foo/bar` → `github.com/foo/bar`."""
    url = url.strip()
    if "://" in url:
        rest = url.split("://", 1)[1]
        rest = rest.split("@", 1)[1] if "@" in rest.split("/", 1)[0] else rest
        host, _, path = rest.partition("/")
    else:
        match = _SCP_LIKE.match(url)
        if not match:
            return url.lower()
        host, path = match.group(1), match.group(2)
    host = host.split(":", 1)[0]
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}".lower()


def _git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def resolve(cwd: str) -> tuple[str, str]:
    """(repo key, display name) for a folder; never raises."""
    if Path(cwd).is_dir():
        remote = _git(cwd, "config", "--get", "remote.origin.url")
        if remote:
            key = normalize_remote(remote)
            return key, key.rsplit("/", 1)[-1] or key
        common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common:
            root = Path(common).parent if Path(common).name == ".git" else Path(common)
            return f"path:{root}", root.name or str(root)
    return f"path:{cwd}", Path(cwd).name or cwd


def ensure(store, cwds: Iterable[str], *, max_age_s: float = MAX_AGE_S) -> None:
    """Resolve and cache every folder whose answer is missing or older than max_age_s."""
    from tasky.store import _parse_iso

    for cwd in {c for c in cwds if c}:
        row = store.repo_row(cwd)
        if row is not None:
            checked = _parse_iso(row["checked_at"])
            if checked is not None and time.time() - checked < max_age_s:
                continue
        key, name = resolve(cwd)
        store.set_repo(cwd, key, name, now_iso())


def repo_of(store, cwd: str) -> str:
    ensure(store, [cwd])
    row = store.repo_row(cwd)
    return row["repo"] if row else f"path:{cwd}"
