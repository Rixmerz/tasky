# ruff: noqa: F811 -- pytest fixtures imported from test_server are redefined as parameters
"""`/api/router`: the model router's settings over HTTP."""

import time

import pytest

from tests.test_server import (  # noqa: F401 -- fixtures are used by name
    _request,
    auth_headers,
    conn,
    running_server,
    token,
    web_dir,
)


@pytest.fixture(autouse=True)
def no_classifier(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKY_MM_CLASSIFIER", str(tmp_path / "missing.mjs"))
    monkeypatch.setenv("MM_LAYA_URL", "http://127.0.0.1:9")


def test_router_defaults_off_with_every_model_allowed(conn, auth_headers):
    resp, body = _request(conn, "GET", "/api/router", headers=auth_headers)
    assert resp.status == 200
    assert body == {
        "enabled": False,
        "models": {"fable": True, "opus": True, "sonnet": True, "haiku": True},
        "classifier": None,
        "laya": False,
    }


def test_router_requires_the_token(conn):
    resp, _ = _request(conn, "GET", "/api/router")
    assert resp.status == 401


def test_patch_switches_the_router_and_forbids_fable(conn, auth_headers):
    resp, body = _request(
        conn, "PATCH", "/api/router", body={"enabled": True, "models": {"fable": False}},
        headers=auth_headers,
    )
    assert resp.status == 200
    assert body["enabled"] is True and body["models"]["fable"] is False
    resp, again = _request(conn, "GET", "/api/router", headers=auth_headers)
    assert again["models"] == {"fable": False, "opus": True, "sonnet": True, "haiku": True}


@pytest.mark.parametrize(
    "payload",
    [
        {"models": {"gpt": True}},
        {"models": {"opus": "no"}},
        {"enabled": "yes"},
        {"other": 1},
        {},
    ],
)
def test_patch_rejects_invalid_settings_and_stores_nothing(conn, auth_headers, payload):
    resp, _ = _request(conn, "PATCH", "/api/router", body=payload, headers=auth_headers)
    assert resp.status == 400
    _, body = _request(conn, "GET", "/api/router", headers=auth_headers)
    assert body["enabled"] is False and all(body["models"].values())


# --- per-task route: the user's pin and the preview -------------------------------------------


def _queued(store, body="diseña la arquitectura"):
    return store.create_task(kind="prompt", body=body, status="queued", source="ui", cwd="/tmp")


@pytest.mark.parametrize(
    ("route", "pinned"),
    [
        ({"model": "opus", "effort": "xhigh"}, ("opus", "xhigh")),
        ({"model": "sonnet"}, ("sonnet", None)),
        ({"model": "haiku"}, ("haiku", None)),
        ("default", ("default", None)),
        (None, (None, None)),
    ],
)
def test_patch_route_pins_the_task(store, conn, auth_headers, route, pinned):
    task = _queued(store)
    resp, body = _request(
        conn, "PATCH", f"/api/tasks/{task['id']}", body={"route": route}, headers=auth_headers
    )
    assert resp.status == 200
    assert (body["pinned_model"], body["pinned_effort"]) == pinned


@pytest.mark.parametrize(
    "route",
    [
        {"model": "gpt"},
        {"model": "haiku", "effort": "low"},
        {"model": "opus", "effort": "extreme"},
        {"effort": "low"},
        {"model": "opus", "other": 1},
        {},
        "opus",
        "session",
        True,
    ],
)
def test_patch_route_rejects_invalid_values_and_stores_nothing(store, conn, auth_headers, route):
    task = _queued(store)
    store.update_task(task["id"], pinned_model="sonnet", pinned_effort="low")
    resp, _ = _request(
        conn, "PATCH", f"/api/tasks/{task['id']}", body={"route": route}, headers=auth_headers
    )
    assert resp.status == 400
    kept = store.get_task(task["id"])
    assert (kept["pinned_model"], kept["pinned_effort"]) == ("sonnet", "low")


def test_state_carries_router_settings_and_previews(store, conn, auth_headers):
    plain = _queued(store, "algo")
    pinned = _queued(store, "otra")
    store.update_task(pinned["id"], pinned_model="opus", pinned_effort="xhigh")
    done = store.create_task(kind="prompt", body="x", status="done", source="ui")

    _, state = _request(conn, "GET", "/api/state", headers=auth_headers)
    tasks = {t["id"]: t for t in state["tasks"]}
    assert state["router"]["enabled"] is False
    assert state["router"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert tasks[plain["id"]]["route_preview"] is None  # router off: nothing would change
    assert tasks[pinned["id"]]["route_preview"]["model"] == "opus"  # a pin shows regardless
    assert "route_preview" not in tasks[done["id"]]

    _request(conn, "PATCH", "/api/router", body={"enabled": True}, headers=auth_headers)
    _, state = _request(conn, "GET", "/api/state", headers=auth_headers)
    preview = {t["id"]: t for t in state["tasks"]}[plain["id"]]["route_preview"]
    # Not classified yet (or already cached as "classifier missing"): never classified inline.
    assert preview["model"] is None and preview["source"] in ("pending", "router")


def test_a_new_task_is_previewed_in_the_background(store, conn, auth_headers):
    _request(conn, "PATCH", "/api/router", body={"enabled": True}, headers=auth_headers)
    resp, task = _request(
        conn, "POST", "/api/tasks", body={"body": "algo", "cwd": "/tmp"}, headers=auth_headers
    )
    assert resp.status == 201
    deadline = time.monotonic() + 5
    while store.get_task(task["id"])["preview_key"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    cached = store.get_task(task["id"])
    assert cached["preview_kind"] is None and "not found" in cached["preview_note"]
    _, state = _request(conn, "GET", "/api/state", headers=auth_headers)
    preview = {t["id"]: t for t in state["tasks"]}[task["id"]]["route_preview"]
    assert preview["source"] == "router" and "not found" in preview["reason"]
