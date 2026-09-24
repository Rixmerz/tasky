"""Areas: the controlled vocabulary for where in a repository work happened.

An area is a business or technical part of one repository ("checkout",
"auth", "ci"), with aliases (synonyms, other languages, the history topics
that mean it) and the repository paths that belong to it. Everything here is
pure: which area a file, a history topic or a spec belongs to.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import PurePosixPath

KINDS = ("business", "technical")
NAME_CHARS = 40
MAX_ALIASES = 12
MAX_PATHS = 20
MAX_SPECS = 30
# Spec files named after their role, whose folder names the capability or feature.
_FOLDER_NAMED = frozenset({"spec.md", "proposal.md", "requirements.md", "bugfix.md", "design.md"})
_NESTED_WORKTREE = re.compile(r"^(?:\.claude/worktrees|\.worktrees)/[^/]+/")
_SLUG_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def slug(text: str) -> str:
    """`"Check Out_flow"` → `"check-out-flow"`; the form area names and aliases are compared in."""
    return "-".join(p for p in _SLUG_SPLIT.split(text.casefold().replace("_", " ")) if p)[
        :NAME_CHARS
    ].strip("-")


def clean_path(path: str) -> str:
    """A repo-relative path as stored: forward slashes, no leading ./ or /, no trailing /."""
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def relative(file_path: str, roots: Iterable[str]) -> str | None:
    """``file_path`` relative to the deepest root that contains it; None when none does."""
    if not file_path:
        return None
    if not file_path.startswith("/"):
        return clean_path(file_path)
    best: str | None = None
    for root in roots:
        root = root.rstrip("/")
        if root and (file_path == root or file_path.startswith(root + "/")) and (
            best is None or len(root) > len(best)
        ):
            best = root
    if best is None:
        return None
    # A worktree kept inside the checkout (Claude Code's .claude/worktrees/<name>) mirrors
    # the repo: its files belong where the same path is in the checkout.
    return _NESTED_WORKTREE.sub("", clean_path(file_path[len(best):]), count=1)


def _covers(area_path: str, rel: str) -> bool:
    return rel == area_path or rel.startswith(area_path + "/")


def area_of(rel: str, areas: list[dict]) -> list[int]:
    """Ids of the areas whose longest matching path covers ``rel`` (ties keep every area)."""
    best_len = -1
    best: list[int] = []
    for area in areas:
        for path in area.get("paths") or []:
            if path and _covers(path, rel):
                if len(path) > best_len:
                    best_len, best = len(path), [area["id"]]
                elif len(path) == best_len and area["id"] not in best:
                    best.append(area["id"])
    return best


def names_index(areas: list[dict]) -> dict[str, int]:
    """slug(name or alias) → area id; a name wins over another area's alias."""
    index: dict[str, int] = {}
    for area in areas:
        for alias in area.get("aliases") or []:
            index.setdefault(slug(alias), area["id"])
    for area in areas:
        index[slug(area["name"])] = area["id"]
    index.pop("", None)
    return index


def by_name(text: str | None, areas: list[dict]) -> int | None:
    if not text:
        return None
    return names_index(areas).get(slug(text))


def files_areas(rel_paths: Iterable[str], areas: list[dict], limit: int = 3) -> list[int]:
    """The areas most of these files belong to, most files first."""
    counts: Counter[int] = Counter()
    for rel in rel_paths:
        for area_id in area_of(rel, areas):
            counts[area_id] += 1
    return [area_id for area_id, _ in counts.most_common(limit)]


def spec_key(path: str) -> str:
    """The name a spec is known by: its capability, change or feature folder, or its file."""
    pure = PurePosixPath(path)
    if pure.name in _FOLDER_NAMED and len(pure.parts) >= 2:
        return slug(re.sub(r"^(\d{4}-\d{2}-\d{2}-|\d{3,4}-)", "", pure.parent.name))
    return slug(re.sub(r"^(adr[-_]?)?\d+[-_]", "", pure.stem, flags=re.IGNORECASE))


def spec_areas(spec: dict, areas: list[dict]) -> list[int]:
    """Areas that list the spec, else the area named like its capability or feature."""
    listed = [a["id"] for a in areas if spec["path"] in (a.get("specs") or [])]
    if listed:
        return listed
    named = names_index(areas).get(spec_key(spec["path"]))
    return [named] if named is not None else []
