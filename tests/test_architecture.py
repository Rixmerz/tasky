"""Areas and architecture: the scanner, the model mapping, links, API, MCP and CLI."""

from __future__ import annotations

import http.client
import io
import json
import sqlite3
import subprocess
import threading

import pytest

from tasky import architecture, cli, history, mcp, quickdiagram, repos
from tasky import areas as area_rules
from tasky.config import load_token
from tasky.server import make_server
from tasky.store import Store

REPO = "github.com/o/shop"


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A checkout (no git: the scanner walks it) with every spec format and tool output."""
    base = tmp_path / "shop"
    for i in range(4):
        _write(base, f"src/checkout/step{i}.ts", "x")
        _write(base, f"src/auth/login{i}.ts", "x")
    _write(base, "src/checkout/cart/total.ts", "x")
    _write(base, "infra/ci/deploy.yml", "x")
    _write(base, "infra/ci/test.yml", "x")
    _write(base, "infra/ci/lint.yml", "x")
    _write(base, "node_modules/lib/index.js", "x")
    _write(base, "openspec/specs/checkout/spec.md", (
        "# checkout Specification\n\n## Purpose\nLets a shopper pay for the cart.\n\n"
        "## Requirements\n\n### Requirement: Pay by card\nThe system SHALL charge.\n\n"
        "#### Scenario: ok\n- WHEN x\n\n### Requirement: Keep the cart\nText.\n"
    ))
    _write(base, "openspec/changes/add-coupons/proposal.md", (
        "# Change: add coupons\n\n## Why\nShoppers ask for discounts.\n\n"
        "## What Changes\n- Coupons at checkout\n- A coupon admin page\n"
    ))
    _write(base, "openspec/changes/archive/2026-08-01-add-login/proposal.md",
           "## Why\nNobody could sign in.\n")
    _write(base, "specs/001-gift-cards/spec.md", (
        "# Feature Specification: Gift cards\n\n**Status**: Draft\n**Created**: 2026-07-02\n\n"
        "## Summary\nSell gift cards.\n\n- **FR-001**: System MUST sell cards.\n"
        "- **FR-002**: System MUST redeem cards.\n"
    ))
    _write(base, ".kiro/specs/returns/requirements.md", (
        "# Requirements Document\n\n## Introduction\nLet shoppers return items.\n\n"
        "### Requirement 1: Start a return\n**User Story:** as a shopper\n"
    ))
    _write(base, ".kiro/specs/returns/tasks.md", "- [x] 1. model\n- [ ] 2. api\n- [ ] 3. ui\n")
    _write(base, "doc/adr/0002_typescript.md", (
        "# Ocupar Typescript\n\n* Status: **Propuesto**\n* Date: 2019-09-03\n\n"
        "## Contexto\nTipos.\n\n## Decisión\nUsamos Typescript en todo el backend.\n"
    ))
    _write(base, "doc/adr/template.md", "# Title\n")
    _write(base, "docs/shop.architecture.json", json.dumps({
        "schema_version": 1, "diagram_type": "architecture", "meta": {"title": "Shop"},
        "components": [
            {"id": "pay", "type": "service", "label": "Payments",
             "sources": [{"path": "src/checkout/step0.ts", "line": 1}]},
            {"id": "cart", "type": "service", "label": "Cart",
             "sources": [{"path": "src/checkout/cart", "line": 1}]},
        ],
        "boundaries": [{"kind": "region", "label": "Checkout Flow", "wraps": ["pay", "cart"]}],
        "connections": [],
    }))
    graph = {"nodes": [
        *({"id": f"a{i}", "source_file": f"src/auth/login{i}.ts", "community": 0}
          for i in range(4)),
        {"id": "x", "source_file": "gone.ts", "community": 0},
    ], "links": []}
    _write(base, "graphify-out/graph.json", json.dumps(graph))
    _write(base, "graphify-out/.graphify_labels.json", json.dumps({"0": "Sign-in"}))
    monkeypatch.setattr(
        repos, "resolve",
        lambda cwd: (REPO, "shop") if cwd.startswith(str(base)) else (f"path:{cwd}", cwd),
    )
    return base


def _task(store, cwd, body, prompt_id, files=(), finished="2026-09-01T10:00:00.000Z"):
    task = store.create_task(kind="prompt", body=body, status="done", source="hook", cwd=cwd,
                             prompt_id=prompt_id, result="ok", finished_at=finished)
    store.add_messages([
        {"uuid": f"{prompt_id}-{i}", "session_id": "s1", "prompt_id": prompt_id,
         "role": "assistant", "kind": "tool_use", "tool_name": "Edit", "file_path": path}
        for i, path in enumerate(files)
    ])
    repos.ensure(store, [cwd])
    return task


class FakeClaude:
    def __init__(self, output, cost=0.07):
        self.output, self.cost, self.calls = output, cost, []

    def __call__(self, cmd, *, input, env, **kwargs):
        self.calls.append({"cmd": cmd, "input": input})
        reply = {"is_error": False, "total_cost_usd": self.cost, "structured_output": self.output}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")


# -- pure rules ---------------------------------------------------------------------


def test_area_rules():
    assert area_rules.slug("  Check Out_flow! ") == "check-out-flow"
    assert area_rules.slug("Autenticación") == "autenticación"
    assert area_rules.relative("/r/app/src/a.ts", ["/r", "/r/app"]) == "src/a.ts"
    assert area_rules.relative("/elsewhere/a.ts", ["/r"]) is None
    assert area_rules.relative("./src/a.ts", []) == "src/a.ts"
    assert area_rules.relative("/r/.claude/worktrees/fix-x/src/a.ts", ["/r"]) == "src/a.ts"
    areas = [{"id": 1, "name": "checkout", "aliases": ["pago"], "paths": ["src/checkout"]},
             {"id": 2, "name": "cart", "aliases": ["checkout-cart"],
              "paths": ["src/checkout/cart"]}]
    assert area_rules.area_of("src/checkout/cart/x.ts", areas) == [2]
    assert area_rules.area_of("src/checkout/x.ts", areas) == [1]
    assert area_rules.area_of("src/checkoutx/x.ts", areas) == []
    assert area_rules.files_areas(["src/checkout/a", "src/checkout/b", "src/checkout/cart/c"],
                                  areas) == [1, 2]
    assert area_rules.by_name("Pago", areas) == 1
    assert area_rules.by_name("checkout-cart", areas) == 2
    assert area_rules.spec_key("openspec/specs/checkout/spec.md") == "checkout"
    assert area_rules.spec_key("openspec/changes/archive/2026-08-01-add-login/proposal.md") \
        == "add-login"
    assert area_rules.spec_key("specs/001-gift-cards/spec.md") == "gift-cards"
    assert area_rules.spec_key("doc/adr/0002_typescript.md") == "typescript"


# -- scanner ------------------------------------------------------------------------


def test_read_specs_understands_every_format(root):
    files = architecture.list_files(str(root))
    assert "node_modules/lib/index.js" not in files
    specs = {s["path"]: s for s in architecture.read_specs(str(root), files)}
    assert set(specs) == {
        "openspec/specs/checkout/spec.md", "openspec/changes/add-coupons/proposal.md",
        "openspec/changes/archive/2026-08-01-add-login/proposal.md",
        "specs/001-gift-cards/spec.md", ".kiro/specs/returns/requirements.md",
        "doc/adr/0002_typescript.md",
    }
    cap = specs["openspec/specs/checkout/spec.md"]
    assert (cap["kind"], cap["title"], cap["status"]) == ("openspec", "checkout", "current")
    assert cap["summary"] == "Lets a shopper pay for the cart."
    assert cap["items"] == ["Pay by card", "Keep the cart"]
    change = specs["openspec/changes/add-coupons/proposal.md"]
    assert (change["status"], change["items"]) == (
        "proposed", ["Coupons at checkout", "A coupon admin page"])
    archived = specs["openspec/changes/archive/2026-08-01-add-login/proposal.md"]
    assert (archived["title"], archived["status"], archived["dated"]) == (
        "add-login", "archived", "2026-08-01")
    kit = specs["specs/001-gift-cards/spec.md"]
    assert (kit["title"], kit["status"], kit["dated"]) == ("Gift cards", "Draft", "2026-07-02")
    assert kit["items"] == ["FR-001: System MUST sell cards.", "FR-002: System MUST redeem cards."]
    kiro = specs[".kiro/specs/returns/requirements.md"]
    assert (kiro["title"], kiro["status"]) == ("returns", "1/3 tasks")
    assert kiro["items"] == ["1: Start a return"]
    adr = specs["doc/adr/0002_typescript.md"]
    assert (adr["title"], adr["status"], adr["dated"]) == (
        "Ocupar Typescript", "Propuesto", "2019-09-03")
    assert adr["summary"] == "Usamos Typescript en todo el backend."


def test_scan_adopts_archify_boundaries_as_areas_once(config, store, root):
    _task(store, str(root), "hi", "p0")
    summary = architecture.scan(config, REPO)
    assert summary["specs"] == 6 and summary["adopted"] == 1
    assert summary["sources"]["archify"] == 1 and summary["sources"]["graphify"] == 1
    [area] = store.areas(REPO)
    assert (area["name"], area["source"]) == ("checkout-flow", "archify")
    assert area["aliases"] == ["Checkout Flow"]
    assert area["paths"] == ["src/checkout/step0.ts", "src/checkout/cart"]
    candidates = store.architecture(REPO)["candidates"]
    graph = next(c for c in candidates if c["source"] == "graphify")
    assert (graph["name"], graph["paths"], graph["files"]) == ("Sign-in", ["src/auth"], 4)
    folders = {c["name"] for c in candidates if c["source"] == "folder"}
    assert {"checkout", "auth", "infra"} <= folders and "openspec" not in folders
    store.hide_area(area["id"])
    assert architecture.scan(config, REPO)["adopted"] == 0  # the user removed it: stays removed


def test_scan_of_a_repo_with_no_folder_on_disk(config, store):
    _task(store, "/gone/app", "hi", "p0")
    with pytest.raises(architecture.ArchitectureError):
        architecture.scan(config, "path:/gone/app")


# -- mapping ------------------------------------------------------------------------


def test_map_areas_checks_everything_the_model_returns(config, store, root):
    _task(store, str(root), "fix login", "p1", [f"{root}/src/auth/login0.ts"])
    store.add_milestone(cwd=str(root), title="Coupons live", topic="cupones", happened_on=None,
                        task_ids=[], commits=[])
    user = store.save_area(REPO, "ops", kind="technical", description="mine", source="user",
                           paths=["infra"])
    stale = store.save_area(REPO, "legacy", source="model", paths=["old"])
    unwanted = store.save_area(REPO, "infra", source="model")
    store.hide_area(unwanted["id"], by_user=True)
    fake = FakeClaude({"areas": [
        {"name": "Checkout", "kind": "business", "description": "Pagar el carrito.",
         "aliases": ["pago", "checkout", "cupones"],
         "paths": ["src/checkout", "does/not/exist", "src/auth"],
         "specs": ["openspec/specs/checkout/spec.md", "invented.md"]},
        {"name": "auth", "kind": "weird", "aliases": ["login"], "paths": ["src/auth", "src/auth"]},
        {"existing_id": user["id"], "name": "renamed-by-model", "kind": "business",
         "paths": ["infra/ci"], "aliases": ["devops"]},
        {"name": "", "paths": []},
        {"name": "infra", "kind": "technical", "paths": ["infra"]},  # the user deleted it
    ]})
    summary = architecture.map_areas(config, REPO, model="sonnet", run=fake)
    assert summary["error"] is None and summary["cost_usd"] == 0.07
    assert (summary["added"], summary["updated"], summary["retired"]) == (2, 1, 1)
    prompt = fake.calls[0]["input"]
    assert "<tree>" in prompt and "src/checkout/ (5)" in prompt
    assert "[archify] Checkout Flow" in prompt and "cupones (1)" in prompt
    assert "src/auth (1 edits)" in prompt
    assert "--model" in fake.calls[0]["cmd"] and "sonnet" in fake.calls[0]["cmd"]
    by_name = {a["name"]: a for a in store.areas(REPO)}
    assert set(by_name) == {"checkout", "auth", "ops"}
    checkout = by_name["checkout"]
    assert checkout["kind"] == "business" and checkout["description"] == "Pagar el carrito."
    assert checkout["paths"] == ["src/checkout", "src/auth"]  # invented path dropped
    assert checkout["specs"] == ["openspec/specs/checkout/spec.md"]
    assert checkout["aliases"] == ["pago", "cupones"]  # its own name is not an alias
    assert by_name["auth"]["paths"] == [] and by_name["auth"]["kind"] == "technical"
    ops = by_name["ops"]  # the user's area keeps its name and description, gains the rest
    assert (ops["description"], ops["kind"], ops["source"]) == ("mine", "technical", "user")
    assert ops["paths"] == ["infra", "infra/ci"] and ops["aliases"] == ["devops"]
    assert store.get_area(stale["id"])["deleted_at"] is not None
    assert store.architecture(REPO)["mapped_at"] and store.architecture(REPO)["state"] == "idle"


def test_map_areas_renames_an_existing_area_and_records_failures(config, store, root):
    _task(store, str(root), "hi", "p0")
    old = store.save_area(REPO, "pay", source="model", paths=["src/checkout"])
    fake = FakeClaude({"areas": [{"existing_id": old["id"], "name": "checkout",
                                  "kind": "business", "paths": ["src/checkout"]}]})
    architecture.map_areas(config, REPO, run=fake)
    assert store.get_area(old["id"])["name"] == "checkout"

    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="not json", stderr="boom")

    summary = architecture.map_areas(config, REPO, run=failing)
    assert "boom" in summary["error"]
    row = store.architecture(REPO)
    assert row["state"] == "idle" and "boom" in row["error"]
    with pytest.raises(architecture.ArchitectureError):
        architecture.map_areas(config, REPO, model="haiku", run=fake)
    assert store.begin_area_mapping(REPO, "sonnet", stale_after_s=60)
    with pytest.raises(architecture.ArchitectureError, match="already"):
        architecture.map_areas(config, REPO, run=fake)


# -- links --------------------------------------------------------------------------


def test_overview_links_tasks_problems_milestones_and_specs(config, store, root):
    cwd = str(root)
    t1 = _task(store, cwd, "pay with card", "p1",
               [f"{root}/src/checkout/step0.ts", f"{root}/src/checkout/cart/total.ts"])
    t2 = _task(store, cwd, "login bug", "p2", [f"{root}/src/auth/login1.ts"],
               finished="2026-09-02T10:00:00.000Z")
    _task(store, cwd, "readme", "p3", [f"{root}/README.md"])
    _task(store, cwd, "question", "p4")
    checkout = store.save_area(REPO, "checkout", kind="business", aliases=["pago"],
                               paths=["src/checkout"], source="model")
    auth = store.save_area(REPO, "auth", aliases=["login"], paths=["src/auth"], source="model",
                           specs=["doc/adr/0002_typescript.md"])
    by_topic = store.add_problem(cwd=cwd, title="Card declined", topic="Pago", symptom="",
                                 first_seen=None, last_seen="2026-09-01", task_ids=[])
    by_tasks = store.add_problem(cwd=cwd, title="Session lost", topic="sesiones", symptom="",
                                 first_seen=None, last_seen="2026-09-02", task_ids=[t2["id"]])
    store.add_problem(cwd=cwd, title="Slow build", topic="build", symptom="", first_seen=None,
                      last_seen=None, task_ids=[])
    store.add_milestone(cwd=cwd, title="Login works", topic="login", happened_on="2026-09-02",
                        task_ids=[], commits=[])
    # An edit from a turn that has no task row (a session from before the hooks) still counts.
    store.add_messages([
        {"uuid": "old-1", "session_id": "s0", "prompt_id": "old", "role": "assistant",
         "kind": "tool_use", "tool_name": "Write", "ts": "2026-08-30T09:00:00Z",
         "file_path": f"{root}/.claude/worktrees/wt/src/auth/new.ts"},
        {"uuid": "old-2", "session_id": "s0", "prompt_id": "old", "role": "assistant",
         "kind": "tool_use", "tool_name": "Edit", "file_path": f"{root}/openspec/specs/x/spec.md"},
        {"uuid": "old-3", "session_id": "s0", "prompt_id": "old", "role": "assistant",
         "kind": "tool_use", "tool_name": "Read", "file_path": f"{root}/src/checkout/step3.ts"},
    ])
    architecture.scan(config, REPO)
    view = architecture.overview(store, REPO)
    areas = {a["name"]: a for a in view["areas"]}
    assert (areas["auth"]["turns"], areas["auth"]["files"]) == (2, 2)
    assert areas["auth"]["last_edit"] == "2026-08-30"
    assert (areas["checkout"]["turns"], areas["checkout"]["files"]) == (1, 2)  # reads don't count
    assert view["unplaced_dirs"] == ["."]  # README.md; spec edits are not code without an area
    assert areas["checkout"]["tasks"] == 1 and areas["auth"]["tasks"] == 1
    assert areas["checkout"]["recent_tasks"][0]["id"] == t1["id"]
    assert [p["id"] for p in areas["checkout"]["problems"]] == [by_topic]
    assert [p["id"] for p in areas["auth"]["problems"]] == [by_tasks]
    assert [m["title"] for m in areas["auth"]["milestones"]] == ["Login works"]
    assert areas["checkout"]["spec_items"] == ["openspec/specs/checkout/spec.md"]
    assert areas["auth"]["spec_items"] == ["doc/adr/0002_typescript.md"]
    assert "specs/001-gift-cards/spec.md" in view["unlinked_specs"]
    assert view["unplaced_topics"] == ["build"]
    assert view["edited_without_area"] == 1  # README.md; the question edited nothing
    # "checkout-flow" was adopted? No: areas already existed, so the scan adopted nothing.
    assert {a["name"] for a in store.areas(REPO)} == {"checkout", "auth"}

    state = {t["id"]: t for t in store.state()["tasks"]}
    assert state[t1["id"]]["areas"] == [{"id": checkout["id"], "name": "checkout"}]
    assert state[t2["id"]]["areas"] == [{"id": auth["id"], "name": "auth"}]


def test_areas_follow_files_edited_in_a_worktree(config, store, root, tmp_path, monkeypatch):
    worktree = tmp_path / "shop-wt"
    worktree.mkdir()
    real_resolve = repos.resolve
    monkeypatch.setattr(repos, "resolve", lambda cwd: (REPO, "shop") if cwd.startswith(
        str(worktree)) else real_resolve(cwd))
    task = _task(store, str(worktree), "edit in worktree", "p9",
                 [f"{worktree}/src/checkout/step1.ts"])
    store.save_area(REPO, "checkout", paths=["src/checkout"], source="model")
    assert store.task_areas([task])[task["id"]][0]["name"] == "checkout"


def test_rename_and_restore_by_name(store):
    a = store.save_area(REPO, "a", source="model")
    b = store.save_area(REPO, "b", source="model")
    assert store.rename_area(a["id"], "b") is None
    store.hide_area(b["id"])
    assert store.rename_area(a["id"], "b")["name"] == "b"
    again = store.save_area(REPO, "c", source="model")
    store.hide_area(again["id"])
    restored = store.save_area(REPO, "c", paths=["x"])
    assert restored["id"] == again["id"] and restored["deleted_at"] is None


# -- migration ----------------------------------------------------------------------


def test_v6_database_migrates_without_rereading_transcripts(tmp_path, config):
    with Store.open(config) as store:
        store.set_transcript_offset("/t.jsonl", 10, 10)
        store.set_repo("/p", "github.com/o/p", "p", "2026-09-01T00:00:00Z")
        store._conn.execute("PRAGMA user_version = 6")
        store._conn.execute("ALTER TABLE repos DROP COLUMN root")
        store._conn.commit()
    with Store.open(config) as store:
        assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert store.transcript_offset("/t.jsonl") == (10, 10)
        row = store.repo_row("/p")
        assert row["root"] is None and row["checked_at"] == ""  # resolved again, with its root
    conn = sqlite3.connect(config.db_path)
    assert {r[1] for r in conn.execute("PRAGMA table_info(areas)")} >= {"aliases", "paths"}
    conn.close()


# -- MCP and CLI --------------------------------------------------------------------


def _mcp(config, **arguments):
    out = io.StringIO()
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "get_architecture", "arguments": arguments}}
    mcp.serve(config, io.StringIO(json.dumps(request) + "\n"), out)
    result = json.loads(out.getvalue())["result"]
    return result["isError"], result["content"][0]["text"]


def test_mcp_get_architecture(config, store, root, monkeypatch):
    monkeypatch.setattr(mcp.os, "getcwd", lambda: str(root))
    assert "No architecture recorded" in _mcp(config)[1]
    _task(store, str(root), "pay", "p1", [f"{root}/src/checkout/step0.ts"])
    problem = store.add_problem(cwd=str(root), title="Card declined", topic="pago", symptom="",
                                first_seen=None, last_seen=None, task_ids=[])
    store.add_attempt(problem, description="retry the charge", outcome="failed", why="same card",
                      evidence="", believed_from=None, invalidated_on=None, task_ids=[],
                      commits=[], source="sync")
    store.save_area(REPO, "checkout", kind="business", description="Paying.",
                    aliases=["pago"], paths=["src/checkout"], source="model")
    architecture.scan(config, REPO)
    error, text = _mcp(config)
    assert not error
    assert "- checkout [business] Paying. | paths: src/checkout | aka: pago | 1 turn(s) edited " \
           "1 file(s), 1 problem(s) (1 open), 1 spec(s)" in text
    assert "Specs in no area:" in text
    error, text = _mcp(config, area="Pago")
    assert not error
    assert "spec openspec/specs/checkout/spec.md [openspec, current] checkout" in text
    assert "  · Pay by card" in text
    assert "✗ failed: retry the charge | why: same card" in text
    assert "task #" in text and "[done] pay" in text
    error, text = _mcp(config, area="nope")
    assert error and "areas: checkout" in text


def test_cli_architecture(config, store, root, monkeypatch):
    _task(store, str(root), "hi", "p0")
    out = io.StringIO()  # the CLI reads TASKY_HOME, set to config's home by conftest
    assert cli.main(["architecture", "--cwd", str(root)], out=out) == 0
    assert "6 spec(s)" in out.getvalue() and "1 area(s) adopted" in out.getvalue()
    monkeypatch.setattr(architecture.history, "call_model", lambda *a, **k: ({"areas": [
        {"name": "checkout", "kind": "business", "paths": ["src/checkout"]}]}, 0.07))
    out = io.StringIO()
    assert cli.main(["architecture", "--repo", REPO, "--map"], out=out) == 0
    assert "1 added" in out.getvalue() and "$0.0700" in out.getvalue()
    assert cli.main(["architecture", "--repo", "nope/x"], out=io.StringIO()) == 1


# -- HTTP API -----------------------------------------------------------------------


@pytest.fixture
def api(config, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html></html>")
    token = load_token(config)
    srv = make_server(config, port=0, web_dir=web, token=token)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port)
    headers = {"X-Tasky-Token": token, "Content-Type": "application/json"}

    def call(method, path, body=None):
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else b"",
                     headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, json.loads(data) if data else None

    call.server = srv
    yield call
    conn.close()
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


def test_api_architecture_scan_and_view(api, store, root):
    _task(store, str(root), "pay", "p1", [f"{root}/src/checkout/step0.ts"])
    status, body = api("GET", "/api/architecture")
    assert status == 200 and body["repo"] == REPO and body["view"]["areas"] == []
    assert body["models"] == ["sonnet", "opus"] and body["repos"][0]["scanned_at"] is None
    status, summary = api("POST", "/api/architecture/scan", {"repo": REPO})
    assert status == 200 and summary["specs"] == 6
    status, body = api("GET", f"/api/architecture?repo={REPO}")
    assert body["view"]["areas"][0]["name"] == "checkout-flow"
    assert body["view"]["areas"][0]["tasks"] == 1
    assert body["repos"][0]["areas"] == 1 and body["view"]["scan"]["files"] > 10
    assert api("POST", "/api/architecture/scan", {"repo": "x/y"})[0] == 404
    assert api("POST", "/api/architecture/scan", {})[0] == 400


def test_api_map_starts_a_background_run(api, store, root, monkeypatch):
    _task(store, str(root), "hi", "p0")
    launched = []
    monkeypatch.setattr(api.server, "popen", lambda *a, **k: launched.append(a[0]))
    status, _ = api("POST", "/api/architecture/map", {"repo": REPO, "model": "opus"})
    assert status == 202
    assert launched[0][-5:] == ["--repo", REPO, "--map", "--model", "opus"]
    assert api("POST", "/api/architecture/map", {"repo": REPO, "model": "haiku"})[0] == 400
    store.begin_area_mapping(REPO, "sonnet", stale_after_s=60)
    assert api("POST", "/api/architecture/map", {"repo": REPO})[0] == 409


def test_api_area_editing(api, store, root):
    _task(store, str(root), "hi", "p0")
    status, area = api("POST", "/api/areas", {
        "repo": REPO, "name": "Check Out", "kind": "business", "aliases": ["pago"],
        "paths": ["./src/checkout/"]})
    assert status == 201 and area["name"] == "check-out" and area["paths"] == ["src/checkout"]
    assert area["source"] == "user"
    assert api("POST", "/api/areas", {"repo": REPO, "name": "check out"})[0] == 409
    assert api("POST", "/api/areas", {"repo": REPO, "name": " "})[0] == 400
    assert api("POST", "/api/areas", {"repo": REPO, "name": "x", "kind": "odd"})[0] == 400
    assert api("POST", "/api/areas", {"repo": REPO, "name": "x", "paths": "src"})[0] == 400
    model_area = store.save_area(REPO, "auth", source="model")
    status, edited = api("PATCH", f"/api/areas/{model_area['id']}",
                         {"name": "login", "description": "Sign in."})
    assert status == 200 and edited["name"] == "login" and edited["source"] == "user"
    assert api("PATCH", f"/api/areas/{model_area['id']}", {"name": "check-out"})[0] == 409
    assert api("PATCH", "/api/areas/999", {"kind": "business"})[0] == 404
    assert api("DELETE", f"/api/areas/{area['id']}")[0] == 200
    assert api("DELETE", f"/api/areas/{area['id']}")[0] == 404
    assert [a["name"] for a in store.areas(REPO)] == ["login"]


# -- regressions found in review ----------------------------------------------------


def test_toplevel_keeps_the_symlinked_spelling(tmp_path):
    real = tmp_path / "real" / "shop"
    (real / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(real)], check=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real")
    assert repos.toplevel(str(link / "shop")) == str(link / "shop")
    assert repos.toplevel(str(link / "shop" / "src")) == str(link / "shop")
    assert repos.toplevel(str(tmp_path / "nowhere")) is None


def test_spec_symlinked_outside_the_checkout_is_not_read(root, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("# Key\n\nPRIVATE KEY BODY\n")
    (root / "specs" / "002-leak").mkdir(parents=True)
    (root / "specs" / "002-leak" / "spec.md").symlink_to(secret)
    specs = architecture.read_specs(str(root), architecture.list_files(str(root)))
    assert all("PRIVATE" not in s["summary"] for s in specs)
    assert "specs/002-leak/spec.md" not in {s["path"] for s in specs}


def test_malformed_or_large_tool_output_does_not_stop_the_scan(config, store, root):
    _write(root, "docs/bad.architecture.json", json.dumps({
        "diagram_type": "architecture",
        "components": [{"id": ["x"]}, {"id": "a", "sources": ["src/auth"]}],
        "boundaries": [{"label": "Bad", "wraps": [["a"], "a"]}],
    }))
    big = {"diagram_type": "architecture", "meta": {"pad": "x" * 300_000},
           "components": [{"id": "c", "sources": [{"path": "infra/ci/test.yml"}]}],
           "boundaries": [{"label": "Big One", "wraps": ["c"]}]}
    _write(root, "docs/big.architecture.json", json.dumps(big))
    _write(root, "tools/graphify-out/graph.json", json.dumps({"nodes": {"not": "a list"}}))
    _task(store, str(root), "hi", "p0")
    architecture.scan(config, REPO)
    names = {c["name"] for c in store.architecture(REPO)["candidates"]}
    assert {"Bad", "Big One", "Checkout Flow"} <= names


def test_graphify_below_the_root_is_found(config, store, root):
    nested = {"nodes": [{"id": f"n{i}", "source_file": f"step{i}.ts", "community": 3}
                        for i in range(4)]}
    _write(root, "src/checkout/graphify-out/graph.json", json.dumps(nested))
    _task(store, str(root), "hi", "p0")
    architecture.scan(config, REPO)
    graph = [c for c in store.architecture(REPO)["candidates"] if c["source"] == "graphify"]
    assert any(c["paths"] == ["src/checkout"] for c in graph)


def test_mapping_handles_duplicate_and_deleted_names(config, store, root):
    _task(store, str(root), "hi", "p0")
    keep = store.save_area(REPO, "pay", source="model", paths=["src/checkout"])
    gone = store.save_area(REPO, "payments", source="model")
    store.hide_area(gone["id"], by_user=True)
    fake = FakeClaude({"areas": [
        {"name": "billing", "kind": "business", "paths": ["src/auth"]},
        {"name": "Billing", "kind": "business", "paths": ["infra"]},
        {"existing_id": keep["id"], "name": "payments", "kind": "business",
         "paths": ["src/checkout"]},
    ]})
    summary = architecture.map_areas(config, REPO, run=fake)
    by_name = {a["name"]: a for a in store.areas(REPO)}
    assert set(by_name) == {"billing", "pay"}
    assert by_name["billing"]["paths"] == ["src/auth"]
    assert by_name["pay"]["id"] == keep["id"] and summary["retired"] == 0


def test_task_areas_are_cached_until_areas_change(store, root):
    task = _task(store, str(root), "pay", "p1", [f"{root}/src/checkout/step0.ts"])
    area = store.save_area(REPO, "checkout", paths=["src/checkout"], source="model")
    assert store.task_areas([task])[task["id"]][0]["name"] == "checkout"
    store.rename_area(area["id"], "pago")
    assert store.task_areas([task])[task["id"]][0]["name"] == "pago"
    store.save_area(REPO, "pago", paths=["src/auth"])
    assert store.task_areas([task]) == {}


def test_edits_in_a_nested_worktree_count_once(store, root):
    store.set_repo(str(root / ".claude/worktrees/wt"), REPO, "shop", "2026-09-01T00:00:00Z",
                   str(root / ".claude/worktrees/wt"))
    _task(store, str(root), "x", "p1", [f"{root}/.claude/worktrees/wt/src/auth/a.ts"])
    assert len(store.edits_under(store.repo_roots(REPO))) == 1


def test_api_stale_mapping_claim_and_recreated_area(api, store, root, monkeypatch):
    _task(store, str(root), "hi", "p0")
    monkeypatch.setattr(api.server, "popen", lambda *a, **k: None)
    store.begin_area_mapping(REPO, "sonnet", stale_after_s=60)
    store._conn.execute("UPDATE architecture SET started_at = '2026-01-01T00:00:00.000Z'")
    store._conn.commit()
    assert api("POST", "/api/architecture/map", {"repo": REPO})[0] == 202
    status, area = api("POST", "/api/areas", {"repo": REPO, "name": "auth", "kind": "business",
                                              "description": "old", "aliases": ["login"]})
    api("DELETE", f"/api/areas/{area['id']}")
    status, again = api("POST", "/api/areas", {"repo": REPO, "name": "auth"})
    assert status == 201
    assert (again["kind"], again["description"], again["aliases"]) == ("technical", "", [])


# -- drawing with the Archify skill -------------------------------------------------


@pytest.fixture
def skill(config):
    place = config.claude_config_dir / "skills" / "archify"
    (place / "bin").mkdir(parents=True)
    (place / "SKILL.md").write_text("---\nname: archify\n---\n")
    (place / "bin" / "archify.mjs").write_text("")
    return place


def test_archify_skill_detection(config, root, tmp_path):
    assert architecture.archify_skill(config, str(root)) is None
    local = root / ".claude" / "skills" / "archify"
    (local / "bin").mkdir(parents=True)
    (local / "SKILL.md").write_text("x")
    assert architecture.archify_skill(config, str(root)) is None  # no CLI, not usable
    (local / "bin" / "archify.mjs").write_text("")
    assert architecture.archify_skill(config, str(root))["dir"] == str(local)


def test_diagram_request_names_areas_and_limits_tools(config, store, root, skill):
    _task(store, str(root), "hi", "p0")
    store.save_area(REPO, "checkout", paths=["src/checkout"], source="model")
    request = architecture.diagram_request(config, store, REPO)
    assert request["root"] == str(root) and request["json"] == \
        "docs/architecture/shop.architecture.json"
    assert "checkout (src/checkout)" in request["body"]
    assert f"node {skill}/bin/archify.mjs deliver architecture" in request["body"]
    args = request["args"]
    assert args[:4] == ["--strict-mcp-config", "--add-dir", str(skill), "--allowedTools"]
    assert f"Bash(node {skill}/bin/archify.mjs:*)" in args
    assert not any(a in ("Bash", "Bash(*)", "Bash(node:*)") for a in args)
    assert args[-2:] == ["--effort", architecture.DRAW_EFFORT]


def test_scan_lists_diagrams_with_their_page(config, store, root):
    _write(root, "docs/shop.html", "<html></html>")
    _task(store, str(root), "hi", "p0")
    architecture.scan(config, REPO)
    [diagram] = store.architecture(REPO)["diagrams"]
    assert diagram == {"file": "docs/shop.architecture.json", "title": "Shop",
                       "html": "docs/shop.html"}


def test_api_draws_rescans_and_opens(api, store, root, skill, monkeypatch):
    _task(store, str(root), "hi", "p0")
    launched = []
    monkeypatch.setattr(api.server, "popen", lambda cmd, **k: launched.append(cmd))
    status, body = api("GET", "/api/architecture")
    assert body["archify"]["installed"] is True and body["diagram_task"] is None
    status, body = api("POST", "/api/architecture/diagram", {"repo": REPO, "model": "opus"})
    assert status == 202 and body["task"]["status"] == "running"
    cmd = launched[-1]
    assert cmd[cmd.index("--model") + 1] == "opus" and "acceptEdits" in cmd
    assert "--allowedTools" in cmd
    assert api("POST", "/api/architecture/diagram", {"repo": REPO})[0] == 409  # still drawing
    task_id = body["task"]["id"]
    # The session wrote the page and finished: the next read scans for it by itself.
    _write(root, "docs/shop.html", "<html></html>")
    store.update_task(task_id, status="done", result="docs/shop.html",
                      finished_at="2999-01-01T00:00:00.000Z")
    status, body = api("GET", "/api/architecture")
    assert body["diagram_task"]["status"] == "done"
    assert body["view"]["scan"]["diagrams"][0]["html"] == "docs/shop.html"
    status, _ = api("POST", "/api/architecture/open", {"repo": REPO, "path": "docs/shop.html"})
    assert status == 200 and launched[-1][-1] == str((root / "docs/shop.html").resolve())
    for path in ("../../etc/passwd", "src/checkout/step0.ts", 3):
        assert api("POST", "/api/architecture/open", {"repo": REPO, "path": path})[0] == 404


def test_api_diagram_without_the_skill(api, store, root):
    _task(store, str(root), "hi", "p0")
    status, body = api("POST", "/api/architecture/diagram", {"repo": REPO})
    assert status == 409 and "not installed" in body["error"]
    assert api("POST", "/api/architecture/diagram", {"repo": REPO, "model": "haiku"})[0] == 400


# -- stage 3: the sync names areas, links specs, learns aliases; the search ladder ---------


def test_query_areas_finds_names_aliases_and_prefixes():
    areas = [
        {"id": 1, "name": "auth", "aliases": ["inicio de sesión", "login"]},
        {"id": 2, "name": "database", "aliases": ["mongodb", "db"]},
        {"id": 3, "name": "models", "aliases": ["modelos", "mocks"]},
    ]
    assert area_rules.query_areas("el Inicio de Sesion falla", areas) == [(1, "inicio de sesion")]
    assert area_rules.query_areas("logins rotos con mongo", areas) == [(1, "logins"), (2, "mongo")]
    assert area_rules.query_areas("mo", areas) == []  # too short to be a prefix
    assert area_rules.query_areas("modelos", areas) == [(3, "modelos")]
    assert area_rules.by_name("Inicio de sesión", areas) == 1


def test_sync_names_areas_links_specs_and_learns_aliases(config, store, root):
    cwd = str(root)
    task = _task(store, cwd, "el cobro con tarjeta falla", "p1", [f"{root}/src/checkout/step0.ts"])
    store.save_area(REPO, "checkout", kind="business", aliases=["pago"], paths=["src/checkout"],
                    source="model")
    store.save_area(REPO, "auth", paths=["src/auth"], source="user")
    architecture.scan(config, REPO)
    spec = "openspec/specs/checkout/spec.md"
    fake = FakeClaude({
        "problems": [
            {"title": "Card declined", "topic": "Pago", "task_ids": [task["id"]],
             "specs": [spec, "not/a/spec.md"]},
            {"title": "Odd rounding", "topic": "misc", "task_ids": [task["id"]], "specs": [spec]},
        ],
        "milestones": [{"title": "Cards work", "topic": "sesiones", "task_ids": [task["id"]],
                        "specs": [spec]}],
        "compact": [],
        "aliases": [
            {"area": "checkout", "alias": "Cobro"},  # written by the developer: kept
            {"area": "checkout", "alias": "facturación"},  # never written: dropped
            {"area": "auth", "alias": "tarjeta"},  # the developer's own area: left alone
            {"area": "nope", "alias": "falla"},
        ],
    })
    summary = history.sync(config, REPO, run=fake)
    assert summary["error"] is None and summary["aliases"] == 1
    cmd = fake.calls[0]["cmd"]
    assert cmd[cmd.index("--effort") + 1] == history.SYNC_EFFORT == "high"
    prompt = fake.calls[0]["input"]
    assert "checkout [business] aka: pago" in prompt and f"{spec} | checkout | current" in prompt
    problems = {p["title"]: p for p in store.problems(REPO)}
    assert problems["Card declined"]["topic"] == "checkout"
    assert problems["Card declined"]["specs"] == [spec]
    assert store.milestones(REPO)[0]["specs"] == [spec]
    checkout = next(a for a in store.areas(REPO) if a["name"] == "checkout")
    assert checkout["aliases"] == ["pago", "cobro"] and checkout["source"] == "model"
    assert next(a for a in store.areas(REPO) if a["name"] == "auth")["aliases"] == []
    view = architecture.overview(store, REPO)
    areas = {a["name"]: a for a in view["areas"]}
    # "misc" names no area: its spec places it.
    assert {p["title"] for p in areas["checkout"]["problems"]} == {"Card declined", "Odd rounding"}
    linked = next(s for s in view["specs"] if s["path"] == spec)
    assert len(linked["problems"]) == 2 and linked["milestones"][0]["title"] == "Cards work"


def test_mcp_search_history_climbs_the_ladder(config, store, root, monkeypatch):
    monkeypatch.setattr(mcp.os, "getcwd", lambda: str(root))
    cwd = str(root)
    task = _task(store, cwd, "pay", "p1", [f"{root}/src/checkout/step0.ts"])
    store.save_area(REPO, "checkout", aliases=["pago", "mongodb"], paths=["src/checkout"],
                    source="model")
    store.add_problem(cwd=cwd, title="Card declined", topic="checkout", symptom="bank said no",
                      first_seen=None, last_seen=None, task_ids=[task["id"]])
    store.add_problem(cwd=cwd, title="Slow build", topic="build", symptom="", first_seen=None,
                      last_seen=None, task_ids=[])

    def search(query):
        return mcp.call_tool(store, "search_history", {"query": query})

    assert "Card declined" in search("bank") and "Areas named" not in search("bank")
    text = search("problemas de pago")  # no record has these words; the alias names the area
    assert text.startswith('Areas named: checkout ("pago")') and "Card declined" in text
    assert "Card declined" in search("mongo")  # the start of an alias
    text = search("slow deploy")
    assert text.startswith("No record has every word") and "Slow build" in text
    text = search("xyzzy")
    assert "Areas with history: checkout (1 problem(s), 0 milestone(s)) aka pago" in text


def test_api_embeds_a_diagram_in_a_sandbox(api, store, root):
    _write(root, "docs/shop.html", "<html><script>1</script></html>")
    _task(store, str(root), "hi", "p0")
    architecture.scan(config=api.server.config, repo=REPO)
    status, body = api("POST", "/api/architecture/embed", {"repo": REPO, "path": "docs/shop.html"})
    assert status == 200 and body["url"].startswith("/diagram/")
    assert api("POST", "/api/architecture/embed", {"repo": REPO, "path": "src/a.ts"})[0] == 404
    conn = http.client.HTTPConnection("127.0.0.1", api.server.server_port)
    conn.request("GET", body["url"])  # no token: an iframe cannot send one
    resp = conn.getresponse()
    assert resp.status == 200 and resp.read() == b"<html><script>1</script></html>"
    csp = resp.getheader("Content-Security-Policy")
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    port = api.server.server_port
    assert f"frame-ancestors http://127.0.0.1:{port} http://localhost:{port}" in csp
    conn.request("GET", "/diagram/" + "x" * 32)
    resp = conn.getresponse()
    assert resp.status == 404
    resp.read()
    conn.close()


def test_api_quick_diagram_answers_errors_and_one_at_a_time(api, store, root, monkeypatch):
    _task(store, str(root), "hi", "p0")
    store.save_area(REPO, "checkout", paths=["src/checkout"], source="model")
    status, body = api("POST", "/api/architecture/quick", {"repo": REPO})
    assert status == 409 and "not installed" in body["error"]
    assert api("POST", "/api/architecture/quick", {"repo": "x/y"})[0] == 404
    drawn = {"json": "a.json", "html": "a.html", "areas": 1, "connections": 0, "dropped": 0,
             "imports": 0, "diagrams": 1}
    monkeypatch.setattr(quickdiagram, "draw", lambda config, repo: drawn)
    assert api("POST", "/api/architecture/quick", {"repo": REPO}) == (200, drawn)
    assert api.server.claim_quick(REPO)
    assert api("POST", "/api/architecture/quick", {"repo": REPO})[0] == 409
    api.server.release_quick(REPO)
