"""Local-only HTTP dashboard and JSON API for Tasky."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from tasky import __version__, history, repos, scheduler, smart_search, transcripts, worker
from tasky.config import Config, load_token, token_proof
from tasky.store import SEARCH_LIMIT, TASK_STATUSES, Store
from tasky.titles import TitleWatcher

_NONCE_RE = re.compile(r"[0-9a-f]{16,128}")
_CONTENT_LENGTH_RE = re.compile(r"^\d{1,7}$")
_MAX_BODY = 1024 * 1024  # 1 MiB
_SEARCH_MAX_QUERY = 200
_CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/digest.js": ("digest.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
_TASK_ID_RE = re.compile(r"^/api/tasks/(\d{1,18})$")
_TASK_RUN_RE = re.compile(r"^/api/tasks/(\d{1,18})/run$")
_TASK_ENQUEUE_RE = re.compile(r"^/api/tasks/(\d{1,18})/enqueue$")
_TASK_RESTORE_RE = re.compile(r"^/api/tasks/(\d{1,18})/restore$")
_SESSION_ID_RE = re.compile(r"^/api/sessions/([^/]+)$")
_TASK_PATCH_FIELDS = ("status", "title", "body", "before_id", "lane", "permission_mode")
_SESSION_PATCH_FIELDS = ("auto_pull", "title")


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    # A client that opens a connection and then stalls (slow headers, a body
    # promised by Content-Length that never arrives) must not tie up a thread
    # forever; ThreadingHTTPServer gives each connection its own thread, so
    # this bounds that one instead of blocking any other client (CWE-400).
    timeout = 10

    def log_message(self, format: str, *args) -> None:
        pass

    def send_error(self, code, message=None, explain=None) -> None:
        self._send_json(code, {"error": message or HTTPStatus(code).phrase})

    # -- shared plumbing ----------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_port
        return host.lower() in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self) -> bool:
        # http.server decodes header values as latin-1, so this always encodes
        # cleanly; compare_digest on str rejects non-ASCII input outright, and
        # the token itself is base64url so comparing as bytes is always safe.
        token = self.server.current_token()
        if token is None:
            return False
        supplied = self.headers.get("X-Tasky-Token", "").encode("latin-1")
        return hmac.compare_digest(supplied, token.encode("ascii"))

    def _read_json_body(self) -> object | None:
        """Return the parsed JSON body, or None (and an error already sent) on failure."""
        length_raw = self.headers.get("Content-Length")
        if length_raw is None or not _CONTENT_LENGTH_RE.fullmatch(length_raw):
            self._error(400, "invalid Content-Length")
            return None
        length = int(length_raw)
        if length > _MAX_BODY:
            self._error(413, "request body too large")
            return None
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            # ValueError, not just JSONDecodeError: invalid bytes (e.g. malformed
            # UTF-8) raise UnicodeDecodeError here, a ValueError subclass.
            self._error(400, "invalid JSON body")
            return None
        if not isinstance(data, dict):
            self._error(400, "JSON body must be an object")
            return None
        return data

    def _mutation_allowed(self) -> bool:
        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._error(403, "Content-Type must be application/json")
            return False
        return True

    # -- HTTP verbs -----------------------------------------------------------

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        if not self._host_ok():
            self._error(403, "unrecognized Host header")
            return
        self._error(403, "cross-origin requests are not supported")

    def _dispatch(self, method: str) -> None:
        try:
            self._dispatch_unsafe(method)
        except (TimeoutError, BrokenPipeError, ConnectionResetError):
            # A stalled or vanished client, not a server bug: nothing useful to
            # send back, and logging it would just fill the disk with noise
            # from every client that navigates away mid-request.
            self.close_connection = True
        except Exception:
            # Nothing above this must drop the connection or leak a traceback
            # to the client (a malformed header, a non-UTF-8 body, a handler
            # bug); it still needs to be diagnosable, so it goes to the same
            # per-home error log hooks.py uses for the same reason.
            config = self.server.config
            with contextlib.suppress(OSError), (config.home / "server-errors.log").open(
                "a", encoding="utf-8"
            ) as fh:
                fh.write(traceback.format_exc())
            self._error(500, "internal error")

    def _dispatch_unsafe(self, method: str) -> None:
        if not self._host_ok():
            self._error(403, "unrecognized Host header")
            return

        path = urlsplit(self.path).path
        is_version_get = method == "GET" and path == "/api/version"

        # /api/version has its own auth: an unauthenticated caller with a
        # valid nonce gets a proof-only reply (see _route_version), so it is
        # exempt from the blanket token gate applied to every other /api/ route.
        if path.startswith("/api/") and not is_version_get and not self._token_ok():
            self._error(401, "missing or invalid token")
            return

        if method in ("POST", "PATCH", "DELETE"):
            if not path.startswith("/api/"):
                # Never read a body for a route that can't use one (CWE-400):
                # a client can promise any Content-Length up to the 1 MiB cap
                # to a non-API path and tie up nothing more than this check.
                self._error(404, "not found")
                return
            if not self._mutation_allowed():
                return
            body = self._read_json_body()
            if body is None:
                return
        else:
            body = None

        self._route(method, path, body)

    def _route(self, method: str, path: str, body: dict | None) -> None:
        if method == "GET" and path == "/api/version":
            self._route_version()
        elif method == "GET" and path == "/api/state":
            self._route_state()
        elif method == "GET" and path == "/api/search":
            self._route_search()
        elif method == "POST" and path == "/api/search/smart":
            self._route_smart_search(body)
        elif method == "GET" and path == "/api/history":
            self._route_history()
        elif method == "POST" and path == "/api/history/sync":
            self._route_history_sync(body)
        elif method == "POST" and path == "/api/tasks":
            self._route_create_task(body)
        elif method == "POST" and path == "/api/import":
            self._route_import()
        elif method == "GET" and path in _STATIC_FILES:
            self._route_static(path)
        elif method == "PATCH" and path == "/api/lanes":
            self._route_patch_lane(body)
        else:
            match = _TASK_RUN_RE.match(path)
            if method == "POST" and match:
                self._route_run_task(int(match.group(1)), body)
                return
            match = _TASK_RESTORE_RE.match(path)
            if match and method == "POST":
                self._route_restore_task(int(match.group(1)))
                return
            match = _TASK_ENQUEUE_RE.match(path)
            if method == "POST" and match:
                self._route_enqueue_task(int(match.group(1)), body)
                return
            match = _TASK_ID_RE.match(path)
            if match:
                task_id = int(match.group(1))
                if method == "GET":
                    self._route_get_task(task_id)
                elif method == "PATCH":
                    self._route_patch_task(task_id, body)
                elif method == "DELETE":
                    self._route_delete_task(task_id)
                else:
                    self._error(404, "not found")
                return
            match = _SESSION_ID_RE.match(path)
            if method == "PATCH" and match:
                self._route_patch_session(unquote(match.group(1)), body)
                return
            self._error(404, "not found")

    # -- routes -----------------------------------------------------------

    def _route_version(self) -> None:
        # The only unauthenticated API reply: `tasky ui` needs a way to prove
        # a listener is the real, token-holding server *before* it hands that
        # listener the token (a squatter on the port must never receive it,
        # CWE-522). A caller with no token header gets a proof only -- never
        # `rev`, which would leak state to an unauthenticated caller.
        token = self.server.current_token()
        nonce = self.headers.get("X-Tasky-Nonce", "")
        nonce_ok = bool(_NONCE_RE.fullmatch(nonce))
        port = self.server.server_port

        if "X-Tasky-Token" not in self.headers:
            if token is None or not nonce_ok:
                self._error(401, "missing or invalid token")
                return
            self._send_json(200, {"proof": token_proof(token, nonce, port)})
            return

        if not self._token_ok():
            self._error(401, "missing or invalid token")
            return

        with Store.open(self.server.config) as store:
            self.server.sync_titles(store)
            payload: dict = {"rev": store.rev()}
        if nonce_ok and token is not None:
            payload["proof"] = token_proof(token, nonce, port)
        self._send_json(200, payload)

    def _route_state(self) -> None:
        config = self.server.config
        with Store.open(config) as store:
            state = store.state()
        state["version"] = __version__
        state["config"] = {
            "queue_prefix": config.queue_prefix,
            "max_chain": config.max_chain,
            "port": config.port,
            "allow_bypass": config.allow_bypass,
        }
        self._send_json(200, state)

    def _route_smart_search(self, body: dict | None) -> None:
        query = body.get("q") if isinstance(body, dict) else None
        if not isinstance(query, str) or not query.strip():
            self._error(400, "q is required")
            return
        if len(query) > _SEARCH_MAX_QUERY:
            self._error(400, f"q is longer than {_SEARCH_MAX_QUERY} characters")
            return
        cwd = body.get("cwd") if isinstance(body.get("cwd"), str) and body.get("cwd") else None
        config = self.server.config
        try:
            with Store.open(config) as store:
                found = smart_search.search(config, store, query, cwd=cwd)
        except smart_search.SmartSearchError as exc:
            self._error(502, str(exc))
            return
        self._send_json(200, found)

    def _route_search(self) -> None:
        params = parse_qs(urlsplit(self.path).query)
        query = params.get("q", [""])[0].strip()
        if len(query) > _SEARCH_MAX_QUERY:
            self._error(400, f"q is longer than {_SEARCH_MAX_QUERY} characters")
            return
        cwd = params.get("cwd", [""])[0] or None
        with Store.open(self.server.config) as store:
            tasks = store.search_tasks(query, cwd=cwd, limit=SEARCH_LIMIT + 1)
        self._send_json(
            200,
            {"query": query, "tasks": tasks[:SEARCH_LIMIT], "more": len(tasks) > SEARCH_LIMIT},
        )

    def _route_get_task(self, task_id: int) -> None:
        with Store.open(self.server.config) as store:
            task = store.get_task(task_id)
            if task is not None and task["prompt_id"] and task["kind"] == "prompt":
                task["files"] = store.edited_files(task["prompt_id"])
                task["stats"] = store.task_stats([task["prompt_id"]]).get(task["prompt_id"])
        if task is None:
            self._error(404, "task not found")
            return
        task.setdefault("files", [])
        task.setdefault("stats", None)
        self._send_json(200, task)

    def _route_history(self) -> None:
        scope = parse_qs(urlsplit(self.path).query).get("repo", ["all"])[0] or "all"
        config = self.server.config
        with Store.open(config) as store:
            repos.ensure(store, store.task_cwds())
            repo_list = store.repo_list()
            for entry in repo_list:
                entry["pending"] = store.pending_history_tasks(entry["cwds"])
                entry["sync"] = store.history_sync(entry["repo"])
            repo = None if scope == "all" else scope
            payload = {
                "scope": scope,
                "default_model": config.history_model,
                "models": list(history.MODELS),
                "repos": repo_list,
                "milestones": store.milestones(repo),
                "problems": store.problems(repo),
            }
        self._send_json(200, payload)

    def _route_history_sync(self, body: dict) -> None:
        repo = body.get("repo")
        if not isinstance(repo, str) or not repo:
            self._error(400, "repo is required")
            return
        model = body.get("model", self.server.config.history_model)
        if model not in history.MODELS:
            self._error(400, f"model must be one of {', '.join(history.MODELS)}")
            return
        config = self.server.config
        with Store.open(config) as store:
            # Only repos Tasky has recorded work in: the sync runs git in their folders.
            if not store.repo_cwds(repo):
                self._error(404, "no tasks recorded for this repository")
                return
            current = store.history_sync(repo)
        if current is not None and current["state"] == "running":
            self._error(409, "a sync of this repository is already running")
            return
        config.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            config.log_dir / "history.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        try:
            self.server.popen(
                [
                    sys.executable, str(worker._TASKY_BIN), "history",
                    "--repo", repo, "--model", model,
                ],
                cwd=str(config.home),
                stdin=subprocess.DEVNULL,
                stdout=fd,
                stderr=fd,
                start_new_session=True,
            )
        finally:
            os.close(fd)
        self._send_json(202, {"started": True})

    def _route_create_task(self, body: dict) -> None:
        task_body = body.get("body")
        if not isinstance(task_body, str) or not task_body.strip():
            self._error(400, "body is required")
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            self._error(400, "cwd is required")
            return
        title = body.get("title")
        if title is not None and not isinstance(title, str):
            self._error(400, "title must be a string")
            return
        session_id = body.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            self._error(400, "session_id must be a string")
            return
        permission_mode = body.get("permission_mode")
        problem = self._permission_problem(permission_mode) if permission_mode else None
        if problem:
            self._error(400, problem)
            return

        with Store.open(self.server.config) as store:
            task = store.create_task(
                kind="prompt",
                body=task_body,
                status="queued",
                source="ui",
                cwd=cwd,
                title=title,
                session_id=session_id,
            )
            if permission_mode:
                task = store.update_task(task["id"], permission_mode=permission_mode)
        self._send_json(201, task)

    def _permission_problem(self, permission_mode: object) -> str | None:
        """Why a requested permission mode cannot be used, or None when it can."""
        if not isinstance(permission_mode, str):
            return "permission_mode must be a string"
        if permission_mode not in worker.PERMISSION_MODES:
            return f"invalid permission mode: {permission_mode!r}"
        if permission_mode == "bypassPermissions" and not self.server.config.allow_bypass:
            return "bypassPermissions requires TASKY_ALLOW_BYPASS=1"
        return None

    def _route_patch_task(self, task_id: int, body: dict) -> None:
        unknown = set(body) - set(_TASK_PATCH_FIELDS)
        if unknown:
            self._error(400, f"unknown field(s): {', '.join(sorted(unknown))}")
            return

        with Store.open(self.server.config) as store:
            if store.get_task(task_id) is None:
                self._error(404, "task not found")
                return

            if "lane" in body:
                if body["lane"] is not None:
                    self._error(400, "lane must be null")
                    return
                task = store.clear_task_lane(task_id)
                if task is None:
                    self._error(409, f"task {task_id} is not queued")
                    return
                self._send_json(200, task)
                return

            if "before_id" in body:
                before_id = body["before_id"]
                if before_id is not None and not (
                    isinstance(before_id, int) and not isinstance(before_id, bool)
                ):
                    self._error(400, "before_id must be an int or null")
                    return
                try:
                    task = store.move_task(task_id, before_id)
                except KeyError:
                    self._error(400, "unknown before_id")
                    return
                self._send_json(200, task)
                return

            fields = {}
            if "permission_mode" in body:
                problem = self._permission_problem(body["permission_mode"])
                if problem:
                    self._error(400, problem)
                    return
                fields["permission_mode"] = body["permission_mode"]
            for key in ("status", "title", "body"):
                if key not in body:
                    continue
                value = body[key]
                if key == "status":
                    if value not in TASK_STATUSES:
                        self._error(400, f"invalid status: {value!r}")
                        return
                elif not isinstance(value, str):
                    self._error(400, f"{key} must be a string")
                    return
                fields[key] = value
            if not fields:
                self._error(400, "no valid fields to update")
                return

            task = store.update_task(task_id, **fields)
        self._send_json(200, task)

    def _route_delete_task(self, task_id: int) -> None:
        # Deleting only hides the task: people delete to clear the board, while
        # the history sync and the MCP tools still learn from what it recorded.
        with Store.open(self.server.config) as store:
            hidden = store.hide_task(task_id)
        if not hidden:
            self._error(404, "task not found")
            return
        self._send_json(200, {"deleted": True, "hidden": True})

    def _route_restore_task(self, task_id: int) -> None:
        with Store.open(self.server.config) as store:
            task = store.restore_task(task_id)
        if task is None:
            self._error(404, "task not found")
            return
        self._send_json(200, task)

    def _route_run_task(self, task_id: int, body: dict) -> None:
        permission_mode = body.get("permission_mode")
        if permission_mode is not None and not isinstance(permission_mode, str):
            self._error(400, "permission_mode must be a string")
            return
        mode = body.get("mode", "now")
        if not isinstance(mode, str) or mode not in ("now", "fork"):
            self._error(400, f"invalid mode: {mode!r}")
            return

        config = self.server.config
        with Store.open(config) as store:
            stored = store.get_task(task_id)
            if stored is None:
                self._error(404, "task not found")
                return
            # No mode in the request: run with the one chosen when the task was added.
            permission_mode = permission_mode or stored["permission_mode"] or "default"
            # The queued check happens inside run_task's atomic claim, not here:
            # a check-then-act split here is exactly the race two concurrent
            # /run calls would win together (CWE-367).
            try:
                task = worker.run_task(
                    store,
                    config,
                    task_id,
                    permission_mode=permission_mode,
                    mode=mode,
                    popen=self.server.popen,
                )
            except worker.TaskNotQueued as exc:
                self._error(409, str(exc))
                return
            except worker.WorkerError as exc:
                self._error(400, str(exc))
                return
        self._send_json(200, task)

    def _route_enqueue_task(self, task_id: int, body: dict) -> None:
        permission_mode = body.get("permission_mode")
        problem = self._permission_problem(permission_mode) if permission_mode is not None else None
        if problem:
            self._error(400, problem)
            return
        config = self.server.config
        before_id = body.get("before_id")
        if before_id is not None and not (
            isinstance(before_id, int) and not isinstance(before_id, bool)
        ):
            self._error(400, "before_id must be an int or null")
            return

        with Store.open(config) as store:
            task = store.get_task(task_id)
            if task is None:
                self._error(404, "task not found")
                return
            if not task.get("cwd"):
                self._error(400, "task has no cwd")
                return
            permission_mode = permission_mode or task["permission_mode"] or "default"
            problem = self._permission_problem(permission_mode)
            if problem:
                self._error(400, problem)
                return
            try:
                enqueued = store.enqueue_task(task_id, permission_mode, before_id)
            except KeyError:
                self._error(400, "unknown before_id")
                return
            if enqueued is None:
                self._error(409, f"task {task_id} is not queued")
                return
            scheduler.kick(store, config, enqueued["cwd"], popen=self.server.popen)
            task = store.get_task(task_id)
        self._send_json(200, task)

    def _route_patch_session(self, session_id: str, body: dict) -> None:
        unknown = set(body) - set(_SESSION_PATCH_FIELDS)
        if unknown:
            self._error(400, f"unknown field(s): {', '.join(sorted(unknown))}")
            return
        fields = {}
        if "auto_pull" in body:
            if not isinstance(body["auto_pull"], bool):
                self._error(400, "auto_pull must be a boolean")
                return
            fields["auto_pull"] = body["auto_pull"]
        if "title" in body:
            if not isinstance(body["title"], str):
                self._error(400, "title must be a string")
                return
            fields["title"] = body["title"]
        if not fields:
            self._error(400, "no valid fields to update")
            return

        with Store.open(self.server.config) as store:
            try:
                session = store.update_session(session_id, **fields)
            except KeyError:
                self._error(404, "session not found")
                return
        self._send_json(200, session)

    def _route_patch_lane(self, body: dict) -> None:
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            self._error(400, "cwd is required")
            return
        paused = body.get("paused")
        if not isinstance(paused, bool):
            self._error(400, "paused must be a boolean")
            return

        config = self.server.config
        with Store.open(config) as store:
            store.set_lane(cwd, paused=paused, reason=None)
            if not paused:
                scheduler.kick(store, config, cwd, popen=self.server.popen)
            lane = store.get_lane(cwd)
        self._send_json(200, lane)

    def _route_import(self) -> None:
        from tasky import importer

        config = self.server.config
        with Store.open(config) as store:
            report = importer.import_transcripts(store, config)
        # Copying every message of every transcript can take minutes on a long
        # history; the tasks above are what the board needs now.
        threading.Thread(target=_backfill_messages, args=(config,), daemon=True).start()
        report["messages"] = "copying in the background"
        self._send_json(200, report)

    def _route_static(self, path: str) -> None:
        filename, content_type = _STATIC_FILES[path]
        file_path = self.server.web_dir / filename
        if not file_path.is_file():
            self._error(404, "not found")
            return
        self._send(200, file_path.read_bytes(), content_type)


class _Server(ThreadingHTTPServer):
    config: Config
    web_dir: Path
    token: str
    token_fixed: bool
    _token_cache_key: tuple | None
    _token_cache_value: str | None

    titles: TitleWatcher
    _titles_lock: threading.Lock
    _titles_synced_at: float
    # Every worker/scheduler launch made from this server goes through this
    # attribute instead of subprocess.Popen directly, so a test can replace
    # it and never spawn a real `claude` process (see design.md decision 2).
    popen: object

    def sync_titles(self, store: Store, *, interval: float = 3.0) -> None:
        """Copy session names set with /rename into the ledger, and advance
        every project's run queue.

        Both run from the version poll at most every ``interval`` seconds
        (design.md decision 2's "periodic sync"). A changed title or a
        started task bumps the revision so open dashboards refresh. Only
        unfinished or still unnamed sessions are followed for titles, so an
        imported history is scanned once, not every poll.
        """
        now = time.monotonic()
        if not self._titles_lock.acquire(blocking=False):
            return
        try:
            if now - self._titles_synced_at < interval:
                return
            self._titles_synced_at = now
            for session in store.list_sessions():
                if session["state"] == "ended" and session["title"]:
                    continue
                title = self.titles.title(session["transcript_path"])
                if title and title != session["title"]:
                    store.update_session(session["id"], title=title)
            scheduler.kick(store, self.config, popen=self.popen)
        finally:
            self._titles_lock.release()

    def current_token(self) -> str | None:
        """Return the token to check requests against, re-reading the token
        file (cheaply) so a rotation -- the file deleted or replaced -- takes
        effect on the next request instead of only at process start.
        """
        if self.token_fixed:
            return self.token
        try:
            st = self.config.token_path.stat()
        except OSError:
            self._token_cache_key = None
            self._token_cache_value = None
            return None
        key = (st.st_mtime_ns, st.st_ino)
        if key != self._token_cache_key:
            self._token_cache_value = load_token(self.config, create=False)
            self._token_cache_key = key if self._token_cache_value is not None else None
        return self._token_cache_value


def _backfill_messages(config: Config) -> None:
    with contextlib.suppress(Exception), Store.open(config) as store:
        transcripts.backfill(store, config)


def make_server(
    config: Config,
    host: str = "127.0.0.1",
    port: int | None = None,
    *,
    web_dir: Path | None = None,
    token: str | None = None,
) -> ThreadingHTTPServer:
    bind_port = config.port if port is None else port
    server = _Server((host, bind_port), _Handler)
    server.config = config
    server.web_dir = Path(web_dir) if web_dir is not None else Path(__file__).parent / "web"
    server._token_cache_key = None
    server._token_cache_value = None
    server.popen = subprocess.Popen
    server.titles = TitleWatcher()
    server._titles_lock = threading.Lock()
    server._titles_synced_at = float("-inf")
    if token is not None:
        # Tests pin a token and never touch the file; treat it as the only
        # source of truth so it can't be undercut by a stray file on disk.
        server.token = token
        server.token_fixed = True
    else:
        server.token = load_token(config)
        server.token_fixed = False
    return server


def open_dashboard(config: Config, url: str) -> None:
    """Open the dashboard URL in a browser without the token touching argv.

    webbrowser.open() hands the URL to a browser or xdg-open as a command
    argument, and /proc/<pid>/cmdline is world-readable for the life of that
    process; a same-uid redirect page keeps the token out of it (CWE-214).
    """
    config.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    page_path = config.log_dir / "open.html"
    html = (
        "<!doctype html><meta charset=\"utf-8\">"
        f'<meta http-equiv="refresh" content="0;url={url}">'
        "<title>Tasky</title>"
        f'<a href="{url}">Open Tasky</a>'
    )
    fd = os.open(page_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(html)
    webbrowser.open(page_path.as_uri())


def serve(config: Config, port: int | None = None, open_browser: bool = False) -> int:
    # New files (db, logs, the prompt/log pair a worker writes) must default to
    # private, not to whatever the caller's umask happens to be (CWE-276).
    os.umask(0o077)
    server = make_server(config, port=port)
    with Store.open(config) as store:
        # Right after an upgrade that reset the copy offsets, read every
        # transcript again (and repair) in the background.
        fresh = store.transcript_offset_count() == 0 and store.count_messages() > 0
    if fresh:
        threading.Thread(target=_backfill_messages, args=(config,), daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/#token={server.token}"
    print(f"Tasky dashboard: {url}")
    if open_browser:
        open_dashboard(config, url)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        server.server_close()
    return 0
