"""Quick diagram: areas and imports drawn by Archify's CLI, with no model."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from tasky import architecture, cli, quickdiagram, repos
from tasky.store import Store

REPO = "github.com/o/shop"
REAL_SKILL = Path.home() / ".claude" / "skills" / "archify"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    """A git checkout with an origin, where web imports api and api imports db."""
    root = tmp_path / "shop"
    for i in range(3):
        _write(root, f"src/web/page{i}.ts", f"import {{ x }} from '../api/handler{i}';\n")
        _write(root, f"src/api/handler{i}.ts", "import { q } from '../db';\nexport const x = 1;\n")
    _write(root, "src/db/index.ts", "export const q = 1;\n")
    _write(root, "tools/gen.py", "from app.models import user\nimport app.models\n")
    _write(root, "app/models/__init__.py", "")
    _write(root, "app/models/user.py", "from . import base\n")
    _write(root, "app/models/base.py", "")
    _write(root, "docs/readme.md", "x")
    _git(root, "init", "-q")
    _git(root, "remote", "add", "origin", "https://github.com/o/shop.git")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(
        repos, "resolve",
        lambda cwd: (REPO, "shop") if cwd.startswith(str(root)) else (f"path:{cwd}", cwd),
    )
    return root


def _areas(store: Store, root: Path) -> None:
    store.create_task(kind="prompt", body="hi", status="done", source="hook", cwd=str(root),
                      result="ok", finished_at="2026-09-01T10:00:00.000Z")
    repos.ensure(store, [str(root)])
    store.save_area(REPO, "web", description="Pages the shopper sees", paths=["src/web"],
                    source="model")
    store.save_area(REPO, "api", description="HTTP handlers for every page and more words here",
                    paths=["src/api"], source="model")
    store.save_area(REPO, "persistencia", aliases=["db"], paths=["src/db"], source="model")
    store.save_area(REPO, "1-tools", paths=["tools"], source="model")
    store.save_area(REPO, "modelos", paths=["app/models"], source="model")
    store.save_area(REPO, "ideas", source="model")  # no folders: not drawn


# -- pure rules ---------------------------------------------------------------------


def test_imports_become_relationships_between_areas(shop):
    areas = [
        {"id": 1, "paths": ["src/web"]}, {"id": 2, "paths": ["src/api"]},
        {"id": 3, "paths": ["src/db"]}, {"id": 4, "paths": ["tools"]},
        {"id": 5, "paths": ["app/models"]},
    ]
    files = sorted(
        str(p.relative_to(shop)) for p in shop.rglob("*") if p.is_file() and ".git" not in p.parts
    )
    edges = quickdiagram.import_edges(str(shop), files, areas)
    assert edges == Counter({(1, 2): 3, (2, 3): 3, (4, 5): 2})
    assert quickdiagram.pick_edges(edges) == [(1, 2, 3), (2, 3, 3)]  # tools: under the minimum
    both = Counter({(1, 2): 5, (2, 1): 3, (2, 3): 3})
    assert quickdiagram.pick_edges(both) == [(1, 2, 5), (2, 3, 3)]  # the stronger direction


def test_layout_puts_importers_above_and_leaves_corridors():
    place, cols = quickdiagram.layout([1, 2, 3, 4, 5], [(1, 2, 9), (1, 3, 4), (2, 3, 5)])
    assert place[1][0] < place[2][0] < place[3][0]  # web → api → db, one row each
    assert place[4][0] == place[5][0] > place[3][0]  # areas without lines go last
    assert cols >= 4 and all(0 <= c < cols for _, c in place.values())
    wide, cols = quickdiagram.layout(list(range(1, 5)), [(1, 2, 3), (1, 3, 3), (1, 4, 3)])
    assert sorted(wide[n][1] for n in (2, 3, 4)) == [0, 2, 4]  # every other column


def test_components_names_types_and_labels():
    taken: set[str] = set()
    assert quickdiagram._component_id("Autenticación", taken) == "autenticacion"
    assert quickdiagram._component_id("1-tools", taken) == "a-1-tools"
    assert quickdiagram._component_id("autenticación", taken) == "autenticacion-2"
    assert quickdiagram._sublabel("HTTP handlers for every page and more words here") == \
        "HTTP handlers for every page and more…"
    assert quickdiagram._component_type({"name": "persistencia", "aliases": ["db"]}) == "database"
    assert quickdiagram._component_type({"name": "web-ui", "aliases": []}) == "frontend"
    assert quickdiagram._component_type({"name": "checkout", "aliases": []}) == "backend"


def test_repairs_follow_archify_diagnostics():
    source = {
        "layout": {"cols": 5},
        "components": [
            {"id": "a", "row": 0, "col": 2}, {"id": "b", "row": 1, "col": 2},
            {"id": "c", "row": 2, "col": 2, "sublabel": "long"},
        ],
        "connections": [
            {"from": "a", "to": "c", "fromSide": "bottom", "toSide": "top"},
            {"from": "a", "to": "b", "fromSide": "bottom", "toSide": "top"},
            {"from": "b", "to": "c"},
        ],
    }
    diagnostics = [
        {"code": "clean-flow/edge-through-node", "evidence": {"obstacleId": "b"},
         "subject": {"collection": "connections", "index": 0}},
        {"code": "clean-flow/endpoint-side-direction",
         "subject": {"collection": "connections", "index": 1}},
        {"code": "clean-flow/label-clearance",
         "subject": {"collection": "connections", "index": 2}},
        {"code": "layout/constraint", "subject": {"identity": "c"}},
    ]
    assert quickdiagram.repair(source, diagnostics)
    assert source["components"][1]["col"] == 3  # moved out of the line's way
    assert "fromSide" not in source["connections"][1]  # routes itself now
    assert [(c["from"], c["to"]) for c in source["connections"]] == [("a", "c"), ("a", "b")]
    assert "sublabel" not in source["components"][2]
    assert not quickdiagram.repair(source, [{"code": "schema/enum", "subject": {}}])


# -- drawing ------------------------------------------------------------------------


FAKE_CLI = r"""
import fs from "node:fs";
const [cmd, , input, output] = process.argv.slice(2);
const log = process.env.FAKE_ARCHIFY_LOG;
const source = JSON.parse(fs.readFileSync(input, "utf8"));
fs.appendFileSync(log, JSON.stringify({ cmd, source }) + "\n");
const calls = fs.readFileSync(log, "utf8").trim().split("\n").length;
if (cmd === "deliver") {
  fs.writeFileSync(output, "<html>diagram</html>");
  console.log(JSON.stringify({ ok: true }));
} else if (calls === 1) {
  console.log(JSON.stringify({ ok: false, diagnostics: [{ code: "clean-flow/edge-through-node",
    subject: { collection: "connections", index: 0 }, evidence: { obstacleId: "nowhere" } }] }));
} else {
  console.log(JSON.stringify({ ok: true }));
}
"""


@pytest.fixture
def fake_archify(config, tmp_path, monkeypatch):
    place = config.claude_config_dir / "skills" / "archify"
    (place / "bin").mkdir(parents=True)
    (place / "SKILL.md").write_text("x")
    (place / "bin" / "archify.mjs").write_text(FAKE_CLI)
    log = tmp_path / "archify.log"
    monkeypatch.setenv("FAKE_ARCHIFY_LOG", str(log))
    return log


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_draw_writes_checks_and_scans(config, store, shop, fake_archify):
    _areas(store, shop)
    drawn = quickdiagram.draw(config, REPO)
    assert drawn == {
        "json": "docs/architecture/shop.areas.architecture.json",
        "html": "docs/architecture/shop.areas.html", "areas": 5, "connections": 1,
        "dropped": 1, "imports": 3, "diagrams": 1,
    }
    calls = [json.loads(line) for line in fake_archify.read_text().splitlines()]
    assert [c["cmd"] for c in calls] == ["validate", "validate", "deliver"]
    first = calls[0]["source"]
    revision = subprocess.run(["git", "-C", str(shop), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    assert first["meta"]["repository"] == {"url": "https://github.com/o/shop.git",
                                           "revision": revision, "link_mode": "local-only"}
    components = {c["id"]: c for c in first["components"]}
    assert set(components) == {"web", "api", "persistencia", "a-1-tools", "modelos"}
    assert components["persistencia"]["type"] == "database"
    assert components["web"]["sources"][0] == {"path": "src/web/page0.ts", "label": "src/web"}
    assert [(c["from"], c["to"]) for c in first["connections"]] == [("web", "api"),
                                                                    ("api", "persistencia")]
    assert all("imports" not in c for c in first["connections"])  # Tasky's note stays out
    assert (shop / drawn["html"]).read_text() == "<html>diagram</html>"
    [diagram] = store.architecture(REPO)["diagrams"]
    assert diagram["html"] == drawn["html"]
    assert architecture.overview(store, REPO)["areas"]  # the scan adopted nothing new


def test_draw_refuses_without_areas_or_skill(config, store, shop):
    store.create_task(kind="prompt", body="hi", status="done", source="hook", cwd=str(shop),
                      result="ok")
    repos.ensure(store, [str(shop)])
    with pytest.raises(quickdiagram.QuickDiagramError, match="map the areas first"):
        quickdiagram.draw(config, REPO)
    store.save_area(REPO, "web", paths=["src/web"], source="model")
    with pytest.raises(quickdiagram.QuickDiagramError, match="not installed"):
        quickdiagram.draw(config, REPO)
    assert cli.main(["architecture", "--repo", REPO, "--quick"]) == 1


@pytest.mark.skipif(
    not (REAL_SKILL / "bin" / "archify.mjs").is_file() or shutil.which("node") is None,
    reason="the real Archify skill is not installed",
)
def test_real_archify_accepts_the_quick_diagram(config, store, shop, monkeypatch):
    """Guards against Archify changing its schema: the real CLI must deliver the page."""
    monkeypatch.setattr(architecture, "archify_skill",
                        lambda config, root=None: {"dir": str(REAL_SKILL)})
    _areas(store, shop)
    drawn = quickdiagram.draw(config, REPO)
    assert drawn["connections"] == 2 and drawn["dropped"] == 0
    assert "<svg" in (shop / drawn["html"]).read_text()
