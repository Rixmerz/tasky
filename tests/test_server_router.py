# ruff: noqa: F811 -- pytest fixtures imported from test_server are redefined as parameters
"""`/api/router`: the model router's settings over HTTP."""

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
