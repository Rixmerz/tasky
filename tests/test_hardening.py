"""Concurrency, permission and token guarantees that single-writer tests cannot see."""

import multiprocessing
import os
import stat
import threading

from tasky.config import Config, load_token
from tasky.store import Store


def _open_and_create(db_path, index, errors):
    try:
        with Store(db_path) as store:
            store.create_task(kind="prompt", body=f"task {index}", status="running", source="hook")
    except Exception as exc:  # noqa: BLE001 - collected for the assertion
        errors.append(repr(exc))


def test_fresh_database_survives_concurrent_first_open(tmp_path):
    db_path = tmp_path / "fresh" / "tasky.db"
    manager = multiprocessing.Manager()
    errors = manager.list()
    procs = [
        multiprocessing.Process(target=_open_and_create, args=(db_path, i, errors))
        for i in range(8)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)

    assert list(errors) == []
    with Store(db_path) as store:
        assert len(store.list_tasks()) == 8


def test_database_and_directory_are_private(tmp_path):
    db_path = tmp_path / "private" / "tasky.db"
    old_umask = os.umask(0o022)
    try:
        Store(db_path).close()
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700


def test_claim_task_only_one_racer_wins(tmp_path):
    db_path = tmp_path / "tasky.db"
    with Store(db_path) as store:
        task = store.create_task(kind="prompt", body="once", status="queued", source="ui")

    winners = []
    barrier = threading.Barrier(6)

    def racer(index):
        with Store(db_path) as store:
            barrier.wait()
            claimed = store.claim_task(task["id"], status="running", session_id=f"s{index}")
            if claimed is not None:
                winners.append(index)

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    with Store(db_path) as store:
        assert store.get_task(task["id"])["session_id"] == f"s{winners[0]}"


def test_claim_task_returns_none_for_missing_or_wrong_status(store):
    running = store.create_task(kind="prompt", body="busy", status="running", source="hook")

    assert store.claim_task(9999, status="running") is None
    assert store.claim_task(running["id"], status="done") is None
    assert store.get_task(running["id"])["status"] == "running"
    claimed = store.claim_task(running["id"], expected_status="running", status="done")
    assert claimed["status"] == "done"


def test_running_children_counts_only_delegations(store):
    parent = store.create_task(kind="prompt", body="p", status="running", source="hook")
    store.create_task(
        kind="prompt", body="t", status="running", source="hook", parent_id=parent["id"]
    )
    assert store.running_children(parent["id"]) == []

    store.create_task(
        kind="delegation", body="d", status="running", source="hook", parent_id=parent["id"]
    )
    assert len(store.running_children(parent["id"])) == 1


def test_interrupt_running_skips_excluded_and_busy_tasks(store):
    kept = store.create_task(
        kind="prompt", body="current", status="running", source="hook", session_id="s1"
    )
    busy = store.create_task(
        kind="prompt", body="busy", status="running", source="hook", session_id="s1"
    )
    store.create_task(
        kind="delegation", body="d", status="running", source="hook", parent_id=busy["id"]
    )
    stale = store.create_task(
        kind="prompt", body="stale", status="running", source="hook", session_id="s1"
    )
    other = store.create_task(
        kind="prompt", body="other", status="running", source="hook", session_id="s2"
    )

    assert store.interrupt_running("s1", exclude_ids=[kept["id"]]) == 1

    assert store.get_task(stale["id"])["status"] == "interrupted"
    for task in (kept, busy, other):
        assert store.get_task(task["id"])["status"] == "running"


def test_session_has_tasks_not_from(store):
    store.create_task(kind="prompt", body="a", status="done", source="import", session_id="s1")
    assert store.session_has_tasks_not_from("s1", "import") is False
    store.create_task(kind="prompt", body="b", status="running", source="cli", session_id="s1")
    assert store.session_has_tasks_not_from("s1", "import") is True


def test_token_is_created_private_and_reused(config):
    token = load_token(config)

    assert token and len(token) >= 32
    assert stat.S_IMODE(config.token_path.stat().st_mode) == 0o600
    assert load_token(config) == token


def test_token_not_created_when_create_is_false(config):
    assert load_token(config, create=False) is None
    assert not config.token_path.exists()


def test_malformed_token_is_replaced(config):
    config.token_path.parent.mkdir(parents=True)
    config.token_path.write_text("short")

    token = load_token(config)

    assert token != "short"  # noqa: S105 - comparing against the planted bad value
    assert config.token_path.read_text() == token


def test_concurrent_token_creation_agrees(config):
    results = []
    barrier = threading.Barrier(5)

    def create():
        barrier.wait()
        results.append(load_token(config))

    threads = [threading.Thread(target=create) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 1


def test_allow_bypass_parsing():
    assert Config.from_env({"TASKY_HOME": "/x", "TASKY_ALLOW_BYPASS": "1"}).allow_bypass is True
    assert Config.from_env({"TASKY_HOME": "/x", "TASKY_ALLOW_BYPASS": "yes"}).allow_bypass is True
    assert Config.from_env({"TASKY_HOME": "/x"}).allow_bypass is False


# --- follow-ups from the security review -------------------------------------------------


def _serve_in_thread(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_server_alive_requires_proof_of_token(config):
    from tasky import cli
    from tasky.server import make_server

    token = load_token(config)
    server = make_server(config, port=0, token=token)
    _serve_in_thread(server)
    try:
        assert cli._server_alive(server.server_port, token) is True
        assert cli._server_alive(server.server_port, "x" * 43) is False
    finally:
        server.shutdown()
        server.server_close()


def test_server_alive_rejects_a_port_squatter(config):
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from tasky import cli

    class Squatter(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            body = json.dumps({"rev": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    squatter = ThreadingHTTPServer(("127.0.0.1", 0), Squatter)
    _serve_in_thread(squatter)
    try:
        assert cli._server_alive(squatter.server_port, load_token(config)) is False
    finally:
        squatter.shutdown()
        squatter.server_close()


def test_lost_worker_race_leaves_no_orphan_session(store, config, tmp_path):
    from tasky.worker import TaskNotQueued, run_task

    task = store.create_task(
        kind="prompt", body="x", status="running", source="ui", cwd=str(tmp_path)
    )
    before = len(store.list_sessions())

    try:
        run_task(store, config, task["id"], popen=lambda *a, **k: None)
    except TaskNotQueued:
        pass
    else:
        raise AssertionError("expected TaskNotQueued")

    assert len(store.list_sessions()) == before


def test_relative_tasky_home_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = Config.from_env({"TASKY_HOME": "rel-home"})

    assert config.home == tmp_path / "rel-home"
    assert config.db_path.is_absolute()
