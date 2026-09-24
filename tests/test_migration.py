"""Schema version 1 -> 2 migration (openspec/changes/add-board-lanes)."""

from __future__ import annotations

import sqlite3

from tasky.store import Store

# The exact tasky 0.2.0 schema, copied verbatim so this test exercises a real
# version-1 database rather than a guess at what one looked like.
_V1_SCHEMA = """
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
  position REAL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
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
"""


def _make_v1_db(db_path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_V1_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('rev', 0)")
        conn.execute(
            "INSERT INTO tasks (kind, title, body, status, source, position, created_at) "
            "VALUES ('prompt', 'old task', 'do the thing', 'queued', 'cli', 0.0, "
            "'2020-01-01T00:00:00.000Z')"
        )
        conn.execute(
            "INSERT INTO sessions (id, cwd, started_at, last_seen_at) "
            "VALUES ('11111111-1111-1111-1111-111111111111', '/proj', "
            "'2020-01-01T00:00:00.000Z', '2020-01-01T00:00:00.000Z')"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


def test_migration_adds_columns_and_lanes_table_and_keeps_rows(tmp_path):
    db_path = tmp_path / "v1.db"
    _make_v1_db(db_path)

    with Store(db_path) as store:
        columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(tasks)")}
        assert {"lane", "run_mode", "permission_mode", "fork_of"} <= columns

        tables = {
            row["name"]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "lanes" in tables

        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 6

        task = store.list_tasks()[0]
        assert task["title"] == "old task"
        assert task["body"] == "do the thing"
        assert task["status"] == "queued"
        assert task["lane"] is None
        assert task["run_mode"] is None
        assert task["permission_mode"] is None
        assert task["fork_of"] is None

        session = store.get_session("11111111-1111-1111-1111-111111111111")
        assert session is not None
        assert session["cwd"] == "/proj"


def test_migration_is_idempotent_when_run_twice(tmp_path):
    db_path = tmp_path / "v1-twice.db"
    _make_v1_db(db_path)

    with Store(db_path) as store:
        # Simulate the migration routine being invoked a second time against
        # an already-migrated database: it must not raise (duplicate column).
        store._migrate_v1_to_v2()
        store._migrate_v1_to_v2()
        columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(tasks)")}
        assert {"lane", "run_mode", "permission_mode", "fork_of"} <= columns


def test_fresh_database_gets_version_2_directly(tmp_path):
    db_path = tmp_path / "fresh.db"
    with Store(db_path) as store:
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 6
        columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(tasks)")}
        assert {"lane", "run_mode", "permission_mode", "fork_of"} <= columns
        tables = {
            row["name"]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "lanes" in tables


def test_reopening_a_migrated_database_does_not_re_run_migration(tmp_path):
    db_path = tmp_path / "v1-reopen.db"
    _make_v1_db(db_path)

    with Store(db_path):
        pass
    # Reopening a database already at version 2 must not error (the
    # constructor's version guard skips _initialize() entirely).
    with Store(db_path) as store:
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 6
