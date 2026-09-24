"""Command line interface for Tasky.

Every command talks to the SQLite store directly, so the CLI works without the
dashboard server running and never spends model tokens.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from tasky import __version__
from tasky.config import Config, load_token, token_proof
from tasky.store import TASK_STATUSES, Store

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TASKY_BIN = _REPO_ROOT / "bin" / "tasky"

STATUS_GLYPHS = {
    "running": "▶",
    "queued": "⏸",
    "interrupted": "⚠",
    "failed": "✕",
    "done": "✓",
    "cancelled": "—",
}


def main(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    os.umask(0o077)
    out = out or sys.stdout
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help(out)
        return 2
    args.env = os.environ if env is None else env
    config = Config.from_env(env)
    try:
        return args.handler(args, config, out)
    except (KeyError, ValueError, LookupError) as exc:
        print(f"tasky: {_message(exc)}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasky", description="Visual task ledger for Claude Code sessions."
    )
    parser.add_argument("--version", action="version", version=f"tasky {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="run the dashboard server in the foreground")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    p.set_defaults(handler=_cmd_serve)

    p = sub.add_parser("ui", help="start the dashboard in the background if needed, print its URL")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    p.set_defaults(handler=_cmd_ui)

    p = sub.add_parser("import", help="import past sessions from Claude Code transcripts")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(handler=_cmd_import)

    p = sub.add_parser("add", help="queue a task")
    p.add_argument("text", nargs="+")
    p.add_argument("--cwd", default=None, help="project directory (default: current directory)")
    p.add_argument("--session", default=None, help="bind the task to a session id")
    p.set_defaults(handler=_cmd_add)

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--status", action="append", choices=TASK_STATUSES)
    p.add_argument("--cwd", default=None)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_cmd_list)

    for name, status, help_text in (
        ("done", "done", "mark a task done"),
        ("cancel", "cancelled", "cancel a task"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id", type=int)
        p.set_defaults(handler=_cmd_set_status, status=status)

    p = sub.add_parser("run", help="run a queued task as a headless Claude Code session")
    p.add_argument("id", type=int)
    p.add_argument("--permission-mode", default="default")
    p.add_argument("--mode", default="now", choices=("now", "fork"))
    p.set_defaults(handler=_cmd_run)

    p = sub.add_parser("enqueue", help="add a queued task to its project's run queue")
    p.add_argument("id", type=int)
    p.add_argument("--permission-mode", default="default")
    p.set_defaults(handler=_cmd_enqueue)

    p = sub.add_parser("status", help="print task counters")
    p.add_argument("--short", action="store_true", help="one line, for status lines")
    p.set_defaults(handler=_cmd_status)

    p = sub.add_parser(
        "history", help="sync a repository's problems, attempts and milestones (spends tokens)"
    )
    p.add_argument("--cwd", default=None, help="a folder of the repo (default: current directory)")
    p.add_argument("--repo", default=None, help="repo key, as the dashboard lists it")
    p.add_argument("--model", default=None, help="haiku, sonnet or opus (default: sonnet)")
    p.set_defaults(handler=_cmd_history)

    p = sub.add_parser("mcp", help="serve the history and task ledger as MCP tools on stdio")
    p.set_defaults(handler=_cmd_mcp)

    p = sub.add_parser("hook", help="handle a Claude Code hook event from stdin")
    p.set_defaults(handler=_cmd_hook)

    # Internal: launched detached by tasky.worker.run_task, never by a user.
    p = sub.add_parser("supervise", help=argparse.SUPPRESS)
    p.add_argument("task_id", type=int)
    p.add_argument("session_id")
    p.add_argument("prompt_path")
    p.add_argument("log_path")
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p.set_defaults(handler=_cmd_supervise)
    return parser


def _cmd_serve(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky.server import serve

    return serve(config, port=args.port, open_browser=args.open)


def _cmd_ui(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    port = args.port or config.port
    token = load_token(config)
    url = f"http://127.0.0.1:{port}/#token={token}"
    if not _server_alive(port, token):
        config.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = config.log_dir / "server.log"
        # The server must outlive this command (and the Claude Code turn that
        # may have started it), so it gets its own session and no inherited
        # stdio. It is launched through bin/tasky, never `-m tasky`: the
        # latter puts the caller's cwd first on sys.path, and a cwd that
        # ships its own tasky/ package would shadow this one (CWE-427).
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [sys.executable, str(_TASKY_BIN), "serve", "--port", str(port)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env=dict(os.environ),
            )
        if not _wait_alive(port, token, timeout=5.0):
            print(f"tasky: server did not start, see {log_path}", file=sys.stderr)
            return 1
    print(url, file=out)
    if args.open:
        from tasky.server import open_dashboard

        open_dashboard(config, url)
    return 0


def _cmd_import(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky.importer import import_transcripts

    with Store.open(config) as store:
        report = import_transcripts(store, config, dry_run=args.dry_run)
    prefix = "would import" if args.dry_run else "imported"
    print(
        f"{prefix} {report['tasks']} tasks from {report['sessions']} sessions "
        f"({report['files']} files, {report['skipped_sessions']} skipped, "
        f"{report['bad_lines']} bad lines)",
        file=out,
    )
    return 0


def _cmd_add(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    body = " ".join(args.text).strip()
    if not body:
        raise ValueError("task text is empty")
    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    with Store.open(config) as store:
        task = store.create_task(
            kind="prompt",
            body=body,
            status="queued",
            source="cli",
            cwd=cwd,
            session_id=args.session,
        )
    print(f"queued #{task['id']} {task['title']}", file=out)
    return 0


def _cmd_list(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    cwd = str(Path(args.cwd).resolve()) if args.cwd else None
    with Store.open(config) as store:
        tasks = store.list_tasks(status=args.status, cwd=cwd, limit=args.limit)
    if args.json:
        json.dump(tasks, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 0
    if not tasks:
        print("no tasks", file=out)
        return 0
    for task in tasks:
        glyph = STATUS_GLYPHS.get(task["status"], "?")
        project = f" [{task['project']}]" if task["project"] else ""
        print(f"{glyph} #{task['id']:<5} {task['status']:<11}{project} {task['title']}", file=out)
    return 0


def _cmd_set_status(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    with Store.open(config) as store:
        task = store.update_task(args.id, status=args.status)
    print(f"#{task['id']} {task['status']}", file=out)
    return 0


def _cmd_run(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky.worker import WorkerError, run_task

    with Store.open(config) as store:
        try:
            task = run_task(
                store, config, args.id, permission_mode=args.permission_mode, mode=args.mode
            )
        except WorkerError as exc:
            print(f"tasky: {exc}", file=sys.stderr)
            return 1
    print(f"#{task['id']} running in session {task['session_id']}", file=out)
    return 0


def _cmd_enqueue(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky import scheduler
    from tasky.worker import PERMISSION_MODES

    if args.permission_mode not in PERMISSION_MODES:
        raise ValueError(f"invalid permission mode: {args.permission_mode!r}")
    if args.permission_mode == "bypassPermissions" and not config.allow_bypass:
        raise ValueError("bypassPermissions requires TASKY_ALLOW_BYPASS=1")

    with Store.open(config) as store:
        task = store.get_task(args.id)
        if task is None:
            raise KeyError(args.id)
        if not task.get("cwd"):
            raise ValueError(f"task {args.id} has no cwd")
        enqueued = store.enqueue_task(args.id, args.permission_mode, None)
        if enqueued is None:
            raise ValueError(f"task {args.id} is not queued")
        scheduler.kick(store, config, enqueued["cwd"])
        task = store.get_task(args.id)
    print(f"#{task['id']} in {task['cwd']} run queue (status: {task['status']})", file=out)
    return 0


def _cmd_status(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    with Store.open(config) as store:
        counts = {
            status: len(store.list_tasks(status=status))
            for status in ("running", "queued", "interrupted", "failed")
        }
    attention = counts["interrupted"] + counts["failed"]
    if args.short:
        print(f"▶{counts['running']} ⏸{counts['queued']} ⚠{attention}", file=out)
    else:
        print(
            f"running {counts['running']}, queued {counts['queued']}, "
            f"needs attention {attention}",
            file=out,
        )
    return 0


def _cmd_history(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky import history

    try:
        if args.repo:
            summary = history.sync(config, args.repo, model=args.model)
        else:
            cwd = os.path.abspath(args.cwd or os.getcwd())
            summary = history.sync_folder(config, cwd, model=args.model)
    except history.SyncError as exc:
        print(f"tasky: {exc}", file=sys.stderr)
        return 1
    print(
        f"{summary['tasks']} task(s) read in {summary['batches']} batch(es): "
        f"{summary['added']} record(s) added, {summary['updated']} updated, "
        f"{summary['model']}, ${summary['cost_usd']:.4f}, "
        f"{summary['pending']} task(s) still to sync",
        file=out,
    )
    if summary["error"]:
        print(f"tasky: sync stopped: {summary['error']}", file=sys.stderr)
        return 1
    return 0


def _cmd_mcp(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky.mcp import serve

    return serve(config, sys.stdin, out)


def _cmd_hook(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky.hooks import main as hook_main

    return hook_main(sys.stdin, out, args.env)


def _cmd_supervise(args: argparse.Namespace, config: Config, out: TextIO) -> int:
    from tasky.supervise import supervise

    return supervise(
        config, args.task_id, args.session_id, args.prompt_path, args.log_path, args.argv
    )


def _server_alive(port: int, token: str) -> bool:
    # Sends only the nonce, never the token: whatever is listening on the port
    # has not been verified as the real server yet, so it must not learn the
    # token just by being asked "are you alive" (CWE-522). The listener has to
    # answer with an HMAC proof bound to this token and this port before the
    # caller ever hands it anything secret.
    nonce = secrets.token_hex(16)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/version",
            headers={"X-Tasky-Nonce": nonce},
        )
        with urllib.request.urlopen(request, timeout=0.5) as response:  # noqa: S310
            if response.status != 200:
                return False
            payload = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError):
        return False
    proof = payload.get("proof") if isinstance(payload, dict) else None
    return isinstance(proof, str) and hmac.compare_digest(proof, token_proof(token, nonce, port))


def _wait_alive(port: int, token: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _server_alive(port, token):
            return True
        time.sleep(0.1)
    return False


def _message(exc: BaseException) -> str:
    # KeyError wraps its message in quotes; unwrap it for a readable CLI error.
    if isinstance(exc, KeyError) and exc.args:
        return f"not found: {exc.args[0]}"
    return str(exc)
