import http.client
import json
import stat
import threading

import pytest

import tasky.server as server_mod
from tasky.config import load_token
from tasky.server import make_server


@pytest.fixture
def web_dir(tmp_path):
    d = tmp_path / "web"
    d.mkdir()
    (d / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    (d / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (d / "app.css").write_text("body{}", encoding="utf-8")
    return d


@pytest.fixture
def token(config):
    return load_token(config)


@pytest.fixture
def running_server(config, web_dir, token):
    srv = make_server(config, port=0, web_dir=web_dir, token=token)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


@pytest.fixture
def conn(running_server):
    connection = http.client.HTTPConnection("127.0.0.1", running_server.server_port)
    yield connection
    connection.close()


@pytest.fixture
def auth_headers(token):
    return {"X-Tasky-Token": token, "Content-Type": "application/json"}


def _request(conn, method, path, body=None, headers=None):
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    hdrs = dict(headers or {})
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    parsed = json.loads(data) if data else None
    return resp, parsed


def _request_raw(conn, method, path, headers=None):
    conn.request(method, path, headers=dict(headers or {}))
    resp = conn.getresponse()
    return resp, resp.read()


# -- localhost protections ---------------------------------------------------


def test_rejects_unknown_host_header(conn, token):
    resp, parsed = _request(
        conn,
        "GET",
        "/api/version",
        headers={"Host": "evil.example:7733", "X-Tasky-Token": token},
    )
    assert resp.status == 403
    assert "error" in parsed


def test_accepts_localhost_host_header(running_server, conn, token):
    resp, parsed = _request(
        conn,
        "GET",
        "/api/version",
        headers={"Host": f"localhost:{running_server.server_port}", "X-Tasky-Token": token},
    )
    assert resp.status == 200
    assert "rev" in parsed


def test_options_always_rejected(conn):
    resp, _ = _request(conn, "OPTIONS", "/api/tasks")
    assert resp.status == 403


def test_no_cors_headers_ever(conn, token):
    resp, _ = _request(conn, "GET", "/api/version", headers={"X-Tasky-Token": token})
    assert resp.status == 200
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_security_headers_present(conn, token):
    resp, _ = _request(conn, "GET", "/api/version", headers={"X-Tasky-Token": token})
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert "default-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("Cache-Control") == "no-store"


# -- token auth ----------------------------------------------------------------


API_GET_ROUTES = ["/api/version", "/api/state", "/api/search?q=x"]


@pytest.mark.parametrize("path", API_GET_ROUTES)
def test_get_api_route_without_token_401(conn, path):
    resp, parsed = _request(conn, "GET", path)
    assert resp.status == 401
    assert "error" in parsed


@pytest.mark.parametrize("path", API_GET_ROUTES)
def test_get_api_route_with_wrong_token_401(conn, path):
    resp, parsed = _request(conn, "GET", path, headers={"X-Tasky-Token": "wrong-token"})
    assert resp.status == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/tasks"),
        ("POST", "/api/import"),
        ("PATCH", "/api/sessions/whatever"),
    ],
)
def test_mutation_route_with_wrong_token_401(conn, method, path):
    resp, parsed = _request(
        conn,
        method,
        path,
        body={},
        headers={"X-Tasky-Token": "wrong-token", "Content-Type": "application/json"},
    )
    assert resp.status == 401


def test_post_api_route_without_token_401_and_no_task_created(store, conn):
    resp, parsed = _request(
        conn,
        "POST",
        "/api/tasks",
        body={"body": "x", "cwd": "/tmp"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 401
    assert store.list_tasks() == []


def test_patch_task_route_without_token_401(store, conn, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "PATCH",
        f"/api/tasks/{task['id']}",
        body={"title": "renamed"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 401
    assert store.get_task(task["id"])["title"] != "renamed"


def test_delete_task_route_without_token_401(store, conn, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, _ = _request(
        conn, "DELETE", f"/api/tasks/{task['id']}", headers={"Content-Type": "application/json"}
    )
    assert resp.status == 401
    assert store.get_task(task["id"]) is not None


def test_run_task_route_without_token_401(store, conn, tmp_path, monkeypatch):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    started = []
    monkeypatch.setattr(server_mod.worker, "run_task", lambda *a, **k: started.append(1))

    resp, _ = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/run",
        body={},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 401
    assert started == []


def test_patch_session_route_without_token_401(store, conn):
    store.upsert_session("sess-auth", cwd="/tmp")
    resp, _ = _request(
        conn,
        "PATCH",
        "/api/sessions/sess-auth",
        body={"title": "x"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 401


def test_import_route_without_token_401(conn):
    resp, _ = _request(
        conn, "POST", "/api/import", body={}, headers={"Content-Type": "application/json"}
    )
    assert resp.status == 401


def test_static_routes_are_public(conn):
    resp, body = _request_raw(conn, "GET", "/")
    assert resp.status == 200
    resp_js, _ = _request_raw(conn, "GET", "/app.js")
    assert resp_js.status == 200
    resp_css, _ = _request_raw(conn, "GET", "/app.css")
    assert resp_css.status == 200


def test_mutation_wrong_content_type_rejected(conn, token, monkeypatch):
    started = []
    monkeypatch.setattr(server_mod.worker, "run_task", lambda *a, **k: started.append(1))

    resp, parsed = _request(
        conn,
        "POST",
        "/api/tasks",
        body={"body": "x", "cwd": "/tmp"},
        headers={"X-Tasky-Token": token, "Content-Type": "text/plain"},
    )
    assert resp.status == 403
    assert started == []


def test_mutation_content_type_with_charset_accepted(conn, auth_headers, tmp_path):
    headers = dict(auth_headers)
    headers["Content-Type"] = "application/json; charset=utf-8"
    resp, parsed = _request(
        conn, "POST", "/api/tasks", body={"body": "x", "cwd": str(tmp_path)}, headers=headers
    )
    assert resp.status == 201


def test_mutation_content_type_lookalike_rejected(conn, auth_headers, tmp_path):
    headers = dict(auth_headers)
    headers["Content-Type"] = "application/jsonfoo"
    resp, parsed = _request(
        conn, "POST", "/api/tasks", body={"body": "x", "cwd": str(tmp_path)}, headers=headers
    )
    assert resp.status == 403


def test_body_too_large_rejected(conn, token):
    conn.putrequest("POST", "/api/tasks")
    conn.putheader("X-Tasky-Token", token)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(2 * 1024 * 1024))
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    assert resp.status == 413
    assert b"error" in body


def test_negative_content_length_rejected(conn, token):
    conn.putrequest("POST", "/api/tasks")
    conn.putheader("X-Tasky-Token", token)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "-1")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400


def test_non_integer_content_length_rejected(conn, token):
    conn.putrequest("POST", "/api/tasks")
    conn.putheader("X-Tasky-Token", token)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400


def test_missing_content_length_rejected(conn, token):
    conn.putrequest("POST", "/api/tasks")
    conn.putheader("X-Tasky-Token", token)
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400


def test_non_ascii_token_header_is_401_not_dropped_connection(conn):
    # http.client encodes a str header as latin-1; a non-ASCII token must not
    # crash hmac.compare_digest (which rejects non-ASCII str input) and take
    # the connection down with it.
    resp, parsed = _request(conn, "GET", "/api/version", headers={"X-Tasky-Token": "h\xe9llo"})
    assert resp.status == 401
    assert "error" in parsed


def test_malformed_utf8_body_is_400_not_500(conn, token):
    conn.putrequest("POST", "/api/tasks")
    conn.putheader("X-Tasky-Token", token)
    conn.putheader("Content-Type", "application/json")
    payload = b"\xff\xff"
    conn.putheader("Content-Length", str(len(payload)))
    conn.endheaders()
    conn.send(payload)
    resp = conn.getresponse()
    assert resp.status == 400


# -- static files -------------------------------------------------------------


def test_static_index(conn):
    resp, body = _request_raw(conn, "GET", "/")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    assert resp.getheader("Cache-Control") == "no-store"
    assert body == b"<html>hi</html>"


def test_static_js_and_css(conn):
    resp_js, body_js = _request_raw(conn, "GET", "/app.js")
    assert resp_js.status == 200
    assert resp_js.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert body_js == b"console.log('hi')"

    resp_css, body_css = _request_raw(conn, "GET", "/app.css")
    assert resp_css.status == 200
    assert resp_css.getheader("Content-Type") == "text/css; charset=utf-8"
    assert body_css == b"body{}"


def test_static_digest_module(running_server, conn):
    (running_server.web_dir / "digest.js").write_text("export const x = 1;")
    resp, body = _request_raw(conn, "GET", "/digest.js")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert body == b"export const x = 1;"


def test_static_missing_file_404(conn):
    # web_dir has all three files; ask for a path that's not registered
    resp, parsed = _request(conn, "GET", "/missing.png")
    assert resp.status == 404


def test_static_missing_on_disk_404(running_server):
    (running_server.web_dir / "app.css").unlink()
    connection = http.client.HTTPConnection("127.0.0.1", running_server.server_port)
    resp, parsed = _request(connection, "GET", "/app.css")
    assert resp.status == 404
    connection.close()


# -- /api/version and /api/state ----------------------------------------------


def test_version_reports_rev(store, conn, auth_headers):
    store.create_task(kind="prompt", body="hi", status="queued", source="ui", cwd="/tmp")
    resp, parsed = _request(conn, "GET", "/api/version", headers=auth_headers)
    assert resp.status == 200
    assert parsed["rev"] >= 1


def test_state_shape(store, config, conn, auth_headers):
    store.create_task(kind="prompt", body="hi", status="queued", source="ui", cwd="/tmp")
    resp, parsed = _request(conn, "GET", "/api/state", headers=auth_headers)
    assert resp.status == 200
    assert set(parsed) >= {"rev", "sessions", "tasks", "config"}
    assert parsed["config"] == {
        "queue_prefix": config.queue_prefix,
        "max_chain": config.max_chain,
        "port": config.port,
        "allow_bypass": config.allow_bypass,
    }


# -- POST /api/tasks -----------------------------------------------------------


def test_create_task(conn, auth_headers, tmp_path):
    resp, parsed = _request(
        conn,
        "POST",
        "/api/tasks",
        body={"body": "document the API", "cwd": str(tmp_path)},
        headers=auth_headers,
    )
    assert resp.status == 201
    assert parsed["status"] == "queued"
    assert parsed["source"] == "ui"
    assert parsed["kind"] == "prompt"
    assert parsed["body"] == "document the API"


def test_create_task_requires_body(conn, auth_headers, tmp_path):
    resp, parsed = _request(
        conn,
        "POST",
        "/api/tasks",
        body={"body": "   ", "cwd": str(tmp_path)},
        headers=auth_headers,
    )
    assert resp.status == 400
    assert "error" in parsed


def test_create_task_requires_cwd(conn, auth_headers):
    resp, parsed = _request(
        conn, "POST", "/api/tasks", body={"body": "x"}, headers=auth_headers
    )
    assert resp.status == 400


# -- PATCH /api/tasks/<id> ------------------------------------------------------


def test_patch_task_status(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "PATCH",
        f"/api/tasks/{task['id']}",
        body={"title": "renamed"},
        headers=auth_headers,
    )
    assert resp.status == 200
    assert parsed["title"] == "renamed"


def test_patch_task_invalid_status(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "PATCH",
        f"/api/tasks/{task['id']}",
        body={"status": "maybe"},
        headers=auth_headers,
    )
    assert resp.status == 400
    unchanged = store.get_task(task["id"])
    assert unchanged["status"] == "queued"


def test_patch_task_unknown_field(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "PATCH",
        f"/api/tasks/{task['id']}",
        body={"nope": 1},
        headers=auth_headers,
    )
    assert resp.status == 400


def test_patch_task_not_found(conn, auth_headers):
    resp, parsed = _request(
        conn, "PATCH", "/api/tasks/999999", body={"title": "x"}, headers=auth_headers
    )
    assert resp.status == 404


def test_patch_task_oversized_id_404(conn, auth_headers):
    resp, parsed = _request(
        conn,
        "PATCH",
        "/api/tasks/99999999999999999999999",
        body={"title": "x"},
        headers=auth_headers,
    )
    assert resp.status == 404


def test_patch_task_reorder(store, conn, auth_headers, tmp_path):
    t1 = store.create_task(
        kind="prompt", body="a", status="queued", source="ui", cwd=str(tmp_path)
    )
    t2 = store.create_task(
        kind="prompt", body="b", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "PATCH",
        f"/api/tasks/{t2['id']}",
        body={"before_id": t1["id"]},
        headers=auth_headers,
    )
    assert resp.status == 200
    tasks = store.list_tasks(status="queued")
    assert [t["id"] for t in tasks] == [t2["id"], t1["id"]]


def test_patch_task_reorder_unknown_before_id(store, conn, auth_headers, tmp_path):
    t1 = store.create_task(
        kind="prompt", body="a", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "PATCH",
        f"/api/tasks/{t1['id']}",
        body={"before_id": 999999},
        headers=auth_headers,
    )
    assert resp.status == 400


# -- DELETE /api/tasks/<id> -----------------------------------------------------


def test_delete_task(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn, "DELETE", f"/api/tasks/{task['id']}", headers=auth_headers
    )
    assert resp.status == 200
    assert parsed == {"deleted": True}
    assert store.get_task(task["id"]) is None


def test_delete_task_not_found(conn, auth_headers):
    resp, parsed = _request(conn, "DELETE", "/api/tasks/999999", headers=auth_headers)
    assert resp.status == 404


# -- POST /api/tasks/<id>/run ---------------------------------------------------


def test_run_task_calls_worker(store, conn, auth_headers, tmp_path, monkeypatch):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    calls = []

    def fake_run_task(store_arg, config_arg, task_id, permission_mode="default", **kw):
        calls.append((task_id, permission_mode))
        return store_arg.update_task(task_id, status="running", session_id="s1")

    monkeypatch.setattr(server_mod.worker, "run_task", fake_run_task)

    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/run",
        body={"permission_mode": "acceptEdits"},
        headers=auth_headers,
    )
    assert resp.status == 200
    assert parsed["status"] == "running"
    assert calls == [(task["id"], "acceptEdits")]


def test_run_task_not_queued_409(store, conn, auth_headers, tmp_path, monkeypatch):
    task = store.create_task(
        kind="prompt", body="x", status="running", source="ui", cwd=str(tmp_path)
    )
    calls = []

    def raising(*a, **k):
        from tasky import worker

        calls.append(1)
        raise worker.TaskNotQueued(f"task {task['id']} is not queued")

    monkeypatch.setattr(server_mod.worker, "run_task", raising)

    resp, parsed = _request(
        conn, "POST", f"/api/tasks/{task['id']}/run", body={}, headers=auth_headers
    )
    assert resp.status == 409
    assert calls == [1]


def test_run_task_worker_error_400(store, conn, auth_headers, tmp_path, monkeypatch):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )

    def raising(*a, **k):
        from tasky.worker import WorkerError

        raise WorkerError("bad mode")

    monkeypatch.setattr(server_mod.worker, "run_task", raising)

    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/run",
        body={"permission_mode": "yolo"},
        headers=auth_headers,
    )
    assert resp.status == 400


def test_run_task_not_found(conn, auth_headers):
    resp, parsed = _request(
        conn, "POST", "/api/tasks/999999/run", body={}, headers=auth_headers
    )
    assert resp.status == 404


def test_run_task_concurrent_eight_requests_one_launch_seven_conflicts(
    store, running_server, auth_headers, tmp_path, monkeypatch
):
    """Real concurrency: 8 threads POST /run at one queued task with a fake popen."""
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )

    calls = []
    lock = threading.Lock()

    def fake_popen(*args, **kwargs):
        with lock:
            calls.append((args, kwargs))
        return object()

    # The server launches every worker through its own `popen` attribute
    # (never subprocess.Popen directly), so tests replace that instead of a
    # real process ever spawning.
    monkeypatch.setattr(running_server, "popen", fake_popen)

    statuses = []
    statuses_lock = threading.Lock()

    def fire():
        connection = http.client.HTTPConnection("127.0.0.1", running_server.server_port)
        resp, _ = _request(
            connection, "POST", f"/api/tasks/{task['id']}/run", body={}, headers=auth_headers
        )
        with statuses_lock:
            statuses.append(resp.status)
        connection.close()

    threads = [threading.Thread(target=fire) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses) == [200] + [409] * 7
    assert len(calls) == 1


# -- PATCH /api/sessions/<id> ----------------------------------------------------


def test_patch_session_auto_pull(store, conn, auth_headers):
    store.upsert_session("sess-1", cwd="/tmp")
    resp, parsed = _request(
        conn,
        "PATCH",
        "/api/sessions/sess-1",
        body={"auto_pull": True},
        headers=auth_headers,
    )
    assert resp.status == 200
    assert parsed["auto_pull"] == 1


def test_patch_session_not_found(conn, auth_headers):
    resp, parsed = _request(
        conn, "PATCH", "/api/sessions/nope", body={"title": "x"}, headers=auth_headers
    )
    assert resp.status == 404


def test_patch_session_unknown_field(store, conn, auth_headers):
    store.upsert_session("sess-2", cwd="/tmp")
    resp, parsed = _request(
        conn,
        "PATCH",
        "/api/sessions/sess-2",
        body={"nope": 1},
        headers=auth_headers,
    )
    assert resp.status == 400


# -- POST /api/import -------------------------------------------------------------


def test_import_calls_importer(conn, auth_headers, monkeypatch):
    import sys
    import types

    fake_report = {"files": 0, "sessions": 0, "tasks": 0, "skipped_sessions": 0, "bad_lines": 0}
    fake_module = types.ModuleType("tasky.importer")
    fake_module.import_transcripts = lambda store, config, **kw: fake_report
    monkeypatch.setitem(sys.modules, "tasky.importer", fake_module)

    resp, parsed = _request(conn, "POST", "/api/import", body={}, headers=auth_headers)
    assert resp.status == 200
    assert parsed == fake_report


# -- unexpected handler errors --------------------------------------------------


def test_unexpected_handler_exception_returns_500_without_traceback(
    conn, auth_headers, monkeypatch
):
    def boom(self):
        raise RuntimeError("boom: something internal broke")

    monkeypatch.setattr(server_mod._Handler, "_route_version", boom)

    resp, parsed = _request(conn, "GET", "/api/version", headers=auth_headers)
    assert resp.status == 500
    assert "error" in parsed
    assert "boom" not in json.dumps(parsed)
    assert "Traceback" not in json.dumps(parsed)


# -- serve() --------------------------------------------------------------------


def test_serve_prints_tokenized_url_and_returns_0(config, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(server_mod.webbrowser, "open", lambda url: opened.append(url))

    def fake_serve_forever(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        server_mod.ThreadingHTTPServer, "serve_forever", fake_serve_forever, raising=True
    )

    result = server_mod.serve(config, port=0, open_browser=True)
    assert result == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:" in out
    assert "#token=" in out
    expected_token = load_token(config)
    assert f"#token={expected_token}" in out
    # --open must never put the token on a command line (CWE-214): the
    # browser gets a tokenless file:// redirect page instead.
    assert len(opened) == 1
    assert opened[0].startswith("file://")
    assert "#token=" not in opened[0]
    assert expected_token not in opened[0]
    page = config.log_dir / "open.html"
    assert page.exists()
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    assert f"#token={expected_token}" in page.read_text(encoding="utf-8")


# -- rotation (N-token) -------------------------------------------------------


def test_token_rotation_is_picked_up_without_restart(config, web_dir):
    srv = make_server(config, port=0, web_dir=web_dir)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        old_token = load_token(config)
        connection = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        resp, parsed = _request(
            connection, "GET", "/api/version", headers={"X-Tasky-Token": old_token}
        )
        assert resp.status == 200
        connection.close()

        config.token_path.unlink()
        new_token = load_token(config)
        assert new_token != old_token

        connection = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        resp, parsed = _request(
            connection, "GET", "/api/version", headers={"X-Tasky-Token": new_token}
        )
        assert resp.status == 200
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        resp, parsed = _request(
            connection, "GET", "/api/version", headers={"X-Tasky-Token": old_token}
        )
        assert resp.status == 401
        connection.close()
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


def test_token_rotation_with_missing_file_gives_401_not_500(config, web_dir):
    srv = make_server(config, port=0, web_dir=web_dir)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        token = load_token(config)
        config.token_path.unlink()

        connection = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        resp, parsed = _request(
            connection, "GET", "/api/version", headers={"X-Tasky-Token": token}
        )
        assert resp.status == 401
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        resp, parsed = _request(
            connection, "GET", "/api/version", headers={"X-Tasky-Nonce": "a" * 16}
        )
        assert resp.status == 401
        connection.close()
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


# -- /api/version proof-only handshake (R1) ------------------------------------


def test_version_nonce_only_returns_proof_and_no_rev(conn, token):
    import hmac as hmac_mod

    from tasky.config import token_proof

    nonce = "a" * 16
    resp, parsed = _request(conn, "GET", "/api/version", headers={"X-Tasky-Nonce": nonce})
    assert resp.status == 200
    assert "proof" in parsed
    assert "rev" not in parsed
    running_server_port = conn.port
    expected = token_proof(token, nonce, running_server_port)
    assert hmac_mod.compare_digest(parsed["proof"], expected)


def test_version_nonce_only_without_valid_nonce_is_401(conn):
    resp, parsed = _request(conn, "GET", "/api/version", headers={"X-Tasky-Nonce": "short"})
    assert resp.status == 401


def test_version_with_token_and_nonce_returns_rev_and_proof(conn, token):
    resp, parsed = _request(
        conn, "GET", "/api/version", headers={"X-Tasky-Token": token, "X-Tasky-Nonce": "b" * 16}
    )
    assert resp.status == 200
    assert "rev" in parsed
    assert "proof" in parsed


def test_version_proof_bound_to_port_rejected_on_a_different_port(conn, token):
    from tasky.config import token_proof

    nonce = "c" * 16
    resp, parsed = _request(conn, "GET", "/api/version", headers={"X-Tasky-Nonce": nonce})
    assert resp.status == 200
    wrong_port_proof = token_proof(token, nonce, conn.port + 1)
    assert wrong_port_proof != parsed["proof"]


# -- R5: slow/bogus requests ----------------------------------------------------


def test_post_to_non_api_path_with_large_content_length_is_404_body_unread(conn):
    conn.putrequest("POST", "/")
    conn.putheader("Content-Length", str(5 * 1024 * 1024))
    conn.endheaders()
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 404


def test_content_length_with_underscore_rejected(conn, token):
    conn.putrequest("POST", "/api/tasks")
    conn.putheader("X-Tasky-Token", token)
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "1_0")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400


def test_stalled_client_does_not_block_a_second_request(
    config, web_dir, token, monkeypatch
):
    import socket
    import time

    monkeypatch.setattr(server_mod._Handler, "timeout", 0.3)
    srv = make_server(config, port=0, web_dir=web_dir, token=token)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        stalled = socket.create_connection(("127.0.0.1", srv.server_port), timeout=5)
        request_head = (
            "POST /api/tasks HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            f"X-Tasky-Token: {token}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 100\r\n\r\n"
            "0123456789"
        )
        stalled.sendall(request_head.encode("ascii"))

        deadline = time.monotonic() + 5
        second_ok = False
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=2)
            try:
                resp, parsed = _request(connection, "GET", "/api/version", headers={})
                if resp.status in (200, 401):
                    second_ok = True
                    break
            except OSError:
                pass
            finally:
                connection.close()
            time.sleep(0.05)
        assert second_ok
        stalled.close()
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


# -- /api/state lanes (design.md decision 5) -----------------------------------


def test_state_includes_lanes(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    store.enqueue_task(task["id"], "default")
    store.set_lane(str(tmp_path), paused=True, reason="task #1 failed")

    resp, parsed = _request(conn, "GET", "/api/state", headers=auth_headers)
    assert resp.status == 200
    assert {"cwd": str(tmp_path), "paused": 1, "reason": "task #1 failed"} in parsed["lanes"]


def test_state_tasks_include_lane_fields(store, conn, auth_headers, tmp_path):
    store.create_task(kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path))
    resp, parsed = _request(conn, "GET", "/api/state", headers=auth_headers)
    assert resp.status == 200
    task = parsed["tasks"][0]
    assert {"lane", "run_mode", "permission_mode", "fork_of"} <= set(task)


# -- POST /api/tasks/<id>/run mode (design.md decision 5) -----------------------


def test_run_task_passes_mode_to_worker(store, conn, auth_headers, tmp_path, monkeypatch):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    calls = []

    def fake_run_task(store_arg, config_arg, task_id, permission_mode="default", mode="now", **kw):
        calls.append((task_id, permission_mode, mode))
        return store_arg.update_task(task_id, status="running", session_id="s1")

    monkeypatch.setattr(server_mod.worker, "run_task", fake_run_task)

    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/run",
        body={"mode": "fork"},
        headers=auth_headers,
    )
    assert resp.status == 200
    assert calls == [(task["id"], "default", "fork")]


def test_run_task_invalid_mode_400(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/run",
        body={"mode": "later"},
        headers=auth_headers,
    )
    assert resp.status == 400
    assert store.get_task(task["id"])["status"] == "queued"


# -- POST /api/tasks/<id>/enqueue ------------------------------------------------


def test_enqueue_task_moves_to_lane_and_kicks(store, running_server, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    running_server.popen = fake_popen
    conn = http.client.HTTPConnection("127.0.0.1", running_server.server_port)
    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/enqueue",
        body={"permission_mode": "acceptEdits"},
        headers=auth_headers,
    )
    conn.close()
    assert resp.status == 200
    assert parsed["lane"] == "serial"
    assert parsed["status"] == "running"  # kicked immediately, an idle lane
    assert len(calls) == 1


def test_enqueue_task_before_id(store, conn, auth_headers, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        server_mod.scheduler, "kick", lambda *a, **k: called.append(1) or []
    )
    task_a = store.create_task(
        kind="prompt", body="a", status="queued", source="ui", cwd=str(tmp_path)
    )
    task_b = store.create_task(
        kind="prompt", body="b", status="queued", source="ui", cwd=str(tmp_path)
    )
    _request(
        conn,
        "POST",
        f"/api/tasks/{task_a['id']}/enqueue",
        body={},
        headers=auth_headers,
    )
    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task_b['id']}/enqueue",
        body={"before_id": task_a["id"]},
        headers=auth_headers,
    )
    assert resp.status == 200
    queued = [
        t["id"] for t in store.list_tasks(status="queued") if t["lane"] == "serial"
    ]
    assert queued == [task_b["id"], task_a["id"]]


def test_enqueue_task_invalid_permission_mode_400(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/enqueue",
        body={"permission_mode": "yolo"},
        headers=auth_headers,
    )
    assert resp.status == 400
    assert store.get_task(task["id"])["lane"] is None


def test_enqueue_task_bypass_permissions_refused_without_allow_bypass(
    store, conn, auth_headers, tmp_path
):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/enqueue",
        body={"permission_mode": "bypassPermissions"},
        headers=auth_headers,
    )
    assert resp.status == 400
    assert store.get_task(task["id"])["lane"] is None


def test_enqueue_task_requires_cwd(store, conn, auth_headers):
    task = store.create_task(kind="prompt", body="x", status="queued", source="ui")
    resp, parsed = _request(
        conn, "POST", f"/api/tasks/{task['id']}/enqueue", body={}, headers=auth_headers
    )
    assert resp.status == 400


def test_enqueue_task_not_found(conn, auth_headers):
    resp, parsed = _request(
        conn, "POST", "/api/tasks/999999/enqueue", body={}, headers=auth_headers
    )
    assert resp.status == 404


def test_enqueue_task_not_queued_409(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="running", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn, "POST", f"/api/tasks/{task['id']}/enqueue", body={}, headers=auth_headers
    )
    assert resp.status == 409


def test_enqueue_task_unknown_before_id_400(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/enqueue",
        body={"before_id": 999999},
        headers=auth_headers,
    )
    assert resp.status == 400


def test_enqueue_task_without_token_401(store, conn, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, _ = _request(
        conn,
        "POST",
        f"/api/tasks/{task['id']}/enqueue",
        body={},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 401
    assert store.get_task(task["id"])["lane"] is None


# -- PATCH /api/tasks/<id> lane: null ---------------------------------------------


def test_patch_task_lane_null_returns_to_inbox(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    store.enqueue_task(task["id"], "default")

    resp, parsed = _request(
        conn, "PATCH", f"/api/tasks/{task['id']}", body={"lane": None}, headers=auth_headers
    )
    assert resp.status == 200
    assert parsed["lane"] is None


def test_patch_task_lane_rejects_non_null_value(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn, "PATCH", f"/api/tasks/{task['id']}", body={"lane": "serial"}, headers=auth_headers
    )
    assert resp.status == 400


def test_patch_task_lane_not_queued_409(store, conn, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="running", source="ui", cwd=str(tmp_path)
    )
    resp, parsed = _request(
        conn, "PATCH", f"/api/tasks/{task['id']}", body={"lane": None}, headers=auth_headers
    )
    assert resp.status == 409


# -- PATCH /api/lanes -------------------------------------------------------------


def test_patch_lane_pauses(store, conn, auth_headers, tmp_path):
    resp, parsed = _request(
        conn,
        "PATCH",
        "/api/lanes",
        body={"cwd": str(tmp_path), "paused": True},
        headers=auth_headers,
    )
    assert resp.status == 200
    assert parsed["paused"] == 1


def test_patch_lane_resume_kicks(store, running_server, auth_headers, tmp_path):
    task = store.create_task(
        kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
    )
    store.enqueue_task(task["id"], "default")
    store.set_lane(str(tmp_path), paused=True, reason="x")
    calls = []
    running_server.popen = lambda *a, **k: calls.append(1) or object()

    conn = http.client.HTTPConnection("127.0.0.1", running_server.server_port)
    resp, parsed = _request(
        conn,
        "PATCH",
        "/api/lanes",
        body={"cwd": str(tmp_path), "paused": False},
        headers=auth_headers,
    )
    conn.close()
    assert resp.status == 200
    assert parsed["paused"] == 0
    assert store.get_task(task["id"])["status"] == "running"
    assert len(calls) == 1


def test_patch_lane_requires_cwd(conn, auth_headers):
    resp, parsed = _request(
        conn, "PATCH", "/api/lanes", body={"paused": True}, headers=auth_headers
    )
    assert resp.status == 400


def test_patch_lane_requires_paused_boolean(conn, auth_headers, tmp_path):
    resp, parsed = _request(
        conn,
        "PATCH",
        "/api/lanes",
        body={"cwd": str(tmp_path)},
        headers=auth_headers,
    )
    assert resp.status == 400


def test_patch_lane_without_token_401(store, conn, tmp_path):
    resp, _ = _request(
        conn,
        "PATCH",
        "/api/lanes",
        body={"cwd": str(tmp_path), "paused": True},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 401
    assert store.get_lane(str(tmp_path)) is None


# -- periodic kick from the version poll -----------------------------------------


def test_version_poll_kicks_idle_run_queues(running_server, conn, auth_headers, config, tmp_path):
    from tasky.store import Store

    with Store.open(config) as store:
        task = store.create_task(
            kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
        )
        store.enqueue_task(task["id"], "default")

    calls = []
    running_server.popen = lambda *a, **k: calls.append(1) or object()
    running_server._titles_synced_at = float("-inf")

    resp, _ = _request(conn, "GET", "/api/version", headers=auth_headers)
    assert resp.status == 200

    with Store.open(config) as store:
        assert store.get_task(task["id"])["status"] == "running"
    assert len(calls) == 1


def test_version_poll_kick_throttled_like_titles(
    running_server, conn, auth_headers, config, tmp_path
):
    from tasky.store import Store

    with Store.open(config) as store:
        task = store.create_task(
            kind="prompt", body="x", status="queued", source="ui", cwd=str(tmp_path)
        )
        store.enqueue_task(task["id"], "default")

    calls = []
    running_server.popen = lambda *a, **k: calls.append(1) or object()
    running_server._titles_synced_at = float("-inf")

    _request(conn, "GET", "/api/version", headers=auth_headers)
    assert len(calls) == 1

    # A second poll within the throttle window must not kick again even
    # though the first kick already started a task (no new work anyway, but
    # this proves the interval guard applies to kick(), not just titles).
    _request(conn, "GET", "/api/version", headers=auth_headers)
    assert len(calls) == 1


def test_version_poll_syncs_session_rename(running_server, conn, auth_headers, config, tmp_path):
    from tasky.store import Store

    transcript = tmp_path / "renamed.jsonl"
    transcript.write_text(json.dumps({"type": "custom-title", "customTitle": "tasky main"}) + "\n")
    with Store.open(config) as store:
        store.upsert_session("sess-renamed", cwd=str(tmp_path), transcript_path=str(transcript))
        rev_before = store.rev()

    resp, body = _request(conn, "GET", "/api/version", headers=auth_headers)
    assert resp.status == 200
    assert body["rev"] > rev_before

    resp, state = _request(conn, "GET", "/api/state", headers=auth_headers)
    session = next(s for s in state["sessions"] if s["id"] == "sess-renamed")
    assert session["title"] == "tasky main"

    running_server._titles_synced_at = float("-inf")
    resp, again = _request(conn, "GET", "/api/version", headers=auth_headers)
    assert again["rev"] == body["rev"]  # unchanged name does not bump the revision


# -- search --------------------------------------------------------------------


def test_search_returns_matching_tasks(store, conn, auth_headers):
    hit = store.create_task(
        kind="prompt", body="how do I rotate keys?", status="done", source="hook",
        result="Use the vault CLI.", cwd="/proj",
    )
    store.create_task(kind="prompt", body="other", status="done", source="hook", cwd="/proj")
    resp, parsed = _request(conn, "GET", "/api/search?q=vault", headers=auth_headers)
    assert resp.status == 200
    assert parsed["query"] == "vault"
    assert [t["id"] for t in parsed["tasks"]] == [hit["id"]]
    assert parsed["tasks"][0]["result"] == "Use the vault CLI."
    assert parsed["more"] is False


def test_search_filters_by_cwd(store, conn, auth_headers):
    store.create_task(kind="prompt", body="note", status="done", source="hook", cwd="/a")
    in_b = store.create_task(kind="prompt", body="note", status="done", source="hook", cwd="/b")
    resp, parsed = _request(conn, "GET", "/api/search?q=note&cwd=%2Fb", headers=auth_headers)
    assert resp.status == 200
    assert [t["id"] for t in parsed["tasks"]] == [in_b["id"]]


def test_search_flags_more_than_the_limit(store, conn, auth_headers, monkeypatch):
    monkeypatch.setattr(server_mod, "SEARCH_LIMIT", 2)
    for _ in range(3):
        store.create_task(kind="prompt", body="many", status="done", source="hook")
    resp, parsed = _request(conn, "GET", "/api/search?q=many", headers=auth_headers)
    assert len(parsed["tasks"]) == 2
    assert parsed["more"] is True


def test_search_rejects_overlong_query(conn, auth_headers):
    resp, parsed = _request(conn, "GET", "/api/search?q=" + "a" * 201, headers=auth_headers)
    assert resp.status == 400
    assert "error" in parsed
