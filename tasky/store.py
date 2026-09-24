"""SQLite-backed ledger of sessions and tasks."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from tasky.config import Config, now_iso
from tasky.prompts import make_title

TASK_STATUSES = ("queued", "running", "done", "failed", "interrupted", "cancelled")
TERMINAL = ("done", "failed", "interrupted", "cancelled")
TASK_KINDS = ("prompt", "delegation")

SEARCH_LIMIT = 50
SEARCH_MAX_WORDS = 8
_MILESTONE_FIELDS = ("topic", "title", "detail", "happened_on", "task_ids", "commits")
_PROBLEM_FIELDS = (
    "topic", "title", "symptom", "cause", "state", "first_seen", "last_seen", "task_ids",
)
_ATTEMPT_FIELDS = (
    "description", "outcome", "why", "evidence", "believed_from", "invalidated_on",
    "task_ids", "commits",
)

_SCHEMA_VERSION = 4
HISTORY_FTS_REBUILD = """
DELETE FROM history_fts;
INSERT INTO history_fts (kind, ref_id, text)
  SELECT 'problem', id, title || ' ' || symptom || ' ' || cause || ' ' || COALESCE(topic, '')
  FROM problems;
INSERT INTO history_fts (kind, ref_id, text)
  SELECT 'attempt', id, description || ' ' || why || ' ' || evidence FROM attempts;
INSERT INTO history_fts (kind, ref_id, text)
  SELECT 'milestone', id, title || ' ' || detail || ' ' || COALESCE(topic, '') FROM milestones;
"""
_INIT_TIMEOUT_S = 10.0
_SESSION_FIELDS = ("title", "auto_pull", "pull_chain", "state")
_SESSION_STATES = ("active", "ended")
_LIST_TASKS_ORDER = (
    "ORDER BY CASE WHEN status = 'queued' THEN 0 ELSE 1 END, "
    "CASE WHEN status = 'queued' THEN position END ASC, "
    "CASE WHEN status != 'queued' THEN COALESCE(started_at, created_at) END DESC"
)
_LIKE_SPECIALS = re.compile(r"[\\%_]")
_TASK_UPDATE_FIELDS = (
    "title",
    "body",
    "status",
    "result",
    "session_id",
    "prompt_id",
    "agent_id",
    "parent_id",
    "position",
    "started_at",
    "finished_at",
    "lane",
    "run_mode",
    "permission_mode",
    "fork_of",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, cwd TEXT, transcript_path TEXT, title TEXT,
  started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',
  auto_pull INTEGER NOT NULL DEFAULT 0, pull_chain INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'hook'
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT, parent_id INTEGER, kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  result TEXT, cwd TEXT, prompt_id TEXT, external_id TEXT UNIQUE, agent_id TEXT,
  source TEXT NOT NULL,
  position REAL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  lane TEXT, run_mode TEXT, permission_mode TEXT, fork_of TEXT
);
CREATE TABLE IF NOT EXISTS lanes (
  cwd TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, reason TEXT
);
CREATE TABLE IF NOT EXISTS repos (
  cwd TEXT PRIMARY KEY, repo TEXT NOT NULL, name TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS milestones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cwd TEXT NOT NULL, topic TEXT, title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '', happened_on TEXT, task_ids TEXT NOT NULL DEFAULT '[]',
  commits TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cwd TEXT NOT NULL, topic TEXT, title TEXT NOT NULL,
  symptom TEXT NOT NULL DEFAULT '', cause TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'open', first_seen TEXT, last_seen TEXT,
  task_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, problem_id INTEGER NOT NULL, seq INTEGER NOT NULL,
  description TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'pending',
  why TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
  believed_from TEXT, invalidated_on TEXT, task_ids TEXT NOT NULL DEFAULT '[]',
  commits TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'sync',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history_cursors (
  cwd TEXT PRIMARY KEY, last_task_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS history_syncs (
  repo TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'idle', started_at TEXT, finished_at TEXT,
  error TEXT, model TEXT, last_cost_usd REAL NOT NULL DEFAULT 0,
  total_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
  kind UNINDEXED, ref_id UNINDEXED, text, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE INDEX IF NOT EXISTS idx_milestones_cwd ON milestones(cwd);
CREATE INDEX IF NOT EXISTS idx_problems_cwd ON problems(cwd);
CREATE INDEX IF NOT EXISTS idx_attempts_problem ON attempts(problem_id);
CREATE INDEX IF NOT EXISTS idx_tasks_session_id ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_prompt_id ON tasks(prompt_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_id ON tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_cwd ON tasks(cwd);

CREATE TRIGGER IF NOT EXISTS trg_tasks_rev_ins AFTER INSERT ON tasks BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_tasks_rev_upd AFTER UPDATE ON tasks BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_milestones_rev_ins AFTER INSERT ON milestones BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_milestones_rev_upd AFTER UPDATE ON milestones BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_milestones_rev_del AFTER DELETE ON milestones BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_problems_rev_ins AFTER INSERT ON problems BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_problems_rev_upd AFTER UPDATE ON problems BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_problems_rev_del AFTER DELETE ON problems BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_attempts_rev_ins AFTER INSERT ON attempts BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_attempts_rev_upd AFTER UPDATE ON attempts BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_attempts_rev_del AFTER DELETE ON attempts BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_history_syncs_rev_ins AFTER INSERT ON history_syncs BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_history_syncs_rev_upd AFTER UPDATE ON history_syncs BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_history_syncs_rev_del AFTER DELETE ON history_syncs BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_tasks_rev_del AFTER DELETE ON tasks BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_sessions_rev_ins AFTER INSERT ON sessions BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_sessions_rev_upd AFTER UPDATE ON sessions BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_sessions_rev_del AFTER DELETE ON sessions BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_lanes_rev_ins AFTER INSERT ON lanes BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_lanes_rev_upd AFTER UPDATE ON lanes BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
CREATE TRIGGER IF NOT EXISTS trg_lanes_rev_del AFTER DELETE ON lanes BEGIN
  UPDATE meta SET value = value + 1 WHERE key = 'rev';
END;
"""


def _split_script(script: str) -> list[str]:
    """Split the schema into statements, keeping trigger bodies intact."""
    statements: list[str] = []
    current: list[str] = []
    for line in script.strip().splitlines():
        current.append(line)
        candidate = "\n".join(current).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            current = []
    return statements


_TOKEN_RE = re.compile(r"#token=[A-Za-z0-9_-]+")


def _clean_result(value: str | None, limit: int) -> str | None:
    """Redact dashboard access links, then cap the length.

    Every writer (hooks, workers, importer, API) stores results through here, so an agent
    that prints the `tasky ui` link never leaves the token in the ledger.
    """
    if value is not None and "#token=" in value:
        value = _TOKEN_RE.sub("#token=<redacted>", value)
    return _truncate(value, limit)


def _parse_iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    suffix = "\n…[truncated]"
    keep = max(limit - len(suffix), 0)
    return value[:keep] + suffix


class Store:
    def __init__(self, db_path: Path, max_result: int = 8000) -> None:
        self.db_path = Path(db_path)
        self.max_result = max_result
        # The ledger holds prompt text and results: keep it private to the user.
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self._conn.execute("PRAGMA user_version").fetchone()[0] < _SCHEMA_VERSION:
            self._initialize()
        with contextlib.suppress(OSError):
            os.chmod(self.db_path, 0o600)

    def _initialize(self) -> None:
        # Several hook processes can open a brand-new database at the same
        # moment. Switching to WAL needs an exclusive lock that busy_timeout
        # does not always wait for, so retry for a bounded time instead of
        # losing the event.
        deadline = time.monotonic() + _INIT_TIMEOUT_S
        while True:
            try:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("BEGIN IMMEDIATE")
                if self._conn.execute("PRAGMA user_version").fetchone()[0] < _SCHEMA_VERSION:
                    for statement in _split_script(_SCHEMA):
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT OR IGNORE INTO meta (key, value) VALUES ('rev', 0)"
                    )
                    self._migrate_v1_to_v2()
                    self._migrate_v3_to_v4()
                    self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                self._conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if "locked" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _migrate_v1_to_v2(self) -> None:
        """Add the version-2 columns to an existing ``tasks`` table.

        A no-op on a fresh database (the ``CREATE TABLE`` above already
        includes them) and safe to run more than once: each column is only
        added if ``PRAGMA table_info`` does not already list it.
        """
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        for column in ("lane", "run_mode", "permission_mode", "fork_of"):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")  # noqa: S608

    def _migrate_v3_to_v4(self) -> None:
        """Move 0.5.0 history records into problems, attempts and milestones.

        A 0.5.0 ``problem`` keeps its solution as an attempt; a ``dead_end``
        becomes a problem whose first attempt failed. The per-folder cursor
        carries over so a sync does not re-read what 0.5.0 already read.
        """
        tables = {
            row[0]
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "insights" not in tables:
            return
        now = now_iso()
        rows = self._conn.execute("SELECT * FROM insights ORDER BY id").fetchall()
        for r in rows:
            if r["kind"] == "milestone":
                self._conn.execute(
                    "INSERT INTO milestones (cwd, title, detail, happened_on, task_ids, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["cwd"], r["title"], r["detail"], r["happened_on"], r["task_ids"], now, now),
                )
                continue
            dead_end = r["kind"] == "dead_end"
            state = "solved" if r["state"] == "solved" else "open"
            cursor = self._conn.execute(
                "INSERT INTO problems (cwd, title, cause, state, first_seen, last_seen, task_ids, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["cwd"], r["title"], "" if dead_end else r["detail"], state,
                    r["happened_on"], r["happened_on"], r["task_ids"], now, now,
                ),
            )
            problem_id = cursor.lastrowid
            attempts = []
            if dead_end:
                attempts.append((r["title"], "failed", r["detail"]))
            if r["solution"]:
                verdict = "worked" if dead_end or state == "solved" else "pending"
                attempts.append((r["solution"], verdict, ""))
            for seq, (description, outcome, why) in enumerate(attempts, start=1):
                self._conn.execute(
                    "INSERT INTO attempts (problem_id, seq, description, outcome, why, "
                    "believed_from, task_ids, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (problem_id, seq, description, outcome, why, r["happened_on"], r["task_ids"],
                     now, now),
                )
        if "insight_syncs" in tables:
            self._conn.execute(
                "INSERT OR IGNORE INTO history_cursors (cwd, last_task_id) "
                "SELECT cwd, last_task_id FROM insight_syncs"
            )
            self._conn.execute("DROP TABLE insight_syncs")
        self._conn.execute("DROP TABLE insights")
        for statement in _split_script(HISTORY_FTS_REBUILD):
            self._conn.execute(statement)

    @classmethod
    def open(cls, config: Config) -> Store:
        return cls(config.db_path, max_result=config.max_result)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def rev(self) -> int:
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'rev'").fetchone()
        return int(row["value"]) if row else 0

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @classmethod
    def _task_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        task = cls._row(row)
        cwd = task.get("cwd") or ""
        task["project"] = Path(cwd).name if cwd else ""
        return task

    def _next_position(self) -> float:
        row = self._conn.execute("SELECT MAX(position) AS m FROM tasks").fetchone()
        maximum = row["m"]
        return (maximum + 1) if maximum is not None else 0.0

    # -- sessions ---------------------------------------------------------

    def upsert_session(
        self,
        session_id: str,
        *,
        cwd: str | None = None,
        transcript_path: str | None = None,
        source: str = "hook",
        at: str | None = None,
    ) -> dict:
        at = at or now_iso()
        existing = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if existing is None:
            self._conn.execute(
                "INSERT INTO sessions "
                "(id, cwd, transcript_path, title, started_at, last_seen_at, state, "
                "auto_pull, pull_chain, source) "
                "VALUES (?, ?, ?, NULL, ?, ?, 'active', 0, 0, ?)",
                (session_id, cwd, transcript_path, at, at, source),
            )
        else:
            new_cwd = cwd if cwd is not None else existing["cwd"]
            new_transcript = (
                transcript_path if transcript_path is not None else existing["transcript_path"]
            )
            self._conn.execute(
                "UPDATE sessions SET cwd = ?, transcript_path = ?, last_seen_at = ?, "
                "state = 'active' WHERE id = ?",
                (new_cwd, new_transcript, at, session_id),
            )
        self._conn.commit()
        session = self.get_session(session_id)
        assert session is not None
        return session

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row(row) if row else None

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY last_seen_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def update_session(self, session_id: str, **fields: Any) -> dict:
        if self.get_session(session_id) is None:
            raise KeyError(session_id)
        unknown = set(fields) - set(_SESSION_FIELDS)
        if unknown:
            raise ValueError(f"unknown session field(s): {', '.join(sorted(unknown))}")
        if "state" in fields and fields["state"] not in _SESSION_STATES:
            raise ValueError(f"invalid session state: {fields['state']!r}")
        if fields:
            items = list(fields.items())
            # identifiers come from the _SESSION_FIELDS allowlist checked above; values are bound
            columns = ", ".join(f"{key} = ?" for key, _ in items)  # noqa: S608
            values = [
                int(v) if key == "auto_pull" and isinstance(v, bool) else v for key, v in items
            ]
            self._conn.execute(
                f"UPDATE sessions SET {columns} WHERE id = ?",  # noqa: S608
                (*values, session_id),
            )
            self._conn.commit()
        session = self.get_session(session_id)
        assert session is not None
        return session

    def end_session(self, session_id: str, at: str | None = None) -> int:
        at = at or now_iso()
        cursor = self._conn.execute(
            "UPDATE tasks SET status = 'interrupted', finished_at = ? "
            "WHERE session_id = ? AND status = 'running'",
            (at, session_id),
        )
        changed = cursor.rowcount
        self._conn.execute(
            "UPDATE sessions SET state = 'ended', last_seen_at = ? WHERE id = ?",
            (at, session_id),
        )
        self._conn.commit()
        return changed

    # -- tasks --------------------------------------------------------------

    def create_task(
        self,
        *,
        kind: str,
        body: str,
        status: str,
        source: str,
        cwd: str | None = None,
        session_id: str | None = None,
        parent_id: int | None = None,
        title: str | None = None,
        prompt_id: str | None = None,
        external_id: str | None = None,
        agent_id: str | None = None,
        result: str | None = None,
        created_at: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict:
        if kind not in TASK_KINDS:
            raise ValueError(f"invalid task kind: {kind!r}")
        if status not in TASK_STATUSES:
            raise ValueError(f"invalid task status: {status!r}")

        now = now_iso()
        created_at = created_at or now
        if status == "running" and started_at is None:
            started_at = now
        if status in TERMINAL and finished_at is None:
            finished_at = now
        if title is None:
            title = make_title(body)
        result = _clean_result(result, self.max_result)

        # Queued tasks need position read-then-inserted atomically: two callers
        # racing on _next_position() would otherwise both land on the same slot.
        needs_lock = status == "queued" and not self._conn.in_transaction
        if needs_lock:
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            position = self._next_position() if status == "queued" else None
            cursor = self._conn.execute(
                "INSERT INTO tasks "
                "(session_id, parent_id, kind, title, body, status, result, cwd, prompt_id, "
                "external_id, agent_id, source, position, created_at, started_at, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    parent_id,
                    kind,
                    title,
                    body,
                    status,
                    result,
                    cwd,
                    prompt_id,
                    external_id,
                    agent_id,
                    source,
                    position,
                    created_at,
                    started_at,
                    finished_at,
                ),
            )
        except sqlite3.IntegrityError:
            # external_id UNIQUE conflict: another writer created it first. Roll back so
            # the failed INSERT does not leave a write transaction open on this
            # connection (it would otherwise still hold the lock), then return the
            # existing row unchanged.
            if self._conn.in_transaction:
                self._conn.rollback()
            existing = self.get_task_by_external_id(external_id) if external_id else None
            if existing is not None:
                return existing
            raise
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        self._conn.commit()
        task = self.get_task(cursor.lastrowid)
        assert task is not None
        return task

    def get_task(self, task_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_row(row) if row else None

    def get_task_by_external_id(self, external_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE external_id = ?", (external_id,)
        ).fetchone()
        return self._task_row(row) if row else None

    def find_task_by_agent_id(self, agent_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE agent_id = ? ORDER BY id DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        return self._task_row(row) if row else None

    def update_task(self, task_id: int, **fields: Any) -> dict:
        current = self.get_task(task_id)
        if current is None:
            raise KeyError(task_id)
        unknown = set(fields) - set(_TASK_UPDATE_FIELDS)
        if unknown:
            raise ValueError(f"unknown task field(s): {', '.join(sorted(unknown))}")
        if "status" in fields and fields["status"] not in TASK_STATUSES:
            raise ValueError(f"invalid task status: {fields['status']!r}")

        fields = dict(fields)
        if "result" in fields:
            fields["result"] = _clean_result(fields["result"], self.max_result)

        new_status = fields.get("status")
        moving_to_queued = new_status == "queued" and new_status != current["status"]
        if new_status is not None and new_status != current["status"]:
            if new_status == "running" and "started_at" not in fields:
                fields["started_at"] = now_iso()
            elif new_status in TERMINAL and "finished_at" not in fields:
                fields["finished_at"] = now_iso()
            elif moving_to_queued:
                fields["started_at"] = None
                fields["finished_at"] = None

        if not fields:
            task = self.get_task(task_id)
            assert task is not None
            return task

        # The tail position is read-then-written atomically: two concurrent
        # moves to "queued" would otherwise both land on the same slot.
        needs_lock = moving_to_queued and not self._conn.in_transaction
        if needs_lock:
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            if moving_to_queued:
                fields["position"] = self._next_position()
            items = list(fields.items())
            # identifiers come from the _TASK_UPDATE_FIELDS allowlist checked above
            columns = ", ".join(f"{key} = ?" for key, _ in items)  # noqa: S608
            values = [v for _, v in items]
            self._conn.execute(
                f"UPDATE tasks SET {columns} WHERE id = ?",  # noqa: S608
                (*values, task_id),
            )
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        self._conn.commit()
        task = self.get_task(task_id)
        assert task is not None
        return task

    def delete_task(self, task_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def list_tasks(
        self,
        *,
        status: str | Iterable[str] | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")  # noqa: S608 -- placeholders only
            params.extend(statuses)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if cwd is not None:
            clauses.append("cwd = ?")
            params.append(cwd)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # clauses are built from a fixed set of column comparisons above; every value is bound
        sql = "SELECT * FROM tasks " + where + " " + _LIST_TASKS_ORDER  # noqa: S608
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._task_row(r) for r in rows]

    def search_tasks(
        self, query: str, *, cwd: str | None = None, limit: int = SEARCH_LIMIT
    ) -> list[dict]:
        """Tasks whose title, prompt or reply holds every word of ``query``.

        Reaches the whole ledger, not just the slice ``state()`` ships, so old
        answers can be found without asking the model again. Matching is a
        case-insensitive substring per word (SQLite ``LIKE``: ASCII case only).
        """
        words = query.split()[:SEARCH_MAX_WORDS]
        if not words:
            return []
        clauses = []
        params: list[Any] = []
        for word in words:
            pattern = "%" + _LIKE_SPECIALS.sub(r"\\\g<0>", word) + "%"
            clauses.append(
                "(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' "
                "OR COALESCE(result, '') LIKE ? ESCAPE '\\')"
            )
            params.extend((pattern, pattern, pattern))
        if cwd is not None:
            clauses.append("cwd = ?")
            params.append(cwd)
        # clauses are fixed LIKE comparisons, one per word; every value is bound
        sql = (
            "SELECT * FROM tasks WHERE "  # noqa: S608
            + " AND ".join(clauses)
            + " ORDER BY COALESCE(finished_at, started_at, created_at) DESC, id DESC LIMIT ?"
        )
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._task_row(r) for r in rows]

    def latest_running_task(
        self,
        session_id: str,
        prompt_id: str | None = None,
        kind: str | None = "prompt",
    ) -> dict | None:
        clauses = ["session_id = ?", "status = 'running'"]
        params: list[Any] = [session_id]
        if prompt_id is not None:
            clauses.append("prompt_id = ?")
            params.append(prompt_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        # clauses are built from a fixed set of column comparisons above; every value is bound
        sql = (
            "SELECT * FROM tasks WHERE " + " AND ".join(clauses)  # noqa: S608
            + " ORDER BY started_at DESC, id DESC LIMIT 1"
        )
        row = self._conn.execute(sql, params).fetchone()
        return self._task_row(row) if row else None

    def latest_prompt_task(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? AND kind = 'prompt' ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._task_row(row) if row else None

    def has_children(self, parent_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tasks WHERE parent_id = ? LIMIT 1", (parent_id,)
        ).fetchone()
        return row is not None

    def running_children(self, parent_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE parent_id = ? AND status = 'running' "
            "AND kind = 'delegation'",
            (parent_id,),
        ).fetchall()
        return [self._task_row(r) for r in rows]

    def claim_task(
        self, task_id: int, *, expected_status: str = "queued", **fields: Any
    ) -> dict | None:
        """Apply ``update_task`` only if the task still has ``expected_status``.

        The check and the write share one ``BEGIN IMMEDIATE`` transaction, so two
        processes racing for the same queued task cannot both win. Returns the
        updated task, or ``None`` when another writer got there first.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.get_task(task_id)
            if current is None or current["status"] != expected_status:
                self._conn.rollback()
                return None
            return self.update_task(task_id, **fields)
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def interrupt_running(
        self, session_id: str, *, kind: str = "prompt", exclude_ids: Iterable[int] = ()
    ) -> int:
        """Mark running tasks of a session ``interrupted`` unless a delegation still runs."""
        excluded = set(exclude_ids)
        count = 0
        for task in self.list_tasks(status="running", session_id=session_id, kind=kind):
            if task["id"] in excluded or self.running_children(task["id"]):
                continue
            self.update_task(task["id"], status="interrupted")
            count += 1
        return count

    def interrupt_session(self, session_id: str) -> int:
        """Mark every running task of a session ``interrupted``, delegations included.

        Unlike ``interrupt_running``, this does not skip a task with running
        children: it is for a session whose own process is gone (resume), so
        nothing will ever finish those children either.
        """
        cursor = self._conn.execute(
            "UPDATE tasks SET status = 'interrupted', finished_at = ? "
            "WHERE session_id = ? AND status = 'running'",
            (now_iso(), session_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def session_has_tasks_not_from(self, session_id: str, source: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tasks WHERE session_id = ? AND source != ? LIMIT 1",
            (session_id, source),
        ).fetchone()
        return row is not None

    def next_queued(self, session_id: str | None, cwd: str | None) -> dict | None:
        # A task in a project's run queue (lane='serial') belongs to the
        # scheduler only; auto-pull must never take it.
        if session_id is not None:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' AND lane IS NULL AND session_id = ? "
                "ORDER BY position ASC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is not None:
                return self._task_row(row)
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE status = 'queued' AND lane IS NULL "
            "AND session_id IS NULL AND cwd = ? ORDER BY position ASC LIMIT 1",
            (cwd,),
        ).fetchone()
        return self._task_row(row) if row else None

    @staticmethod
    def _renumber(order: list[int]) -> list[tuple[float, int]]:
        return [(float(position), tid) for position, tid in enumerate(order)]

    def move_task(self, task_id: int, before_id: int | None) -> dict:
        # Read the queued order and renumber it atomically: two concurrent moves
        # reading the same order would otherwise both compute conflicting positions.
        # Ordering is scoped to the task's own lane (Inbox or a project's run
        # queue): the two are independent columns on the board.
        needs_lock = not self._conn.in_transaction
        if needs_lock:
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            task = self.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status = 'queued' AND lane IS ? ORDER BY position ASC",
                (task["lane"],),
            ).fetchall()
            order = [r["id"] for r in rows if r["id"] != task_id]
            if before_id is None:
                order.append(task_id)
            else:
                if before_id not in order:
                    raise KeyError(before_id)
                order.insert(order.index(before_id), task_id)

            for position, tid in self._renumber(order):
                self._conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (position, tid))
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        self._conn.commit()
        task = self.get_task(task_id)
        assert task is not None
        return task

    # -- lanes ---------------------------------------------------------------

    def get_lane(self, cwd: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM lanes WHERE cwd = ?", (cwd,)).fetchone()
        return self._row(row) if row else None

    def set_lane(self, cwd: str, *, paused: bool, reason: str | None = None) -> dict:
        self._conn.execute(
            "INSERT OR REPLACE INTO lanes (cwd, paused, reason) VALUES (?, ?, ?)",
            (cwd, int(paused), reason),
        )
        self._conn.commit()
        lane = self.get_lane(cwd)
        assert lane is not None
        return lane

    def list_lanes(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM lanes ORDER BY cwd").fetchall()
        return [self._row(r) for r in rows]

    def serial_lane_cwds(self) -> list[str]:
        """Every directory with a queued task in its run queue."""
        rows = self._conn.execute(
            "SELECT DISTINCT cwd FROM tasks WHERE status = 'queued' AND lane = 'serial' "
            "AND cwd IS NOT NULL"
        ).fetchall()
        return [r["cwd"] for r in rows]

    def claim_serial_head(self, cwd: str, session_id: str) -> dict | None:
        """Claim the head of ``cwd``'s run queue, or ``None`` if it cannot start now.

        Checks "not paused" and "no running task already started from this
        queue" and claims the lowest-position queued task in one
        ``BEGIN IMMEDIATE`` transaction, closing the race where two kicks
        both see an idle queue and claim different tasks.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            lane_row = self._conn.execute(
                "SELECT paused FROM lanes WHERE cwd = ?", (cwd,)
            ).fetchone()
            if lane_row is not None and lane_row["paused"]:
                self._conn.rollback()
                return None
            running = self._conn.execute(
                "SELECT 1 FROM tasks WHERE cwd = ? AND status = 'running' "
                "AND run_mode = 'serial' LIMIT 1",
                (cwd,),
            ).fetchone()
            if running is not None:
                self._conn.rollback()
                return None
            head = self._conn.execute(
                "SELECT id FROM tasks WHERE cwd = ? AND status = 'queued' AND lane = 'serial' "
                "ORDER BY position ASC LIMIT 1",
                (cwd,),
            ).fetchone()
            if head is None:
                self._conn.rollback()
                return None
            # update_task() commits, which closes this transaction too.
            return self.update_task(
                head["id"], status="running", session_id=session_id, run_mode="serial"
            )
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def enqueue_task(
        self, task_id: int, permission_mode: str, before_id: int | None = None
    ) -> dict | None:
        """Move a queued task into its project's run queue at a given place.

        Returns ``None`` if the task is not currently ``queued`` (the caller
        should answer 409). Raises ``KeyError`` if ``before_id`` is not a
        queued task already in that run queue.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            task = self.get_task(task_id)
            if task is None or task["status"] != "queued":
                self._conn.rollback()
                return None
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status = 'queued' AND lane = 'serial' AND cwd = ? "
                "ORDER BY position ASC",
                (task["cwd"],),
            ).fetchall()
            order = [r["id"] for r in rows if r["id"] != task_id]
            if before_id is None:
                order.append(task_id)
            else:
                if before_id not in order:
                    raise KeyError(before_id)
                order.insert(order.index(before_id), task_id)

            for position, tid in self._renumber(order):
                self._conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (position, tid))
            self._conn.execute(
                "UPDATE tasks SET lane = 'serial', permission_mode = ? WHERE id = ?",
                (permission_mode, task_id),
            )
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        self._conn.commit()
        return self.get_task(task_id)

    def clear_task_lane(self, task_id: int) -> dict | None:
        """Send a queued task from a run queue back to Inbox (``lane = NULL``).

        Returns ``None`` if the task is not currently ``queued``.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            task = self.get_task(task_id)
            if task is None or task["status"] != "queued":
                self._conn.rollback()
                return None
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status = 'queued' AND lane IS NULL "
                "ORDER BY position ASC"
            ).fetchall()
            order = [r["id"] for r in rows if r["id"] != task_id]
            order.append(task_id)
            for position, tid in self._renumber(order):
                self._conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (position, tid))
            self._conn.execute("UPDATE tasks SET lane = NULL WHERE id = ?", (task_id,))
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        self._conn.commit()
        return self.get_task(task_id)

    def latest_session_for_cwd(self, cwd: str) -> dict | None:
        row = self._conn.execute(
            # Only sessions with a transcript can be resumed. A session the user
            # drove is preferred over a headless worker's: cloning a worker gives
            # that one task's context, not the conversation the user built.
            "SELECT * FROM sessions WHERE cwd = ? AND transcript_path IS NOT NULL "
            "ORDER BY (source = 'worker') ASC, last_seen_at DESC LIMIT 1",
            (cwd,),
        ).fetchone()
        return self._row(row) if row else None

    # -- project history ----------------------------------------------------
    #
    # Records belong to the folder whose tasks they came from; a repository
    # is the set of folders ``repos`` maps to one key (see tasky.repos).

    def repo_row(self, cwd: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM repos WHERE cwd = ?", (cwd,)).fetchone()
        return self._row(row) if row else None

    def set_repo(self, cwd: str, repo: str, name: str, checked_at: str) -> None:
        self._conn.execute(
            "INSERT INTO repos (cwd, repo, name, checked_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(cwd) DO UPDATE SET repo = excluded.repo, name = excluded.name, "
            "checked_at = excluded.checked_at",
            (cwd, repo, name, checked_at),
        )
        self._conn.commit()

    def task_cwds(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT cwd, MAX(COALESCE(finished_at, started_at, created_at)) AS last FROM tasks "
            "WHERE cwd IS NOT NULL AND cwd != '' GROUP BY cwd ORDER BY last DESC"
        ).fetchall()
        return [r["cwd"] for r in rows]

    def repo_cwds(self, repo: str) -> list[str]:
        rows = self._conn.execute("SELECT cwd FROM repos WHERE repo = ?", (repo,)).fetchall()
        return [r["cwd"] for r in rows]

    def repo_list(self) -> list[dict]:
        """Every repo with recorded tasks, most recently active first."""
        order = {cwd: i for i, cwd in enumerate(self.task_cwds())}
        repos: dict[str, dict] = {}
        for r in self._conn.execute("SELECT * FROM repos").fetchall():
            if r["cwd"] not in order:
                continue
            entry = repos.setdefault(
                r["repo"],
                {"repo": r["repo"], "name": r["name"], "cwds": [], "rank": order[r["cwd"]]},
            )
            entry["cwds"].append(r["cwd"])
            entry["rank"] = min(entry["rank"], order[r["cwd"]])
        result = sorted(repos.values(), key=lambda e: e["rank"])
        for entry in result:
            del entry["rank"]
            entry["cwds"].sort(key=lambda c: order[c])
        return result

    @staticmethod
    def _ids(raw: str | None) -> list:
        try:
            value = json.loads(raw or "[]")
        except ValueError:
            return []
        return value if isinstance(value, list) else []

    def _history_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = self._row(row)
        item["task_ids"] = [
            i
            for i in self._ids(item.get("task_ids"))
            if isinstance(i, int) and not isinstance(i, bool)
        ]
        if "commits" in item:
            item["commits"] = [c for c in self._ids(item["commits"]) if isinstance(c, str)]
        return item

    def _scope_sql(self, repo: str | None) -> tuple[str, list[Any]]:
        if repo is None:
            return "", []
        return " WHERE cwd IN (SELECT cwd FROM repos WHERE repo = ?)", [repo]

    def _names(self) -> dict[str, tuple[str, str]]:
        return {
            r["cwd"]: (r["repo"], r["name"])
            for r in self._conn.execute("SELECT cwd, repo, name FROM repos").fetchall()
        }

    def _label(self, item: dict, names: dict[str, tuple[str, str]]) -> dict:
        repo, name = names.get(item["cwd"], (f"path:{item['cwd']}", Path(item["cwd"]).name))
        item["repo"] = repo
        item["name"] = name
        return item

    def milestones(self, repo: str | None) -> list[dict]:
        where, params = self._scope_sql(repo)
        rows = self._conn.execute(
            "SELECT * FROM milestones" + where  # noqa: S608 - fixed clause, values bound
            + " ORDER BY COALESCE(happened_on, '') ASC, id ASC",
            params,
        ).fetchall()
        names = self._names()
        return [self._label(self._history_row(r), names) for r in rows]

    def problems(self, repo: str | None) -> list[dict]:
        where, params = self._scope_sql(repo)
        rows = self._conn.execute(
            "SELECT * FROM problems" + where  # noqa: S608 - fixed clause, values bound
            + " ORDER BY COALESCE(last_seen, first_seen, '') DESC, id DESC",
            params,
        ).fetchall()
        names = self._names()
        problems = [self._label(self._history_row(r), names) for r in rows]
        by_id = {p["id"]: p for p in problems}
        for p in problems:
            p["attempts"] = []
        if by_id:
            marks = ", ".join("?" for _ in by_id)
            for a in self._conn.execute(
                f"SELECT * FROM attempts WHERE problem_id IN ({marks}) ORDER BY seq, id",  # noqa: S608
                list(by_id),
            ).fetchall():
                by_id[a["problem_id"]]["attempts"].append(self._history_row(a))
        return problems

    def get_problem(self, problem_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
        if row is None:
            return None
        problem = self._label(self._history_row(row), self._names())
        problem["attempts"] = [
            self._history_row(a)
            for a in self._conn.execute(
                "SELECT * FROM attempts WHERE problem_id = ? ORDER BY seq, id", (problem_id,)
            ).fetchall()
        ]
        return problem

    def get_milestone(self, milestone_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM milestones WHERE id = ?", (milestone_id,)
        ).fetchone()
        return self._label(self._history_row(row), self._names()) if row else None

    def get_attempt(self, attempt_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        return self._history_row(row) if row else None

    def _reindex(self, kind: str, ref_id: int) -> None:
        self._conn.execute(
            "DELETE FROM history_fts WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        )
        if kind == "problem":
            sql = ("SELECT title || ' ' || symptom || ' ' || cause || ' ' || COALESCE(topic, '') "
                   "FROM problems WHERE id = ?")
        elif kind == "attempt":
            sql = "SELECT description || ' ' || why || ' ' || evidence FROM attempts WHERE id = ?"
        else:
            sql = ("SELECT title || ' ' || detail || ' ' || COALESCE(topic, '') "
                   "FROM milestones WHERE id = ?")
        row = self._conn.execute(sql, (ref_id,)).fetchone()
        if row is not None:
            self._conn.execute(
                "INSERT INTO history_fts (kind, ref_id, text) VALUES (?, ?, ?)",
                (kind, ref_id, row[0]),
            )

    def _insert(self, table: str, fields: dict[str, Any]) -> int:
        now = now_iso()
        fields = {**fields, "created_at": now, "updated_at": now}
        for key in ("task_ids", "commits"):
            if key in fields:
                fields[key] = json.dumps(list(dict.fromkeys(fields[key])))
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        # table and column names come from the fixed callers below; values are bound
        cursor = self._conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})",  # noqa: S608
            list(fields.values()),
        )
        return int(cursor.lastrowid)

    def _update(self, table: str, row_id: int, fields: dict[str, Any], allowed: tuple) -> None:
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        for key in ("task_ids", "commits"):
            if key in fields:
                fields[key] = json.dumps(list(dict.fromkeys(fields[key])))
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",  # noqa: S608 - keys from `allowed`
            (*fields.values(), row_id),
        )

    def add_milestone(self, **fields: Any) -> int:
        milestone_id = self._insert("milestones", fields)
        self._reindex("milestone", milestone_id)
        self._conn.commit()
        return milestone_id

    def update_milestone(self, milestone_id: int, **fields: Any) -> None:
        self._update("milestones", milestone_id, fields, _MILESTONE_FIELDS)
        self._reindex("milestone", milestone_id)
        self._conn.commit()

    def add_problem(self, **fields: Any) -> int:
        problem_id = self._insert("problems", fields)
        self._reindex("problem", problem_id)
        self._conn.commit()
        return problem_id

    def update_problem(self, problem_id: int, **fields: Any) -> None:
        self._update("problems", problem_id, fields, _PROBLEM_FIELDS)
        self._reindex("problem", problem_id)
        self._conn.commit()

    def add_attempt(self, problem_id: int, **fields: Any) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM attempts WHERE problem_id = ?", (problem_id,)
        ).fetchone()
        attempt_id = self._insert("attempts", {**fields, "problem_id": problem_id, "seq": row[0]})
        self._reindex("attempt", attempt_id)
        self._conn.commit()
        return attempt_id

    def update_attempt(self, attempt_id: int, **fields: Any) -> None:
        self._update("attempts", attempt_id, fields, _ATTEMPT_FIELDS)
        self._reindex("attempt", attempt_id)
        self._conn.commit()

    def dead_ends(self, repo: str | None, limit: int) -> list[dict]:
        """Failed attempts, newest first, each with its problem and what worked instead."""
        scope = "" if repo is None else " AND p.cwd IN (SELECT cwd FROM repos WHERE repo = ?)"
        params: list[Any] = [] if repo is None else [repo]
        rows = self._conn.execute(
            "SELECT a.*, p.title AS problem_title, p.cwd AS cwd, "  # noqa: S608 - fixed clauses
            "(SELECT w.description FROM attempts w WHERE w.problem_id = a.problem_id "
            " AND w.outcome = 'worked' ORDER BY w.seq DESC LIMIT 1) AS worked_instead "
            "FROM attempts a JOIN problems p ON p.id = a.problem_id "
            "WHERE a.outcome = 'failed'" + scope  # noqa: S608 - fixed clause, values bound
            + " ORDER BY COALESCE(a.invalidated_on, a.believed_from, '') DESC, a.id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        names = self._names()
        return [self._label(self._history_row(r), names) for r in rows]

    def search_history(self, query: str, repo: str | None, limit: int = 20) -> dict:
        """Problems (with their attempts) and milestones matching ``query``, best first.

        Every word must match (FTS5, accent-insensitive); when nothing does,
        any word may. Attempt hits surface their problem.
        """
        words = [w.replace('"', "") for w in query.split()][:SEARCH_MAX_WORDS]
        words = [w for w in words if w]
        if not words:
            return {"problems": [], "milestones": []}
        rows: list[sqlite3.Row] = []
        for joiner in (" ", " OR "):
            match = joiner.join(f'"{w}"' for w in words)
            rows = self._conn.execute(
                "SELECT kind, ref_id FROM history_fts WHERE history_fts MATCH ? "
                "ORDER BY bm25(history_fts) LIMIT 200",
                (match,),
            ).fetchall()
            if rows:
                break
        allowed = None if repo is None else set(self.repo_cwds(repo))
        problems: list[dict] = []
        milestones: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for r in rows:
            if r["kind"] == "milestone":
                key = ("milestone", int(r["ref_id"]))
                item = None if key in seen else self.get_milestone(key[1])
                bucket = milestones
            else:
                problem_id = int(r["ref_id"])
                if r["kind"] == "attempt":
                    attempt = self.get_attempt(problem_id)
                    if attempt is None:
                        continue
                    problem_id = attempt["problem_id"]
                key = ("problem", problem_id)
                item = None if key in seen else self.get_problem(problem_id)
                bucket = problems
            if item is None:
                continue
            seen.add(key)
            if allowed is not None and item["cwd"] not in allowed:
                continue
            if len(problems) + len(milestones) < limit:
                bucket.append(item)
        return {"problems": problems, "milestones": milestones}

    def history_cursor(self, cwd: str) -> int:
        row = self._conn.execute(
            "SELECT last_task_id FROM history_cursors WHERE cwd = ?", (cwd,)
        ).fetchone()
        return int(row[0]) if row else 0

    def advance_history_cursor(self, cwd: str, last_task_id: int) -> None:
        self._conn.execute(
            "INSERT INTO history_cursors (cwd, last_task_id) VALUES (?, ?) "
            "ON CONFLICT(cwd) DO UPDATE SET "
            "last_task_id = MAX(last_task_id, excluded.last_task_id)",
            (cwd, last_task_id),
        )
        self._conn.commit()

    def _unsynced_sql(self, cwds: list[str]) -> tuple[str, list[Any]]:
        if not cwds:
            return "0", []
        clauses = " OR ".join("(cwd = ? AND id > ?)" for _ in cwds)
        params: list[Any] = []
        for cwd in cwds:
            params += [cwd, self.history_cursor(cwd)]
        return (
            f"kind = 'prompt' AND status IN ('done', 'failed', 'interrupted') AND ({clauses})",
            params,
        )

    def tasks_for_history(self, cwds: list[str], limit: int) -> list[dict]:
        where, params = self._unsynced_sql(cwds)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE {where} ORDER BY id ASC LIMIT ?",  # noqa: S608
            [*params, limit],
        ).fetchall()
        return [self._task_row(r) for r in rows]

    def pending_history_tasks(self, cwds: list[str]) -> int:
        where, params = self._unsynced_sql(cwds)
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM tasks WHERE {where}", params  # noqa: S608
        ).fetchone()
        return int(row[0])

    def history_sync(self, repo: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM history_syncs WHERE repo = ?", (repo,)).fetchone()
        return self._row(row) if row else None

    def begin_history_sync(self, repo: str, model: str, *, stale_after_s: float) -> bool:
        """Mark a repo's sync running; False if one already is (a stale claim is taken over)."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT state, started_at FROM history_syncs WHERE repo = ?", (repo,)
            ).fetchone()
            if row is not None and row["state"] == "running" and row["started_at"]:
                started = _parse_iso(row["started_at"])
                if started is not None and time.time() - started < stale_after_s:
                    self._conn.rollback()
                    return False
            self._conn.execute(
                "INSERT INTO history_syncs (repo, state, started_at, error, model) "
                "VALUES (?, 'running', ?, NULL, ?) ON CONFLICT(repo) DO UPDATE SET "
                "state = 'running', started_at = excluded.started_at, error = NULL, "
                "model = excluded.model",
                (repo, now_iso(), model),
            )
            self._conn.commit()
            return True
        except BaseException:
            self._conn.rollback()
            raise

    def finish_history_sync(self, repo: str, *, cost_usd: float, error: str | None) -> None:
        self._conn.execute(
            "UPDATE history_syncs SET state = 'idle', finished_at = ?, error = ?, "
            "last_cost_usd = ?, total_cost_usd = total_cost_usd + ? WHERE repo = ?",
            (now_iso(), error, cost_usd, cost_usd, repo),
        )
        self._conn.commit()

    def has_history(self) -> bool:
        return self._conn.execute("SELECT 1 FROM attempts LIMIT 1").fetchone() is not None

    def has_project(self, cwd: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM tasks WHERE cwd = ? LIMIT 1", (cwd,)).fetchone()
        return row is not None

    def state(self, done_limit: int = 500) -> dict:
        terminal_placeholders = ", ".join("?" for _ in TERMINAL)
        # placeholder count comes from the fixed TERMINAL tuple; values are bound
        not_in_sql = f"SELECT * FROM tasks WHERE status NOT IN ({terminal_placeholders})"  # noqa: S608
        non_terminal = self._conn.execute(not_in_sql, list(TERMINAL)).fetchall()
        in_sql = (
            f"SELECT * FROM tasks WHERE status IN ({terminal_placeholders}) "  # noqa: S608
            "ORDER BY COALESCE(finished_at, started_at, created_at) DESC LIMIT ?"
        )
        terminal = self._conn.execute(in_sql, (*TERMINAL, done_limit)).fetchall()
        tasks = [self._task_row(r) for r in (*non_terminal, *terminal)]
        return {
            "rev": self.rev(),
            "sessions": self.list_sessions(),
            "tasks": tasks,
            "lanes": self.list_lanes(),
        }
