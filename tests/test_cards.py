"""Compact dashboard: synced tasks grouped into issue-tracker cards by Haiku, checked."""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from tasky import cards, cli, repos
from tasky.store import Store

REPO = "github.com/o/shop"


@pytest.fixture
def shop(tmp_path, monkeypatch):
    root = tmp_path / "shop"
    root.mkdir()
    monkeypatch.setattr(
        repos, "resolve",
        lambda cwd: (REPO, "shop") if cwd.startswith(str(root)) else (f"path:{cwd}", cwd),
    )
    return root


def _task(store: Store, root, body: str, prompt_id: str, files=(), day="2026-09-01"):
    task = store.create_task(kind="prompt", body=body, status="done", source="hook",
                             cwd=str(root), prompt_id=prompt_id, result=f"did {body}",
                             finished_at=f"{day}T10:00:00.000Z")
    store.add_messages([
        {"uuid": f"{prompt_id}-{i}", "session_id": "s1", "prompt_id": prompt_id,
         "role": "assistant", "kind": "tool_use", "tool_name": "Edit",
         "file_path": f"{root}/{path}"}
        for i, path in enumerate(files)
    ])
    repos.ensure(store, [str(root)])
    return task


def _synced(store: Store, root) -> None:
    for cwd in store.repo_cwds(REPO):
        store.advance_history_cursor(cwd, 10**9)


class FakeHaiku:
    def __init__(self, *outputs, cost=0.03):
        self.outputs, self.cost, self.calls = list(outputs), cost, []

    def __call__(self, cmd, *, input, env, **kwargs):
        self.calls.append({"cmd": cmd, "input": input})
        output = self.outputs.pop(0) if self.outputs else {"cards": []}
        reply = {"is_error": False, "total_cost_usd": self.cost, "structured_output": output}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(reply), stderr="")


def test_compact_checks_everything_haiku_returns(config, store, shop):
    t1 = _task(store, shop, "add coupons; a coupon must not apply twice", "p1",
               ["src/checkout/coupon.ts", "src/checkout/cart.ts"])
    t2 = _task(store, shop, "fix coupon rounding", "p2", ["src/checkout/coupon.ts"],
               day="2026-09-03")
    t3 = _task(store, shop, "what is our ci?", "p3")
    store.save_area(REPO, "checkout", aliases=["pago"], paths=["src/checkout"], source="model")
    problem = store.add_problem(cwd=str(shop), title="Rounding", symptom="", first_seen=None,
                                last_seen=None, task_ids=[t2["id"]])
    _synced(store, shop)
    fake = FakeHaiku({"cards": [
        {"title": "Coupons at checkout", "kind": "story", "status": "done", "area": "Pago",
         "objective": "Let shoppers use coupons.", "description": "Coupons in the cart.",
         "task_ids": [t1["id"], t2["id"], 999], "problem_ids": [problem, 12345],
         "commits": ["abc1234"],
         "criteria": [
             {"text": "A coupon applies once", "quote": "a coupon must not apply twice"},
             {"text": "Totals round to cents", "quote": "never said this"},
             {"text": "Invented without a quote"},
         ]},
        {"title": "Steals a placed task", "task_ids": [t1["id"]]},  # already placed: dropped
        {"title": "No tasks at all", "task_ids": [999]},
    ]})
    summary = cards.compact(config, REPO, run=fake)
    assert summary["error"] is None and summary["model"] == "haiku"
    assert (summary["added"], summary["left_out"], summary["pending"]) == (1, 1, 0)
    cmd = fake.calls[0]["cmd"]
    assert cmd[cmd.index("--model") + 1] == "haiku" and "--effort" not in cmd
    prompt = fake.calls[0]["input"]
    assert "<edited>src/checkout/cart.ts, src/checkout/coupon.ts</edited>" in prompt
    assert 'areas="checkout"' in prompt and f"#{problem} [open] Rounding" in prompt
    [card] = store.cards(REPO)
    assert card["area"] == "checkout" and card["task_ids"] == [t1["id"], t2["id"]]
    assert card["problem_ids"] == [problem] and card["commits"] == []  # no git log shown
    assert (card["first_on"], card["last_on"]) == ("2026-09-01", "2026-09-03")
    assert card["criteria"] == [
        {"text": "A coupon applies once", "source": "stated",
         "quote": "a coupon must not apply twice"},
        {"text": "Totals round to cents", "source": "inferred"},
        {"text": "Invented without a quote", "source": "inferred"},
    ]
    [viewed] = cards.view(store, REPO)
    assert viewed["files"] == ["src/checkout/coupon.ts", "src/checkout/cart.ts"]
    assert [t["id"] for t in viewed["tasks"]] == [t1["id"], t2["id"]]
    assert viewed["problems"][0]["title"] == "Rounding"
    assert t3["id"] not in {i for c in store.cards(REPO) for i in c["task_ids"]}


def test_later_runs_extend_cards_of_this_repo_only(config, store, shop, tmp_path):
    t1 = _task(store, shop, "coupons", "p1")
    _synced(store, shop)
    cards.compact(config, REPO, run=FakeHaiku({"cards": [
        {"title": "Coupons", "status": "in_progress", "task_ids": [t1["id"]],
         "criteria": [{"text": "Applies once"}]}]}))
    [card] = store.cards(REPO)
    other = store.add_card("github.com/o/other", title="Elsewhere", task_ids=[])
    t2 = _task(store, shop, "finish coupons", "p2", day="2026-09-05")
    _synced(store, shop)
    fake = FakeHaiku({"cards": [
        {"existing_id": card["id"], "status": "done", "task_ids": [t2["id"]],
         "criteria": [{"text": "Applies once"}, {"text": "Shows the discount"}]},
        {"existing_id": other, "title": "Hijack", "task_ids": [t2["id"]]},
    ]})
    summary = cards.compact(config, REPO, run=fake)
    assert (summary["updated"], summary["added"]) == (1, 0)
    assert f'"existing_id": {card["id"]}' in fake.calls[0]["input"]
    assert f'"existing_id": {other}' not in fake.calls[0]["input"]
    card = store.get_card(card["id"])
    assert card["status"] == "done" and card["task_ids"] == [t1["id"], t2["id"]]
    assert [c["text"] for c in card["criteria"]] == ["Applies once", "Shows the discount"]
    assert card["last_on"] == "2026-09-05"
    assert store.get_card(other)["task_ids"] == []


def test_compact_waits_for_the_history_sync(config, store, shop):
    _task(store, shop, "coupons", "p1")
    with pytest.raises(cards.CardsError, match="sync the history first: 1 task"):
        cards.compact(config, REPO, run=FakeHaiku())
    _synced(store, shop)
    store.begin_history_sync(REPO, "sonnet", stale_after_s=60)
    with pytest.raises(cards.CardsError, match="syncing"):
        cards.compact(config, REPO, run=FakeHaiku())
    store.finish_history_sync(REPO, cost_usd=0, error=None)
    store.begin_card_run(REPO, stale_after_s=60)
    with pytest.raises(cards.CardsError, match="already being made"):
        cards.compact(config, REPO, run=FakeHaiku())


def test_a_failed_call_keeps_the_cursor_and_records_the_error(config, store, shop):
    _task(store, shop, "coupons", "p1")
    _synced(store, shop)

    def broken(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    summary = cards.compact(config, REPO, run=broken)
    assert "boom" in summary["error"] and summary["pending"] == 1
    run = store.card_run(REPO)
    assert run["state"] == "idle" and "boom" in run["error"]


def test_cli_compact(config, store, shop, monkeypatch):
    _task(store, shop, "coupons", "p1")
    out = io.StringIO()
    assert cli.main(["compact", "--repo", REPO], out=out) == 1  # not synced
    _synced(store, shop)
    monkeypatch.setattr(cards.history, "call_model", lambda *a, **k: ({"cards": [
        {"title": "Coupons", "task_ids": [1]}]}, 0.02))
    out = io.StringIO()
    assert cli.main(["compact", "--cwd", str(shop)], out=out) == 0
    assert "1 card(s) added" in out.getvalue() and "$0.0200" in out.getvalue()


def test_api_compact_and_remove(config, store, shop, tmp_path):
    import http.client
    import threading

    from tasky.config import load_token
    from tasky.server import make_server

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html></html>")
    token = load_token(config)
    srv = make_server(config, port=0, web_dir=web, token=token)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    launched = []
    srv.popen = lambda cmd, **kwargs: launched.append(cmd)
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port)

    def call(method, path, body=None):
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else b"",
                     headers={"X-Tasky-Token": token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"null")

    try:
        _task(store, shop, "coupons", "p1")
        status, body = call("POST", "/api/cards/compact", {"repo": REPO})
        assert status == 409 and "sync the history first" in body["error"]
        _synced(store, shop)
        status, body = call("POST", "/api/cards/compact", {"repo": REPO})
        assert status == 202 and body["pending"] == 1 and launched[-1][-3:] == [
            "compact", "--repo", REPO]
        assert call("POST", "/api/cards/compact", {"repo": "x/y"})[0] == 404
        store.advance_card_cursor(REPO, 10**9)
        assert call("POST", "/api/cards/compact", {"repo": REPO})[0] == 409  # nothing pending
        card = store.add_card(REPO, title="Coupons", task_ids=[1], first_on="2026-09-01",
                              last_on="2026-09-01")
        status, body = call("GET", f"/api/history?repo={REPO}")
        entry = next(r for r in body["repos"] if r["repo"] == REPO)
        assert entry["cards"]["pending"] == 0 and entry["cards"]["history_pending"] == 0
        assert body["cards"][0]["title"] == "Coupons" and body["cards_model"] == "haiku"
        assert call("DELETE", f"/api/cards/{card}") == (200, {"deleted": True})
        assert call("DELETE", f"/api/cards/{card}")[0] == 404
    finally:
        conn.close()
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()
