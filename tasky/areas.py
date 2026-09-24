"""Areas: the controlled vocabulary for where in a repository work happened.

An area is a business or technical part of one repository ("checkout",
"auth", "ci"), with aliases (synonyms, other languages, the history topics
that mean it) and the repository paths that belong to it. Everything here is
pure: which area a file, a history topic or a spec belongs to.
"""

from __future__ import annotations

import re
import unicodedata
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


def key(text: str) -> str:
    """The form names, aliases and questions are matched in: a slug without accents."""
    folded = unicodedata.normalize("NFKD", slug(text))
    return "".join(c for c in folded if not unicodedata.combining(c))


def names_index(areas: list[dict]) -> dict[str, int]:
    """key(name or alias) → area id; a name wins over another area's alias."""
    index: dict[str, int] = {}
    for area in areas:
        for alias in area.get("aliases") or []:
            index.setdefault(key(alias), area["id"])
    for area in areas:
        index[key(area["name"])] = area["id"]
    index.pop("", None)
    return index


def by_name(text: str | None, areas: list[dict]) -> int | None:
    if not text:
        return None
    return names_index(areas).get(key(text))


def query_areas(query: str, areas: list[dict], max_words: int = 4) -> list[tuple[int, str]]:
    """Areas a question names, by name or alias, as (area id, the words that named it).

    Tries every run of up to ``max_words`` words, longest first, so "inicio de
    sesión" finds the area that lists it before "sesión" alone can; a word
    ending in s also tries its singular, and a word of 4+ letters may be the
    start of a one-word name or alias.
    """
    index = names_index(areas)
    words = [w for w in _SLUG_SPLIT.split(query.casefold()) if w]
    found: dict[int, str] = {}
    taken: set[int] = set()
    for size in range(min(max_words, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            span = set(range(start, start + size))
            if span & taken:
                continue
            phrase = " ".join(words[start : start + size])
            tries = [phrase]
            if size == 1 and len(phrase) > 3 and phrase.endswith("s"):
                tries.append(phrase[:-1])
            for text in tries:
                area_id = index.get(key(text))
                if area_id is not None:
                    found.setdefault(area_id, phrase)
                    taken |= span
                    break
    # A word of 4+ letters that starts exactly one area's one-word name or alias ("mongo").
    for pos, word in enumerate(words):
        if pos in taken or len(word) < 4:
            continue
        prefix = key(word)
        ids = {i for k, i in index.items() if "-" not in k and k.startswith(prefix)}
        if len(ids) == 1:
            found.setdefault(ids.pop(), word)
    return list(found.items())


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
    named = names_index(areas).get(key(spec_key(spec["path"])))
    return [named] if named is not None else []
