"""Quick diagram: a repository's areas and the imports between them, drawn by Archify's CLI.

No model is called. The components are the repository's areas, each with a tracked file as
evidence; a relationship is a set of imports from one area's files into another's (JavaScript,
TypeScript, Python and Go), placed in layers so what imports sits above what it uses. Archify
validates the result against the pinned commit and renders it; its diagnostics steer a few
fixed repairs (move a box one column, let a line route itself, drop a line) until it passes.
"""

from __future__ import annotations

import json
import posixpath
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tasky import architecture
from tasky import areas as area_rules
from tasky.config import Config
from tasky.store import Store

MIN_IMPORTS = 3
MAX_CONNECTIONS = 18
MAX_COLS = 12
MAX_REPAIRS = 14
FILE_CHARS = 200_000
SUBLABEL_CHARS = 38
SOURCES_PER_AREA = 2
CALL_TIMEOUT_S = 120
_CODE = re.compile(r"\.(?:[cm]?[jt]sx?|py|go)$")
_JS_IMPORT = re.compile(
    r"""(?:\bimport\s[^'"`;]*?\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+|"""
    r"""\bexport\s[^'"`;]*?\bfrom\s*)['"]([^'"]+)['"]""",
    re.M,
)
_PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w, ]+)|import\s+([\w.]+))", re.M)
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([\w./-]+)"', re.M)
_GO_MODULE = re.compile(r"^module\s+(\S+)", re.M)
_JS_EXTS = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
            "/index.ts", "/index.tsx", "/index.js", "/index.mjs")
_TYPES = (
    ("database", ("database", "db", "mongo", "sql", "postgres", "mysql", "redis", "storage",
                  "persistence", "persistencia", "repository", "migrations")),
    ("frontend", ("ui", "web", "frontend", "front", "dashboard", "client", "app-shell", "pages")),
    ("security", ("auth", "security", "access", "permissions", "iam", "login", "seguridad")),
    ("messagebus", ("queue", "bus", "events", "kafka", "rabbit", "pubsub", "messaging", "sqs")),
    ("cloud", ("infra", "deploy", "ci", "cloud", "k8s", "kubernetes", "terraform", "docker")),
)


class QuickDiagramError(Exception):
    pass


def _git(root: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                              timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _component_type(area: dict) -> str:
    words = {area_rules.key(w) for w in [area["name"], *area["aliases"]]}
    words |= {part for w in words for part in w.split("-")}
    for kind, hints in _TYPES:
        if words & set(hints):
            return kind
    return "backend"


def _component_id(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", area_rules.key(name)).strip("-") or "area"
    if not base[0].isalpha():
        base = f"a-{base}"
    ident, n = base, 2
    while ident in taken:
        ident, n = f"{base}-{n}", n + 1
    taken.add(ident)
    return ident


def _sublabel(text: str) -> str:
    text = " ".join((text or "").split()).rstrip(".")
    if len(text) <= SUBLABEL_CHARS:
        return text
    cut = text[: SUBLABEL_CHARS - 1]
    if text[SUBLABEL_CHARS - 1] != " ":  # the cut splits a word: drop its start
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def _sources(area: dict, files: list[str]) -> list[dict]:
    """Up to SOURCES_PER_AREA tracked files, code first, one per area path."""
    picked: list[dict] = []
    for path in area["paths"]:
        under = [f for f in files if f == path or f.startswith(path + "/")]
        if not under:
            continue
        code = [f for f in under if _CODE.search(f)]
        picked.append({"path": (code or under)[0], "label": path})
        if len(picked) >= SOURCES_PER_AREA:
            break
    return picked


def _python_modules(files: list[str]) -> dict[str, str]:
    """Dotted module name → file, from the repository root and from a src/ folder."""
    modules: dict[str, str] = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        parts = f[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in (0, 1) if parts and parts[0] in ("src", "lib", "app") else (0,):
            if parts[start:]:
                modules.setdefault(".".join(parts[start:]), f)
    return modules


def _targets(rel: str, text: str, tracked: set[str], py: dict[str, str],
             go_module: str | None, go_dirs: dict[str, str]) -> list[str]:
    found: list[str] = []
    if rel.endswith(".py"):
        package = rel.rsplit("/", 1)[0].split("/") if "/" in rel else []
        for m in _PY_IMPORT.finditer(text):
            if m.group(3):
                if m.group(3) in py:
                    found.append(py[m.group(3)])
                continue
            base = m.group(1)
            dots = len(base) - len(base.lstrip("."))
            if dots:
                anchor = package[: len(package) - dots + 1] if dots <= len(package) + 1 else []
                base = ".".join([*anchor, base.lstrip(".")]).strip(".")
            # One import per name: the submodule when it is one, else the module it comes from.
            for name in (n.strip() for n in m.group(2).split(",") if n.strip()):
                target = py.get(f"{base}.{name}") or py.get(base)
                if target:
                    found.append(target)
    elif rel.endswith(".go"):
        for m in _GO_IMPORT.finditer(text):
            spec = m.group(1)
            if go_module and spec.startswith(go_module + "/"):
                target = go_dirs.get(spec[len(go_module) + 1:])
                if target:
                    found.append(target)
    else:
        for m in _JS_IMPORT.finditer(text):
            spec = m.group(1)
            if not spec.startswith("."):
                continue
            base = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
            for ext in _JS_EXTS:
                if base + ext in tracked:
                    found.append(base + ext)
                    break
    return found


def import_edges(root: str, files: list[str], areas: list[dict]) -> Counter[tuple[int, int]]:
    """(importing area id, imported area id) → how many imports cross that way."""
    tracked = set(files)
    py = _python_modules(files)
    go_module = None
    if "go.mod" in tracked:
        match = _GO_MODULE.search(_read(root, "go.mod"))
        go_module = match.group(1) if match else None
    go_dirs: dict[str, str] = {}
    for f in files:
        if f.endswith(".go"):
            go_dirs.setdefault(posixpath.dirname(f), f)
    edges: Counter[tuple[int, int]] = Counter()
    owner: dict[str, list[int]] = {}

    def area(rel: str) -> list[int]:
        if rel not in owner:
            owner[rel] = area_rules.area_of(rel, areas)
        return owner[rel]

    for rel in files:
        if not _CODE.search(rel) or not area(rel):
            continue
        text = _read(root, rel)
        if not text:
            continue
        for target in _targets(rel, text, tracked, py, go_module, go_dirs):
            for a in area(rel):
                for b in area(target):
                    if a != b:
                        edges[(a, b)] += 1
    return edges


def _read(root: str, rel: str) -> str:
    path = Path(root, rel)
    try:
        if path.is_symlink() or path.stat().st_size > FILE_CHARS:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def pick_edges(edges: Counter[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """The strongest direction of each pair with MIN_IMPORTS or more, at most MAX_CONNECTIONS."""
    kept: dict[frozenset, tuple[int, int, int]] = {}
    for (a, b), n in edges.items():
        if n < MIN_IMPORTS:
            continue
        pair = frozenset((a, b))
        if pair not in kept or n > kept[pair][2]:
            kept[pair] = (a, b, n)
    return sorted(kept.values(), key=lambda e: (-e[2], e[0], e[1]))[:MAX_CONNECTIONS]


Edge = tuple[int, int, int]


def layout(ids: list[int], edges: list[Edge]) -> tuple[dict[int, tuple[int, int]], int]:
    """(area id → (row, col), columns): importers above what they import, lines in corridors.

    A node's layer is the longest chain of imports below it; rows are ordered by the average
    column of their neighbours above, and sparse rows use every other column so lines that
    skip a row have a gap to run through. Areas without relationships go in the last rows.
    """
    below: dict[int, set[int]] = defaultdict(set)
    for a, b, _ in edges:
        below[a].add(b)
    depth: dict[int, int] = {}

    def walk(node: int, path: frozenset) -> int:
        if node in depth:
            return depth[node]
        d = max((walk(n, path | {node}) + 1 for n in below[node] if n not in path), default=0)
        depth[node] = d
        return d

    linked = {a for a, _, _ in edges} | {b for _, b, _ in edges}
    for node in ids:
        if node in linked:
            walk(node, frozenset())
    top = max(depth.values(), default=0)
    rows: dict[int, list[int]] = defaultdict(list)
    for node in ids:
        if node in linked:
            rows[top - depth[node]].append(node)
    widest = max((len(r) for r in rows.values()), default=0)
    cols = max(4, min(MAX_COLS, max((2 * len(r) - 1 for r in rows.values()), default=0)))
    cols = max(cols, min(MAX_COLS, widest))
    neighbours: dict[int, set[int]] = defaultdict(set)
    for a, b, _ in edges:
        neighbours[a].add(b)
        neighbours[b].add(a)
    place: dict[int, tuple[int, int]] = {}
    row_index = 0
    for layer in sorted(rows):
        nodes = rows[layer]
        nodes.sort(key=lambda n: (
            sum(place[m][1] for m in neighbours[n] if m in place)
            / max(1, sum(1 for m in neighbours[n] if m in place)),
            n,
        ))
        for chunk in (nodes[i : i + cols] for i in range(0, len(nodes), cols)):
            step = 2 if 2 * len(chunk) - 1 <= cols else 1
            start = (cols - (step * (len(chunk) - 1) + 1)) // 2
            for i, node in enumerate(chunk):
                place[node] = (row_index, start + step * i)
            row_index += 1
    loose = sorted(n for n in ids if n not in linked)
    for i in range(0, len(loose), cols):
        for j, node in enumerate(loose[i : i + cols]):
            place[node] = (row_index, j)
        row_index += 1
    return place, cols


def build(areas: list[dict], files: list[str], edges: list[tuple[int, int, int]],
          title: str, repository: dict | None) -> dict:
    """The Archify architecture source for these areas and relationships."""
    place, cols = layout([a["id"] for a in areas], edges)
    taken: set[str] = set()
    ident = {a["id"]: _component_id(a["name"], taken) for a in areas}
    components = []
    for area in areas:
        row, col = place[area["id"]]
        component: dict[str, Any] = {
            "id": ident[area["id"]], "type": _component_type(area), "label": area["name"],
            "row": row, "col": col, "size": [190, 64],
        }
        if area["description"]:
            component["sublabel"] = _sublabel(area["description"])
        sources = _sources(area, files) if repository else []
        if sources:
            component["sources"] = sources
        components.append(component)
    connections = []
    for a, b, n in edges:
        connection: dict[str, Any] = {"from": ident[a], "to": ident[b]}
        (ra, ca), (rb, cb) = place[a], place[b]
        if ra != rb:
            connection["fromSide"], connection["toSide"] = (
                ("bottom", "top") if rb > ra else ("top", "bottom")
            )
        else:
            connection["fromSide"], connection["toSide"] = (
                ("right", "left") if cb > ca else ("left", "right")
            )
        connection["imports"] = n  # Tasky's own note; removed before Archify reads it
        connections.append(connection)
    meta: dict[str, Any] = {"title": title}
    if repository:
        meta["repository"] = repository
    return {
        "schema_version": 1, "diagram_type": "architecture", "meta": meta,
        "layout": {"mode": "grid", "cols": cols, "cellW": 200, "cellH": 90, "gapX": 24,
                   "gapY": 80},
        "components": components, "connections": connections,
    }


def _archify(skill: str, root: str, *args: str) -> dict:
    try:
        proc = subprocess.run(["node", f"{skill}/bin/archify.mjs", *args], cwd=root,
                              capture_output=True, text=True, timeout=CALL_TIMEOUT_S,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QuickDiagramError(f"could not run Archify's CLI: {exc}") from exc
    try:
        reply = json.loads(proc.stdout)
    except ValueError as exc:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        raise QuickDiagramError(f"Archify's CLI failed: {detail}") from exc
    return reply if isinstance(reply, dict) else {}


def _free_col(source: dict, component: dict) -> int | None:
    """A column next to ``component`` with no other box in its row, nearest first."""
    row, col = component["row"], component["col"]
    used = {c["col"] for c in source["components"] if c["row"] == row}
    for delta in (1, -1, 2, -2):
        target = col + delta
        if 0 <= target < source["layout"]["cols"] and target not in used:
            return target
    return None


def repair(source: dict, diagnostics: list[dict]) -> bool:
    """Apply one fixed repair per diagnosed subject; False when nothing could be changed."""
    changed = False
    drop: set[int] = set()
    moved: set[str] = set()
    by_id = {c["id"]: c for c in source["components"]}
    for diag in diagnostics:
        subject = diag.get("subject") or {}
        evidence = diag.get("evidence") or {}
        code = diag.get("code") or ""
        index = subject.get("index")
        conn = (source["connections"][index]
                if subject.get("collection") == "connections" and isinstance(index, int)
                and 0 <= index < len(source["connections"]) else None)
        if code == "clean-flow/edge-through-node" and conn is not None:
            obstacle = by_id.get(evidence.get("obstacleId"))
            if obstacle is not None and obstacle["id"] not in moved:
                col = _free_col(source, obstacle)
                if col is not None:
                    obstacle["col"] = col
                    moved.add(obstacle["id"])
                    changed = True
                    continue
            drop.add(index)
        elif conn is not None:
            if "fromSide" in conn and code.endswith("side-direction"):
                conn.pop("fromSide", None)
                conn.pop("toSide", None)
                changed = True
            else:
                drop.add(index)
        elif subject.get("identity") in by_id and "sublabel" in by_id[subject["identity"]]:
            del by_id[subject["identity"]]["sublabel"]
            changed = True
    if drop:
        source["connections"] = [c for i, c in enumerate(source["connections"]) if i not in drop]
        changed = True
    return changed


def _for_archify(source: dict) -> dict:
    clean = json.loads(json.dumps(source))
    for conn in clean["connections"]:
        conn.pop("imports", None)
    if not clean["connections"]:
        del clean["connections"]
    return clean


def draw(config: Config, repo: str) -> dict:
    """Write and deliver the quick diagram of ``repo``; returns what was drawn."""
    with Store.open(config) as store:
        root = architecture.scan_root(store, repo)
        areas = [a for a in store.areas(repo) if a["paths"]]
    if root is None:
        raise QuickDiagramError("no checkout of this repository is on disk")
    if not areas:
        raise QuickDiagramError("this repository has no areas with folders; map the areas first")
    skill = architecture.archify_skill(config, root)
    if skill is None:
        raise QuickDiagramError("the Archify skill is not installed for Claude Code")
    revision = _git(root, "rev-parse", "HEAD")
    if not revision:
        raise QuickDiagramError("the checkout has no commit to pin the evidence to")
    url = _git(root, "remote", "get-url", "origin")
    listing = _git(root, "ls-tree", "-r", "--name-only", revision) or ""
    files = sorted(listing.splitlines())
    repository = {"url": url, "revision": revision, "link_mode": "local-only"} if url else None
    name = area_rules.slug(Path(root).name) or "repository"
    edges = pick_edges(import_edges(root, files, areas))
    source = build(areas, files, edges, f"{Path(root).name} — areas and imports", repository)
    rel_json = f"{architecture.DIAGRAM_DIR}/{name}.areas.architecture.json"
    rel_html = f"{architecture.DIAGRAM_DIR}/{name}.areas.html"
    Path(root, architecture.DIAGRAM_DIR).mkdir(parents=True, exist_ok=True)
    target = Path(root, rel_json)
    dropped = 0
    reply: dict = {}
    for attempt in range(MAX_REPAIRS + 2):
        target.write_text(json.dumps(_for_archify(source), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        reply = _archify(skill["dir"], root, "validate", "architecture", rel_json, "--json",
                         "--repo-root", ".")
        if reply.get("ok"):
            break
        diagnostics = [d for d in reply.get("diagnostics") or [] if isinstance(d, dict)]
        before = len(source["connections"])
        if attempt >= MAX_REPAIRS or not repair(source, diagnostics):
            if not source["connections"]:
                break
            source["connections"] = []  # the areas alone, rather than nothing
        dropped += before - len(source["connections"])
    if not reply.get("ok"):
        first = next(iter(reply.get("diagnostics") or []), {}) or {}
        raise QuickDiagramError(
            f"Archify did not accept the diagram: {first.get('message') or reply.get('error')}"
        )
    delivered = _archify(skill["dir"], root, "deliver", "architecture", rel_json, rel_html,
                         "--json", "--repo-root", ".")
    if not delivered.get("ok"):
        raise QuickDiagramError(f"Archify could not render the page: {delivered.get('error')}")
    summary = architecture.scan(config, repo)
    return {
        "json": rel_json, "html": rel_html, "areas": len(source["components"]),
        "connections": len(source["connections"]), "dropped": dropped,
        "imports": sum(c.get("imports", 0) for c in source["connections"]),
        "diagrams": summary.get("diagrams", 0),
    }
