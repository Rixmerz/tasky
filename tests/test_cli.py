import io
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tasky import cli
from tasky.store import Store


@pytest.fixture
def env(tmp_path):
    return {
        "TASKY_HOME": str(tmp_path / "tasky-home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
    }


def run(env, *argv):
    out = io.StringIO()
    code = cli.main(list(argv), env=env, out=out)
    return code, out.getvalue()


def open_store(env):
    from tasky.config import Config

    return Store.open(Config.from_env(env))


def test_no_command_prints_help(env):
    code, output = run(env)
    assert code == 2
    assert "usage" in output


def test_add_queues_task_for_given_directory(env, tmp_path):
    project = tmp_path / "app"
    project.mkdir()

    code, output = run(env, "add", "update", "the", "changelog", "--cwd", str(project))

    assert code == 0
    assert output.startswith("queued #1 update the changelog")
    with open_store(env) as store:
        task = store.get_task(1)
    assert task["status"] == "queued"
    assert task["body"] == "update the changelog"
    assert task["cwd"] == str(project)
    assert task["source"] == "cli"


def test_add_defaults_to_current_directory(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    run(env, "add", "write docs")

    with open_store(env) as store:
        assert store.get_task(1)["cwd"] == str(tmp_path)


def test_add_rejects_blank_text(env, capsys):
    code, _ = run(env, "add", "   ")

    assert code == 1
    assert "empty" in capsys.readouterr().err


def test_list_empty(env):
    code, output = run(env, "list")
    assert code == 0
    assert output.strip() == "no tasks"


def test_list_text_and_json(env, tmp_path):
    run(env, "add", "first", "--cwd", str(tmp_path))
    run(env, "add", "second", "--cwd", str(tmp_path))

    code, text = run(env, "list")
    _, raw = run(env, "list", "--json", "--status", "queued")

    assert code == 0
    assert "⏸ #1" in text and "first" in text
    assert [t["title"] for t in json.loads(raw)] == ["first", "second"]


def test_done_and_cancel_change_status(env, tmp_path):
    run(env, "add", "a", "--cwd", str(tmp_path))
    run(env, "add", "b", "--cwd", str(tmp_path))

    assert run(env, "done", "1") == (0, "#1 done\n")
    assert run(env, "cancel", "2") == (0, "#2 cancelled\n")


def test_done_unknown_task_reports_not_found(env, capsys):
    code, _ = run(env, "done", "99")

    assert code == 1
    assert "not found: 99" in capsys.readouterr().err


def test_status_short_counts(env, tmp_path):
    run(env, "add", "a", "--cwd", str(tmp_path))
    run(env, "add", "b", "--cwd", str(tmp_path))
    with open_store(env) as store:
        store.create_task(kind="prompt", body="c", status="running", source="hook")
        store.create_task(kind="prompt", body="d", status="failed", source="hook")
        store.create_task(kind="prompt", body="e", status="interrupted", source="hook")

    assert run(env, "status", "--short") == (0, "▶1 ⏸2 ⚠2\n")
    assert run(env, "status")[1] == "running 1, queued 2, needs attention 2\n"


def test_import_prints_report(env, monkeypatch):
    calls = {}

    def fake_import(store, config, *, dry_run=False):
        calls["dry_run"] = dry_run
        return {"files": 2, "sessions": 1, "tasks": 3, "skipped_sessions": 0, "bad_lines": 1}

    monkeypatch.setattr("tasky.importer.import_transcripts", fake_import)

    code, output = run(env, "import", "--dry-run")

    assert code == 0
    assert calls == {"dry_run": True}
    assert output.startswith("would import 3 tasks from 1 sessions")


def test_run_success_and_worker_error(env, tmp_path, monkeypatch, capsys):
    from tasky import worker

    def fake_run(store, config, task_id, permission_mode="default", mode="now"):
        if task_id == 2:
            raise worker.WorkerError("task #2 is not queued")
        return {"id": task_id, "session_id": "sess-1"}

    monkeypatch.setattr(worker, "run_task", fake_run)

    assert run(env, "run", "1", "--permission-mode", "acceptEdits") == (
        0,
        "#1 running in session sess-1\n",
    )
    code, _ = run(env, "run", "2")
    assert code == 1
    assert "not queued" in capsys.readouterr().err


def test_run_passes_mode_to_worker(env, tmp_path, monkeypatch):
    from tasky import worker

    calls = []

    def fake_run(store, config, task_id, permission_mode="default", mode="now"):
        calls.append((task_id, permission_mode, mode))
        return {"id": task_id, "session_id": "sess-1"}

    monkeypatch.setattr(worker, "run_task", fake_run)

    run(env, "run", "1", "--mode", "fork")

    assert calls == [(1, "default", "fork")]


def test_enqueue_moves_task_into_its_run_queue(env, tmp_path, monkeypatch):
    # scheduler.kick() launches through worker.run_task; stub it here so an
    # idle lane's immediate kick never spawns a real `claude` process.
    from tasky import worker

    calls = []

    def fake_run_task(store, config, task_id, permission_mode="default", mode="now", popen=None):
        calls.append((task_id, mode))
        return store.update_task(task_id, status="running", session_id="s1")

    monkeypatch.setattr(worker, "run_task", fake_run_task)

    project = tmp_path / "app"
    project.mkdir()
    run(env, "add", "do it", "--cwd", str(project))

    code, output = run(env, "enqueue", "1", "--permission-mode", "acceptEdits")

    assert code == 0
    assert "#1" in output
    with open_store(env) as store:
        task = store.get_task(1)
    assert task["lane"] == "serial"
    assert task["permission_mode"] == "acceptEdits"
    assert task["status"] == "running"  # an idle lane starts it right away
    assert calls == [(1, "serial")]


def test_enqueue_unknown_task_reports_not_found(env, capsys):
    code, _ = run(env, "enqueue", "99")
    assert code == 1
    assert "not found: 99" in capsys.readouterr().err


def test_enqueue_rejects_invalid_permission_mode(env, tmp_path, capsys):
    run(env, "add", "do it", "--cwd", str(tmp_path))
    code, _ = run(env, "enqueue", "1", "--permission-mode", "yolo")
    assert code == 1
    assert "invalid permission mode" in capsys.readouterr().err
    with open_store(env) as store:
        assert store.get_task(1)["lane"] is None


def test_enqueue_rejects_bypass_permissions_without_allow_bypass(env, tmp_path, capsys):
    run(env, "add", "do it", "--cwd", str(tmp_path))
    code, _ = run(env, "enqueue", "1", "--permission-mode", "bypassPermissions")
    assert code == 1
    assert "TASKY_ALLOW_BYPASS" in capsys.readouterr().err


def test_enqueue_task_without_cwd_reports_error(env, capsys):
    with open_store(env) as store:
        store.create_task(kind="prompt", body="a", status="queued", source="cli")
    code, _ = run(env, "enqueue", "1")
    assert code == 1
    assert "cwd" in capsys.readouterr().err


def test_enqueue_not_queued_reports_error(env, tmp_path, capsys):
    with open_store(env) as store:
        store.create_task(
            kind="prompt", body="a", status="running", source="hook", cwd=str(tmp_path)
        )
    code, _ = run(env, "enqueue", "1")
    assert code == 1
    assert "not queued" in capsys.readouterr().err


def _token(env):
    from tasky.config import Config, load_token

    return load_token(Config.from_env(env))


def test_ui_prints_tokenized_url_when_server_already_running(env, monkeypatch):
    monkeypatch.setattr(cli, "_server_alive", lambda port, token: True)
    started = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: started.append(a))

    code, output = run(env, "ui", "--port", "7799")

    assert code == 0
    assert output == f"http://127.0.0.1:7799/#token={_token(env)}\n"
    assert started == []


def test_ui_starts_server_when_not_running(env, monkeypatch):
    alive = iter([False, True])
    monkeypatch.setattr(cli, "_server_alive", lambda port, token: next(alive))
    started = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))

    code, output = run(env, "ui", "--port", "7799")

    assert code == 0
    assert output == f"http://127.0.0.1:7799/#token={_token(env)}\n"
    cmd, kwargs = started[0]
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("bin/tasky")
    assert cmd[-3:] == ["serve", "--port", "7799"]
    assert kwargs["start_new_session"] is True


def test_ui_never_launches_via_m_tasky(env, monkeypatch):
    """`-m tasky` puts the caller's cwd first on sys.path (CWE-427); bin/tasky doesn't."""
    monkeypatch.setattr(cli, "_server_alive", lambda port, token: False)
    monkeypatch.setattr(cli, "_wait_alive", lambda port, token, timeout: True)
    started = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))

    run(env, "ui", "--port", "7799")

    cmd = started[0]
    assert "-m" not in cmd
    assert cmd[1] != "tasky"


def test_ui_reports_failure_when_server_never_answers(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_server_alive", lambda port, token: False)
    monkeypatch.setattr(cli, "_wait_alive", lambda port, token, timeout: False)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: None)

    code, _ = run(env, "ui", "--port", "7799")

    assert code == 1
    assert "did not start" in capsys.readouterr().err


def test_server_alive_false_when_nothing_listens():
    assert cli._server_alive(1, "sometoken") is False  # noqa: S106 - synthetic test value


def test_server_alive_sends_no_token_header_to_the_listener():
    """A raw listener on a free port proves `_server_alive` never sends the
    token before the listener has proved it's the real server (R1, CWE-522).
    """
    import socket
    import threading

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    captured = {}

    def accept_once():
        conn, _ = sock.accept()
        data = b""
        conn.settimeout(2)
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        except OSError:
            pass
        captured["request"] = data
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        conn.close()

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    try:
        result = cli._server_alive(port, "the-real-token")  # noqa: S106
    finally:
        thread.join(timeout=5)
        sock.close()

    assert result is False
    request = captured.get("request", b"")
    assert b"X-Tasky-Token" not in request
    assert b"X-Tasky-Nonce" in request


def test_server_alive_rejects_proof_bound_to_a_different_port(config):
    """A proof lifted from a real server on another port must not verify here."""
    from tasky.config import load_token, token_proof
    from tasky.server import make_server

    token = load_token(config)
    real = make_server(config, port=0, token=token)
    real_thread = __import__("threading").Thread(target=real.serve_forever, daemon=True)
    real_thread.start()

    # A decoy that answers with a proof computed for the real server's port,
    # not its own -- exactly what a relaying squatter would try.
    import socket
    import threading

    decoy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    decoy_sock.bind(("127.0.0.1", 0))
    decoy_sock.listen(1)
    decoy_port = decoy_sock.getsockname()[1]

    def relay_once():
        conn, _ = decoy_sock.accept()
        conn.settimeout(2)
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        except OSError:
            conn.close()
            return
        nonce = None
        for line in data.split(b"\r\n"):
            if line.lower().startswith(b"x-tasky-nonce:"):
                nonce = line.split(b":", 1)[1].strip().decode("ascii")
        proof = token_proof(token, nonce, real.server_port)  # bound to the real port
        body = json.dumps({"proof": proof}).encode("utf-8")
        resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        resp += str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        conn.sendall(resp)
        conn.close()

    decoy_thread = threading.Thread(target=relay_once, daemon=True)
    decoy_thread.start()
    try:
        result = cli._server_alive(decoy_port, token)
    finally:
        decoy_thread.join(timeout=5)
        decoy_sock.close()
        real.shutdown()
        real_thread.join(timeout=5)
        real.server_close()

    assert result is False


def test_ui_open_writes_private_file_and_opens_it_tokenless(env, monkeypatch):
    from tasky.config import Config

    monkeypatch.setattr(cli, "_server_alive", lambda port, token: True)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: None)
    opened = []
    monkeypatch.setattr(
        "tasky.server.webbrowser.open", lambda url: opened.append(url)
    )

    code, output = run(env, "ui", "--port", "7799", "--open")

    assert code == 0
    token = _token(env)
    assert output == f"http://127.0.0.1:7799/#token={token}\n"
    assert len(opened) == 1
    assert opened[0].startswith("file://")
    assert f"#token={token}" not in opened[0]

    config = Config.from_env(env)
    page = config.log_dir / "open.html"
    assert page.exists()
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    assert f"#token={token}" in page.read_text(encoding="utf-8")


def test_hook_passes_cli_env_through(env, monkeypatch):
    captured = {}

    def fake_hook_main(stdin, stdout, hook_env):
        captured["env"] = hook_env
        return 0

    monkeypatch.setattr("tasky.hooks.main", fake_hook_main)

    code, _ = run(env, "hook")

    assert code == 0
    assert captured["env"] == env


def test_supervise_dispatches_to_supervise_module(env, monkeypatch):
    calls = []

    def fake_supervise(config, task_id, session_id, prompt_path, log_path, argv):
        calls.append((task_id, session_id, prompt_path, log_path, argv))
        return 0

    monkeypatch.setattr("tasky.supervise.supervise", fake_supervise)

    code, _ = run(
        env,
        "supervise",
        "5",
        "sid-1",
        "/tmp/p.prompt",
        "/tmp/t.log",
        "--",
        "claude",
        "-p",
        "--session-id",
        "sid-1",
    )

    assert code == 0
    assert calls == [
        (5, "sid-1", "/tmp/p.prompt", "/tmp/t.log", ["claude", "-p", "--session-id", "sid-1"])
    ]


def test_bin_tasky_shim_is_not_shadowed_by_cwd_package(tmp_path):
    """A cwd shipping its own tasky/ package must not shadow the real one (F3)."""
    evil = tmp_path / "evilrepo"
    package = evil / "tasky"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('SHADOWED: attacker tasky package executed')", encoding="utf-8"
    )

    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [str(repo_root / "bin" / "tasky"), "--version"],
        cwd=evil,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "SHADOWED" not in result.stdout
    assert "SHADOWED" not in result.stderr
    assert "tasky" in result.stdout


def test_bin_tasky_shim_works_through_a_symlink(tmp_path):
    """The shim resolves through a symlink placed anywhere, not just its real dir."""
    evil = tmp_path / "evilrepo"
    package = evil / "tasky"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('SHADOWED: attacker tasky package executed')", encoding="utf-8"
    )

    repo_root = Path(__file__).resolve().parent.parent
    link_dir = tmp_path / "elsewhere"
    link_dir.mkdir()
    link = link_dir / "tasky"
    link.symlink_to(repo_root / "bin" / "tasky")

    result = subprocess.run(
        [str(link), "--version"], cwd=evil, capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0
    assert "SHADOWED" not in result.stdout
    assert "SHADOWED" not in result.stderr
