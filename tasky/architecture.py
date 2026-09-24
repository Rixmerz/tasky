"""Architecture of a repository: its areas, and the specs and decisions that describe it.

A scan reads the repository's files and costs no tokens. It keeps:

- specs: OpenSpec capabilities and changes, spec-kit features, Kiro specs and
  architecture decision records (Nygard, MADR and home-grown styles), each
  with a title, status, summary and its requirement names;
- candidate areas: Archify boundaries (``*.architecture.json``), Graphify
  communities (``graphify-out/graph.json``) and the folders that hold code.

When the repository has no areas yet and Archify or Graphify output exists,
the scan adopts those as areas. Otherwise areas come from one model call the
user asks for (``map_areas``): it gets the folder tree, the candidates, the
specs, the history topics and where recorded tasks edited files, and returns
the vocabulary: names, kind (business or technical), aliases, paths and
specs. Nothing it returns is trusted: paths must exist in the scan, specs
must be ones it was shown, and areas the user created or edited keep their
name and description.

Tasks, problems and milestones are linked to areas when read (``overview``):
a task by the files it edited, a problem or milestone by its topic (an area
name or alias) or else by its tasks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from tasky import areas as area_rules
from tasky import history, repos
from tasky.config import Config
from tasky.store import Store

MODELS = history.MODELS
MAX_FILES = 60_000
MAX_SPECS = 400
MAX_SPEC_BYTES = 200_000
MAX_JSON_BYTES = 30_000_000
MAX_CANDIDATES = 60
MAX_AREAS = 30
TREE_LINES = 300
PROMPT_SPECS_CHARS = 30_000
STALE_MAPPING_S = 20 * 60
_SUMMARY = 300
_ITEM = 140
_ITEMS = 40
_DESCRIPTION = 300
_GIT_TIMEOUT_S = 20

SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
    ".nuxt", "target", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage",
    ".idea", ".vscode", "vendor", ".cache", ".turbo", ".gradle", "out", ".angular",
    "graphify-out",
})
# Folders whose children are the interesting units ("src/auth", "packages/ui").
CONTAINERS = frozenset({
    "src", "packages", "apps", "services", "libs", "lib", "modules", "app", "internal", "pkg",
    "cmd", "components", "features", "domains", "projects", "plugins", "crates",
})
_NOT_AREAS = frozenset({"openspec", "specs", ".kiro", "docs", "doc", "tests", "test"})
_ADR_DIRS = frozenset({"adr", "adrs", "decisions", "architecture-decisions", "decision-records"})
_ADR_NAME = re.compile(r"^(adr[-_ ]?\d+|\d{3,4})[-_. ]", re.IGNORECASE)
_SPECKIT_DIR = re.compile(r"^\d{3}-")
_ARCHIVED = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_STATUS_LINE = re.compile(
    r"^\W*(?:status|estado)\W*[:\s]\W*([A-Za-zÁÉÍÓÚáéíóúñÑ][\wÁÉÍÓÚáéíóúñÑ -]{1,30})",
    re.IGNORECASE,
)
_DATE_LINE = re.compile(r"^\W*(?:date|fecha|created)\W*[:\s]", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FR = re.compile(r"\*\*(FR-\d+)\*\*:?\s*(.*)")

SYSTEM_PROMPT = """\
You define the areas of one software repository: the controlled vocabulary its developer \
uses to say where work happened, for tagging tasks, problems and decisions.

An area is a business part of the product ("checkout", "onboarding", "billing") or a \
technical part of the system ("auth", "ci", "database", "design-system"). Prefer the words \
the repository and its specs already use. Architectural layers (domain, infrastructure, \
controllers, utils) are not areas unless the code is organised only that way and nothing \
better exists.

Return 4 to 20 areas covering where the work shown happened. For each:
- name: short, lowercase, hyphenated, in the language the code uses for it.
- kind: "business" or "technical".
- description: one sentence, in the language the developer writes history topics in.
- aliases: other names people use for it: synonyms, the other language (English/Spanish), \
abbreviations, and every history topic listed in <topics> that means this area. At most 12.
- paths: repository-relative folders or files, copied from <tree>, that belong to it. A \
path belongs to one area only; use the most specific folder that fits.
- specs: paths of specs listed in <specs> that describe it.
- existing_id: when an area in <areas> is the same area, its id (you may rename it unless \
its source is "user"). Areas in <areas> you do not return are retired, except user ones.
Use Archify boundaries and Graphify communities in <candidates> as strong hints; folder \
candidates are only where code lives.
"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "areas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": list(area_rules.KINDS)},
                    "description": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "specs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "kind", "paths"],
            },
        }
    },
    "required": ["areas"],
}

Runner = Callable[..., subprocess.CompletedProcess]


class ArchitectureError(Exception):
    """A scan or mapping could not run."""


# -- files -------------------------------------------------------------------------


def scan_root(store: Store, repo: str) -> str | None:
    """Where to read the repo: its main checkout when known, else any existing root."""
    roots = [r for r in store.repo_roots(repo) if Path(r).is_dir()]
    for root in roots:
        if (Path(root) / ".git").is_dir():
            return root
    return roots[0] if roots else None


def list_files(root: str) -> list[str]:
    """Repo-relative files: git's view (tracked and untracked, not ignored), else a walk."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        names = proc.stdout.decode("utf-8", "replace").split("\0")
        files = [
            n for n in names
            if n and not any(part in SKIP_DIRS for part in PurePosixPath(n).parts[:-1])
        ]
        return sorted(set(files))[:MAX_FILES]
    files: list[str] = []
    for folder, subdirs, names in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        rel_folder = Path(folder).relative_to(root)
        files += [(rel_folder / n).as_posix() for n in names]
        if len(files) >= MAX_FILES:
            break
    return sorted(files)[:MAX_FILES]


def _dirs(files: list[str]) -> set[str]:
    dirs: set[str] = set()
    for f in files:
        parent = PurePosixPath(f).parent
        while str(parent) not in (".", ""):
            dirs.add(str(parent))
            parent = parent.parent
    return dirs


def _read(root: str, rel: str, limit: int = MAX_SPEC_BYTES) -> str:
    """A regular file inside the checkout; "" for anything a symlink points outside it."""
    try:
        base = Path(root).resolve()
        path = (Path(root) / rel).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            return ""
        with path.open("rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except (OSError, RuntimeError):
        return ""


# -- specs -------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sections(text: str) -> list[tuple[int, str, list[str]]]:
    """(level, heading, body lines) for every heading; level 0 is the text before any."""
    sections: list[tuple[int, str, list[str]]] = [(0, "", [])]
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        match = None if fence else _HEADING.match(line)
        if match:
            sections.append((len(match.group(1)), match.group(2).strip(), []))
        else:
            sections[-1][2].append(line)
    return sections


def _paragraph(lines: list[str]) -> str:
    """The first paragraph of prose (metadata lines like **Status**: and lists skipped)."""
    chunk: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if chunk:
                break
            continue
        if not chunk and (
            _STATUS_LINE.match(stripped) or _DATE_LINE.match(stripped)
            or stripped.startswith(("**", "<!--", "|", "---", ">"))
        ):
            continue
        chunk.append(stripped.lstrip("-* "))
    return _clip(" ".join(chunk), _SUMMARY)


def _section(sections: list, *names: str) -> str:
    wanted = tuple(n.casefold() for n in names)
    for _, heading, body in sections:
        if heading.casefold().rstrip(":").startswith(wanted):
            text = _paragraph(body)
            if text:
                return text
    return ""


def _title(sections: list, fallback: str) -> str:
    for level, heading, _ in sections:
        if level == 1 and heading:
            return _clip(heading, 160)
    return fallback


def _status(text: str) -> str | None:
    lines = text.splitlines()[:60]
    for i, line in enumerate(lines):
        match = _STATUS_LINE.match(line.strip().lstrip("*-# ").replace("**", ""))
        if match:
            return _clip(match.group(1).strip(" *."), 30)
        if _HEADING.match(line) and _HEADING.match(line).group(2).strip().casefold() in (
            "status", "estado"
        ):
            for follow in lines[i + 1:i + 4]:
                if follow.strip():
                    return _clip(follow.strip(" *-_."), 30)
    return None


def _dated(text: str) -> str | None:
    for line in text.splitlines()[:60]:
        if _DATE_LINE.match(line.strip().lstrip("*-# ").replace("**", "")):
            match = _DATE.search(line)
            if match:
                return match.group(1)
    return None


def _headings(sections: list, level: int, prefix: str = "") -> list[str]:
    items = []
    for lvl, heading, _ in sections:
        if lvl == level and heading.casefold().startswith(prefix.casefold()):
            items.append(_clip(heading[len(prefix):].strip(" :"), _ITEM))
    return items[:_ITEMS]


def _openspec_spec(rel: str, text: str) -> dict:
    sections = _sections(text)
    cap = PurePosixPath(rel).parent.name
    return {
        "kind": "openspec", "title": cap, "status": "current",
        "summary": _section(sections, "purpose", "propósito", "proposito"),
        "items": _headings(sections, 3, "Requirement:"),
    }


def _openspec_change(rel: str, text: str) -> dict:
    sections = _sections(text)
    folder = PurePosixPath(rel).parent.name
    archived = _ARCHIVED.match(folder) if "/archive/" in f"/{rel}" else None
    return {
        "kind": "openspec-change",
        "title": archived.group(2) if archived else folder,
        "status": "archived" if archived else "proposed",
        "dated": archived.group(1) if archived else None,
        "summary": _section(sections, "why", "por qué", "porque", "motivación", "summary")
        or _paragraph(sections[0][2]),
        "items": _bullets(sections, "what changes", "qué cambia", "cambios"),
    }


def _bullets(sections: list, *names: str) -> list[str]:
    wanted = tuple(n.casefold() for n in names)
    for _, heading, body in sections:
        if heading.casefold().startswith(wanted):
            return [
                _clip(line.strip()[2:], _ITEM)
                for line in body
                if line.strip().startswith(("- ", "* "))
            ][:_ITEMS]
    return []


def _speckit(rel: str, text: str) -> dict:
    sections = _sections(text)
    title = _title(sections, PurePosixPath(rel).parent.name)
    title = re.sub(r"^feature specification:\s*", "", title, flags=re.IGNORECASE)
    items = []
    for line in text.splitlines():
        match = _FR.search(line)
        if match:
            items.append(_clip(f"{match.group(1)}: {match.group(2)}", _ITEM))
    return {
        "kind": "spec-kit", "title": title, "status": _status(text), "dated": _dated(text),
        "summary": _section(sections, "summary", "overview", "user scenarios")
        or _paragraph(sections[0][2]),
        "items": items[:_ITEMS],
    }


def _kiro(root: str, rel: str, text: str) -> dict:
    sections = _sections(text)
    folder = PurePosixPath(rel).parent
    status = None
    tasks = _read(root, str(folder / "tasks.md"))
    if tasks:
        done = len(re.findall(r"^\s*- \[[xX]\]", tasks, re.MULTILINE))
        todo = len(re.findall(r"^\s*- \[ \]", tasks, re.MULTILINE))
        if done + todo:
            status = f"{done}/{done + todo} tasks"
    return {
        "kind": "kiro", "title": folder.name, "status": status,
        "summary": _section(sections, "introduction", "overview", "summary")
        or _paragraph(sections[0][2]),
        "items": _headings(sections, 3, "Requirement") or _headings(sections, 2, ""),
    }


def _adr(rel: str, text: str) -> dict:
    sections = _sections(text)
    title = _title(sections, PurePosixPath(rel).stem)
    return {
        "kind": "adr", "title": title, "status": _status(text), "dated": _dated(text),
        "summary": _section(sections, "decision", "decisión", "decision outcome")
        or _section(sections, "context", "contexto"),
        "items": [],
    }


def _is_adr(rel: str) -> bool:
    path = PurePosixPath(rel)
    if path.suffix.casefold() != ".md" or path.stem.casefold() in ("readme", "index", "template"):
        return False
    if "template" in path.stem.casefold():
        return False
    parents = {p.casefold() for p in path.parts[:-1]}
    if parents & _ADR_DIRS:
        return True
    return bool(_ADR_NAME.match(path.name)) and any("adr" in p or "decision" in p for p in parents)


def read_specs(root: str, files: list[str]) -> list[dict]:
    specs: list[dict] = []
    for rel in files:
        if len(specs) >= MAX_SPECS:
            break
        path = PurePosixPath(rel)
        parts = path.parts
        name = path.name
        parser = None
        if name == "spec.md" and len(parts) >= 4 and parts[-4:-2] == ("openspec", "specs"):
            parser = _openspec_spec
        elif name == "proposal.md" and "openspec" in parts and "changes" in parts:
            parser = _openspec_change
        elif (
            name == "spec.md" and len(parts) >= 3 and parts[-3] == "specs"
            and _SPECKIT_DIR.match(parts[-2]) and "openspec" not in parts
        ):
            parser = _speckit
        elif ".kiro" in parts and "specs" in parts and name in ("requirements.md", "bugfix.md"):
            parser = lambda r, t: _kiro(root, r, t)  # noqa: E731
        elif _is_adr(rel):
            parser = _adr
        if parser is None:
            continue
        text = _read(root, rel)
        if not text.strip():
            continue
        spec = parser(rel, text)
        spec.setdefault("dated", None)
        spec["path"] = rel
        specs.append(spec)
    return specs


# -- candidate areas ---------------------------------------------------------------


def _folder_candidates(files: list[str]) -> list[dict]:
    counts: Counter[str] = Counter()
    for f in files:
        parts = PurePosixPath(f).parts
        if len(parts) < 2 or parts[0] in _NOT_AREAS or (
            parts[0].startswith(".") and parts[0] != ".github"
        ):
            continue
        counts[parts[0]] += 1
        if parts[0] in CONTAINERS and len(parts) >= 3:
            counts[f"{parts[0]}/{parts[1]}"] += 1
    found = [
        {"name": PurePosixPath(d).name, "source": "folder", "paths": [d], "files": n}
        for d, n in counts.items()
        if n >= 3 and not (d in CONTAINERS and any(k.startswith(d + "/") for k in counts))
    ]
    found.sort(key=lambda c: -c["files"])
    return found[:MAX_CANDIDATES]


def _top_dirs(rels: list[str], limit: int = 4) -> list[str]:
    counts = Counter(str(PurePosixPath(r).parent) for r in rels)
    counts.pop(".", None)
    return [d for d, _ in counts.most_common(limit)]


def _archify(root: str, files: list[str], fileset: set[str], dirs: set[str]) -> list[dict]:
    found = []
    for rel in files:
        if not rel.endswith(".architecture.json"):
            continue
        try:
            data = json.loads(_read(root, rel, MAX_JSON_BYTES))
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("diagram_type") != "architecture":
            continue
        try:
            found += _archify_boundaries(data, rel, fileset, dirs)
        except (TypeError, AttributeError):  # a malformed file must not stop the scan
            continue
    return found


def _archify_boundaries(data: dict, rel: str, fileset: set[str], dirs: set[str]) -> list[dict]:
    found = []
    components = {
        c["id"]: c
        for c in data.get("components") or []
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }
    for boundary in data.get("boundaries") or []:
        if not isinstance(boundary, dict) or not boundary.get("label"):
            continue
        wrapped = [
            components[i] for i in boundary.get("wraps") or []
            if isinstance(i, str) and i in components
        ]
        paths: list[str] = []
        for component in wrapped:
            for source in component.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                path = area_rules.clean_path(str(source.get("path") or ""))
                if path in dirs:
                    paths.append(path)
                elif path in fileset:
                    paths.append(str(PurePosixPath(path).parent))
        found.append({
            "name": str(boundary["label"])[:80],
            "source": "archify",
            "paths": [p for p in dict.fromkeys(paths) if p != "."][: area_rules.MAX_PATHS],
            "detail": ", ".join(str(c.get("label") or c.get("id")) for c in wrapped)[:200],
            "file": rel,
        })
    return found


def _graphify_labels(folder: Path) -> dict[str, str]:
    try:
        data = json.loads((folder / ".graphify_labels.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    data = data.get("labels") if isinstance(data.get("labels"), dict) else data
    data = data.get("communities") if isinstance(data.get("communities"), dict) else data
    labels = {}
    for key, value in data.items():
        if isinstance(value, dict):
            value = value.get("label") or value.get("name") or value.get("title")
        if isinstance(value, str) and value.strip():
            labels[str(key)] = value.strip()
    return labels


def _graphify(root: str, files: list[str], fileset: set[str]) -> list[dict]:
    # graphify-out is usually git-ignored: look for it at the root and one or two levels down.
    graphs = {
        p.relative_to(root).as_posix()
        for pattern in ("graphify-out/graph.json", "*/graphify-out/graph.json",
                        "*/*/graphify-out/graph.json")
        for p in Path(root).glob(pattern)
    }
    found = []
    for rel in sorted(graphs):
        folder = Path(root) / rel
        base = PurePosixPath(rel).parent.parent
        try:
            data = json.loads(_read(root, rel, MAX_JSON_BYTES))
        except ValueError:
            continue
        nodes = data.get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes, list):
            continue
        labels = _graphify_labels(folder.parent)
        members: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        for node in nodes:
            if not isinstance(node, dict) or node.get("community") is None:
                continue
            source = str(node.get("source_file") or "")
            if not source:
                continue
            if source.startswith("/"):
                source = area_rules.relative(source, [root]) or ""
            source = area_rules.clean_path(source)
            for option in (source, str(base / source)):
                if option in fileset:
                    key = str(node["community"])
                    members.setdefault(key, []).append(option)
                    if node.get("community_name"):
                        names[key] = str(node["community_name"])
                    break
        for key, rels in members.items():
            if len(rels) < 3:
                continue
            paths = _top_dirs(rels)
            name = labels.get(key) or names.get(key) or (
                PurePosixPath(paths[0]).name if paths else f"community {key}"
            )
            found.append({
                "name": name[:80], "source": "graphify", "paths": paths, "files": len(rels),
                "file": rel,
            })
    found.sort(key=lambda c: -c.get("files", 0))
    return found[:MAX_CANDIDATES]


# -- scan --------------------------------------------------------------------------


def _adopt(store: Store, repo: str, candidates: list[dict]) -> int:
    """Seed areas from Archify boundaries, else Graphify communities, when the repo has none."""
    if store.areas(repo, include_hidden=True):
        return 0
    for source in ("archify", "graphify"):
        chosen = [c for c in candidates if c["source"] == source]
        if chosen:
            break
    else:
        return 0
    seen: set[str] = set()
    for candidate in chosen[:MAX_AREAS]:
        name = area_rules.slug(candidate["name"])
        if not name or name in seen:
            continue
        seen.add(name)
        store.save_area(
            repo, name, kind="technical", source=candidate["source"],
            description=candidate.get("detail") or "",
            aliases=[candidate["name"]] if candidate["name"] != name else [],
            paths=candidate["paths"],
        )
    return len(seen)


def scan(config: Config, repo: str) -> dict:
    """Read the repo's specs and candidate areas; returns counts. Costs no tokens."""
    with Store.open(config) as store:
        repos.ensure(store, store.repo_cwds(repo), max_age_s=0)
        root = scan_root(store, repo)
    if root is None:
        raise ArchitectureError(f"no folder of {repo} exists on this machine")
    files = list_files(root)
    fileset = set(files)
    dirs = _dirs(files)
    specs = read_specs(root, files)
    candidates = [
        *_archify(root, files, fileset, dirs),
        *_graphify(root, files, fileset),
        *_folder_candidates(files),
    ]
    sources = Counter(s["kind"] for s in specs)
    sources.update(c["source"] for c in candidates)
    with Store.open(config) as store:
        store.replace_specs(repo, specs)
        store.save_scan(
            repo, root=root, files=len(files), sources=dict(sources), candidates=candidates
        )
        adopted = _adopt(store, repo, candidates)
    return {"root": root, "files": len(files), "specs": len(specs),
            "candidates": len(candidates), "adopted": adopted, "sources": dict(sources)}


# -- mapping areas with a model ----------------------------------------------------


def tree(files: list[str], max_lines: int = TREE_LINES) -> str:
    """Folders (with file counts) down to the depth that fits ``max_lines``."""
    counts: Counter[str] = Counter()
    for f in files:
        parent = PurePosixPath(f).parent
        while str(parent) not in (".", ""):
            counts[str(parent)] += 1
            parent = parent.parent
    lines: list[str] = []
    for depth in range(1, 6):
        level = sorted(d for d in counts if len(PurePosixPath(d).parts) <= depth)
        if len(level) > max_lines and lines:
            break
        lines = [f"{d}/ ({counts[d]})" for d in level[:max_lines]]
    top_files = [f for f in files if "/" not in f][:40]
    return "\n".join([*lines, *top_files])


def _edited_dirs(store: Store, repo: str, limit: int = 40) -> list[str]:
    roots = store.repo_roots(repo)
    counts: Counter[str] = Counter()
    for edit in store.edits_under(roots):
        rel = area_rules.relative(edit["file_path"], roots)
        if rel:
            counts[str(PurePosixPath(rel).parent)] += 1
    return [f"{d} ({n} edits)" for d, n in counts.most_common(limit)]


def _topics(store: Store, repo: str) -> list[str]:
    counts: Counter[str] = Counter()
    for item in (*store.problems(repo), *store.milestones(repo)):
        if item.get("topic"):
            counts[item["topic"]] += 1
    return [f"{t} ({n})" for t, n in counts.most_common(80)]


def build_prompt(
    repo: str,
    files: list[str],
    candidates: list[dict],
    specs: list[dict],
    known: list[dict],
    topics: list[str],
    edited: list[str],
) -> str:
    spec_lines = []
    used = 0
    for spec in specs:
        line = f"{spec['path']} [{spec['kind']}] {spec['title']}"
        if spec.get("summary"):
            line += f" — {_clip(spec['summary'], 160)}"
        if spec.get("items"):
            line += " | " + "; ".join(spec["items"][:6])
        used += len(line)
        if used > PROMPT_SPECS_CHARS:
            spec_lines.append(f"… {len(specs) - len(spec_lines)} more specs not shown")
            break
        spec_lines.append(line)
    known_json = [
        {k: a[k] for k in ("id", "name", "kind", "description", "aliases", "paths", "source")}
        for a in known
    ]
    candidate_lines = [
        f"[{c['source']}] {c['name']}: {', '.join(c['paths']) or '-'}"
        + (f" ({c['detail']})" if c.get("detail") else "")
        for c in candidates
    ]
    parts = [
        f"<repository>{repo}</repository>",
        "<tree>", tree(files), "</tree>",
        "<candidates>", *candidate_lines, "</candidates>",
        "<specs>", *spec_lines, "</specs>",
        "<areas>", json.dumps(known_json, ensure_ascii=False), "</areas>",
        "<topics>", *topics, "</topics>",
        "<edited>", *edited, "</edited>",
    ]
    return "\n".join(parts)


def _strings(value: Any, limit: int, size: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip()[:size] for v in value if isinstance(v, str) and v.strip()][:limit]


def apply_mapping(
    store: Store, repo: str, output: dict, *, files: list[str], spec_paths: set[str]
) -> dict:
    """Store the areas the model returned, checked against the scan; returns counts."""
    fileset = set(files)
    valid_paths = fileset | _dirs(files)
    current = {a["id"]: a for a in store.areas(repo)}
    deleted_by_user = {
        a["name"] for a in store.areas(repo, include_hidden=True)
        if a["deleted_at"] and a["source"] == "user"
    }
    by_name = {a["name"]: a for a in current.values()}
    kept: set[int] = set()
    seen: set[str] = set()
    claimed: set[str] = set()
    added = updated = 0
    for item in (output.get("areas") or [])[:MAX_AREAS]:
        if not isinstance(item, dict):
            continue
        name = area_rules.slug(str(item.get("name") or ""))
        existing_id = item.get("existing_id")
        target = current.get(existing_id) if isinstance(existing_id, int) else None
        target = target or by_name.get(name)
        if target is not None and target["id"] in kept:
            target = None
        if name in deleted_by_user:
            if target is None:
                continue
            name = target["name"]  # keep the area, not the name the user deleted
        if not name or name in seen:
            continue
        seen.add(name)
        paths = []
        for path in _strings(item.get("paths"), area_rules.MAX_PATHS * 2, 300):
            path = area_rules.clean_path(path)
            if path in valid_paths and path not in claimed:
                paths.append(path)
                claimed.add(path)
        specs = [
            s for s in _strings(item.get("specs"), area_rules.MAX_SPECS, 300) if s in spec_paths
        ]
        aliases = [
            a for a in _strings(item.get("aliases"), area_rules.MAX_ALIASES, area_rules.NAME_CHARS)
            if area_rules.slug(a) != name
        ]
        kind = item.get("kind") if item.get("kind") in area_rules.KINDS else "technical"
        description = str(item.get("description") or "").strip()[:_DESCRIPTION]
        if target is not None and target["source"] == "user":
            store.save_area(
                repo, target["name"],
                paths=[*target["paths"], *paths], specs=[*target["specs"], *specs],
                aliases=[*target["aliases"], *aliases],
            )
            kept.add(target["id"])
            updated += 1
            continue
        if target is not None and target["name"] != name:
            renamed = store.rename_area(target["id"], name)
            if renamed is None:  # another area has the name: keep this one as it is named
                name = target["name"]
                seen.add(name)
        area = store.save_area(
            repo, name, kind=kind, description=description, aliases=aliases, paths=paths,
            specs=specs, source="model",
        )
        kept.add(area["id"])
        if target is not None:
            updated += 1
        else:
            added += 1
    retired = 0
    for area in current.values():
        if area["id"] not in kept and area["source"] != "user" and store.hide_area(area["id"]):
            retired += 1
    return {"added": added, "updated": updated, "retired": retired}


def map_areas(
    config: Config, repo: str, *, model: str | None = None, run: Runner = subprocess.run
) -> dict:
    """Scan the repo, then ask the model for its areas; returns counts and cost."""
    model = model or config.history_model
    if model not in MODELS:
        raise ArchitectureError(f"unknown model {model!r}; use one of {', '.join(MODELS)}")
    with Store.open(config) as store:
        if not store.repo_cwds(repo):
            raise ArchitectureError(f"no recorded folders belong to {repo}")
        if not store.begin_area_mapping(repo, model, stale_after_s=STALE_MAPPING_S):
            raise ArchitectureError("areas of this repository are already being mapped")
    cost = 0.0
    error: str | None = None
    summary: dict[str, Any] = {"model": model}
    try:
        summary.update(scan(config, repo))
        files = list_files(summary["root"])
        with Store.open(config) as store:
            specs = store.specs(repo)
            scan_row = store.architecture(repo) or {}
            prompt = build_prompt(
                repo, files, scan_row.get("candidates") or [], specs,
                store.areas(repo), _topics(store, repo), _edited_dirs(store, repo),
            )
        try:
            output, cost = history.call_model(
                config, model, prompt, run=run, system_prompt=SYSTEM_PROMPT, schema=OUTPUT_SCHEMA
            )
        except history.SyncError as exc:
            raise ArchitectureError(str(exc)) from exc
        with Store.open(config) as store:
            summary.update(
                apply_mapping(store, repo, output, files=files,
                              spec_paths={s["path"] for s in specs})
            )
    except ArchitectureError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never left 'running'
        error = f"{type(exc).__name__}: {exc}"
    finally:
        with Store.open(config) as store:
            store.finish_area_mapping(repo, cost_usd=cost, error=error)
    summary["cost_usd"] = cost
    summary["error"] = error
    return summary


# -- reading it back ---------------------------------------------------------------


def _is_spec_path(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and (parts[0] in ("openspec", ".kiro") or _is_adr(rel))


def _task_brief(task: dict) -> dict:
    return {
        "id": task["id"], "title": task["title"], "status": task["status"],
        "date": (task.get("finished_at") or task.get("created_at") or "")[:10],
    }


def overview(store: Store, repo: str, *, recent: int = 5) -> dict:
    """Areas of a repo with their specs, tasks, problems and milestones linked."""
    area_list = store.areas(repo)
    specs = store.specs(repo)
    tasks = store.repo_tasks(repo)
    task_areas = store.task_areas(tasks)
    by_task = {t["id"]: t for t in tasks}
    index = {a["id"]: {**a, "spec_items": [], "tasks": 0, "recent_tasks": [],
                       "last_task": None, "problems": [], "milestones": [], "turns": 0,
                       "files": 0, "last_edit": None}
             for a in area_list}

    # Activity from every recorded edit under the repo, with or without a task row:
    # turns that edited the area, distinct files, and the last edit's date.
    roots = store.repo_roots(repo)
    turns: dict[int, set] = {}
    touched: dict[int, set] = {}
    unplaced_files: set[str] = set()
    for edit in store.edits_under(roots):
        rel = area_rules.relative(edit["file_path"], roots)
        if rel is None:
            continue
        ids = area_rules.area_of(rel, area_list)
        if not ids and not _is_spec_path(rel):
            unplaced_files.add(rel)
        for i in ids:
            turns.setdefault(i, set()).add(edit["prompt_id"])
            touched.setdefault(i, set()).add(rel)
            day = (edit["ts"] or "")[:10] or None
            if day and (index[i]["last_edit"] or "") < day:
                index[i]["last_edit"] = day
    for i, prompts in turns.items():
        index[i]["turns"] = len(prompts)
        index[i]["files"] = len(touched[i])

    for task in tasks:  # newest first
        for ref in task_areas.get(task["id"], []):
            area = index[ref["id"]]
            area["tasks"] += 1
            if len(area["recent_tasks"]) < recent:
                area["recent_tasks"].append(_task_brief(task))
            area["last_task"] = area["last_task"] or _task_brief(task)["date"]

    def linked(item: dict) -> list[int]:
        named = area_rules.by_name(item.get("topic"), area_list)
        if named is not None:
            return [named]
        counts: Counter[int] = Counter(
            ref["id"] for tid in item["task_ids"] if tid in by_task
            for ref in task_areas.get(tid, [])
        )
        return [i for i, _ in counts.most_common(1)]

    unplaced_topics: Counter[str] = Counter()
    for problem in store.problems(repo):
        ids = linked(problem)
        for i in ids:
            index[i]["problems"].append({
                "id": problem["id"], "title": problem["title"], "state": problem["state"],
                "failed": sum(1 for a in problem["attempts"] if a["outcome"] == "failed"),
                "last_seen": problem["last_seen"],
            })
        if not ids and problem.get("topic"):
            unplaced_topics[problem["topic"]] += 1
    for milestone in store.milestones(repo):
        ids = linked(milestone)
        for i in ids:
            index[i]["milestones"].append({
                "id": milestone["id"], "title": milestone["title"],
                "happened_on": milestone["happened_on"],
            })
        if not ids and milestone.get("topic"):
            unplaced_topics[milestone["topic"]] += 1

    unlinked_specs = []
    for spec in specs:
        ids = area_rules.spec_areas(spec, area_list)
        spec["areas"] = [index[i]["name"] for i in ids]
        for i in ids:
            index[i]["spec_items"].append(spec["path"])
        if not ids:
            unlinked_specs.append(spec["path"])

    edited = store.edited_files_by_prompt(t["prompt_id"] for t in tasks if t["prompt_id"])
    edited_without_area = sum(
        1 for t in tasks if t["id"] not in task_areas and edited.get(t["prompt_id"])
    )
    for area in index.values():
        area["problems"].sort(key=lambda p: (p["state"] == "solved", p["last_seen"] or ""))
        area["milestones"].sort(key=lambda m: m["happened_on"] or "", reverse=True)
    scan_row = store.architecture(repo)
    return {
        "repo": repo,
        "scan": scan_row,
        "areas": sorted(index.values(), key=lambda a: (-a["turns"], -a["tasks"], a["name"])),
        "unplaced_dirs": [
            d for d, _ in Counter(str(PurePosixPath(f).parent) for f in unplaced_files)
            .most_common(15)
        ],
        "specs": specs,
        "unlinked_specs": unlinked_specs,
        "unplaced_topics": [t for t, _ in unplaced_topics.most_common()],
        "edited_without_area": edited_without_area,
        "tasks_total": len(tasks),
    }
