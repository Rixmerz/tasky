## Context

Claude Code (verified against 2.1.272) emits lifecycle hooks with JSON on stdin. Payload facts this
design relies on, all observed in a live headless session:

- Every event carries `session_id`, `cwd`, `transcript_path`, `hook_event_name`; most carry `prompt_id`.
- `UserPromptSubmit` has `prompt` and `prompt_id`. It does **not** carry `source` in this version.
- `UserPromptSubmit` returning `{"decision":"block","reason":...}` stops the prompt before the model:
  zero turns, zero cost.
- The `Agent` tool runs subagents asynchronously. `PreToolUse`/`PostToolUse` carry `tool_use_id` and
  `tool_input` (`description`, `prompt`, `subagent_type`). `PostToolUse.tool_response` is
  `{"isAsync": true, "status": "async_launched", "agentId": "...", ...}`.
- `SubagentStart`/`SubagentStop` carry `agent_id`, `agent_type`; `SubagentStop` has `last_assistant_message`.
- When a background subagent finishes, the parent session receives a new `UserPromptSubmit` whose
  prompt starts with `<task-notification>` and contains `<task-id>`, `<tool-use-id>`, `<status>`,
  `<result>`. It has a **new** `prompt_id`.
- `Stop` has `last_assistant_message`, `stop_hook_active`, `background_tasks`. Returning
  `{"decision":"block","reason":...}` continues the session with `reason` as the instruction; the next
  `Stop` keeps the **same** `prompt_id` and has `stop_hook_active: true`.
- `SessionStart` has `source` in `startup|resume|clear|compact|fork`; returning
  `hookSpecificOutput.additionalContext` injects text into the model context.
- `SessionEnd` has `reason`. Environment variables of the `claude` process are inherited by hooks.
- Transcripts live in `<claude config dir>/projects/<slug>/<session>.jsonl`; subagent transcripts in
  `<slug>/<session>/subagents/`. User entries carry `isMeta`, `isCompactSummary`, `isSidechain`,
  `toolUseResult`, `origin.kind` (`human` | `task-notification`), `timestamp`, `uuid`, `sessionId`, `cwd`.

## Goals / Non-Goals

**Goals:** deterministic capture with no model tokens; zero-token queueing from chat; opt-in queue
draining; context recovery after compaction; parallel headless workers; a dashboard that needs nothing
but Python's standard library; safe failure (a Tasky bug never breaks a session).

**Non-Goals:** classifying prompts with a model; syncing across machines; editing Claude Code settings
on the user's behalf; user accounts or remote access to the dashboard.

## Decisions

1. **Standard library only, Python 3.10+.** No install step besides the plugin. SQLite in WAL mode
   with `busy_timeout=5000` handles concurrent hook processes, CLI and server.
2. **Repository root is the plugin root.** `.claude-plugin/plugin.json`, `hooks/hooks.json`,
   `commands/`, `bin/`, `tasky/` live at the root, and `.claude-plugin/marketplace.json` makes the
   repository installable as a single-plugin marketplace.
3. **All hooks synchronous, tool hooks narrowly matched.** `PostToolUse` is the only event that
   carries both `tool_use_id` and `agentId`; making it async would race `SubagentStop`. Pre/PostToolUse
   use the anchored matcher `^(Agent|Task)$` so Python never starts for Bash or Read. Todo lists are not mirrored: a
   todo left in progress kept its prompt running forever, and no requirement needs them.
4. **`prompt_id` is a plain column, not a unique key.** One prompt id can span several tasks (the
   original prompt plus tasks pulled at `Stop`). A `Stop` resolves to the most recently started running
   task with that prompt id in that session.
5. **Change detection by revision counter.** A `meta.rev` integer is bumped by triggers on every
   insert, update and delete of `tasks` and `sessions`. The dashboard polls it every 2 seconds.
6. **Token plus localhost protections.** Bind `127.0.0.1`; reject unknown `Host`; every `/api/*`
   request carries `X-Tasky-Token` matching `$TASKY_HOME/token` (0600, compared in constant time);
   mutations must be `application/json`. Browser-level checks alone only stop websites: other local
   accounts and sandboxed apps share the loopback port. The token reaches the page once through the
   URL fragment printed by `tasky ui`, which is never sent to the server. The token file is re-read on
   every request, so deleting it revokes open tabs. Before sending the token, `tasky ui` and the page
   request `/api/version` with a random nonce only and verify `HMAC-SHA256(token, "<nonce>:<port>")`,
   so a listener squatting on the port never receives the token.
7. **Claims are atomic.** Starting a worker and pulling a queued task at `Stop` both use a
   check-and-update inside one `BEGIN IMMEDIATE` transaction, so a task is taken exactly once.
8. **Workers never put the task text in argv.** The prompt goes to `claude -p` on stdin, so text that
   looks like an option cannot change permissions. A detached supervisor waits for the process and
   records a failure if it exits without a result. A fixed list of session variables of the launching
   Claude Code process (`CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT` and the `CLAUDE_CODE_*` session,
   messaging and entrypoint variables) is removed from the worker environment; user settings such as
   provider selection pass through. `bypassPermissions` requires `TASKY_ALLOW_BYPASS`.
9. **Private files.** Entry points set umask 077; the data directory is 0700 and the database 0600.

## Data model

Timestamps are UTC ISO-8601 strings with milliseconds and a `Z` suffix (`2026-01-02T03:04:05.678Z`).

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);   -- ('rev', n)
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, cwd TEXT, transcript_path TEXT, title TEXT,
  started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',          -- active | ended
  auto_pull INTEGER NOT NULL DEFAULT 0, pull_chain INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'hook'            -- hook | import | worker
);
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT, parent_id INTEGER, kind TEXT NOT NULL,   -- prompt | delegation
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,     -- queued | running | done | failed | interrupted | cancelled
  result TEXT, cwd TEXT, prompt_id TEXT, external_id TEXT UNIQUE, agent_id TEXT,
  source TEXT NOT NULL,     -- hook | chat | ui | cli | import
  position REAL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
```

Schema version via `PRAGMA user_version = 1`. Indexes on `tasks(session_id)`, `tasks(status)`,
`tasks(prompt_id)`, `tasks(agent_id)`, `tasks(cwd)`.

## Module contracts

These signatures are the contract between modules. Row values are returned as plain `dict`s whose
keys are the column names above; `tasks` rows additionally include `project` (basename of `cwd`, or
`""`).

### `tasky/config.py`

```python
@dataclass(frozen=True)
class Config:
    home: Path                 # TASKY_HOME, else $XDG_DATA_HOME/tasky, else ~/.local/share/tasky
    db_path: Path              # home / "tasky.db"
    log_dir: Path              # home / "logs"
    claude_config_dir: Path    # CLAUDE_CONFIG_DIR, else ~/.claude
    port: int                  # TASKY_PORT, default 7733
    queue_prefix: str          # TASKY_QUEUE_PREFIX, default "++"
    max_chain: int             # TASKY_MAX_CHAIN, default 5
    max_result: int            # TASKY_MAX_RESULT, default 8000
    context_items: int         # TASKY_CONTEXT_ITEMS, default 10
    claude_bin: str            # TASKY_CLAUDE_BIN, default "claude"
    allow_bypass: bool         # TASKY_ALLOW_BYPASS in ("1", "true", "yes")
    token_path: Path           # home / "token"
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config": ...
def now_iso() -> str: ...
def load_token(config: Config, *, create: bool = True) -> str | None: ...  # 0600, atomic create
```

Invalid integers in the environment fall back to defaults.

### `tasky/prompts.py`

```python
@dataclass(frozen=True)
class Classified:
    kind: str   # "task" | "notification" | "ignore"
    text: str   # task text (slash commands rendered "/name args"); original text otherwise
def classify(text: str) -> Classified: ...
def parse_notifications(text: str) -> list[dict]:  # [{"task_id","tool_use_id","status","result"}]
def queue_request(text: str, prefix: str) -> str | None:  # None if not prefixed; "" if prefix only
def make_title(text: str, limit: int = 140) -> str:       # first non-empty line, collapsed, ellipsized
```

Ignored: empty text; text starting with `<local-command-`, `<command-message>` without
`<command-args>` content, `Caveat:` caveat blocks (`<local-command-caveat>`), `<system-reminder>`,
`<bash-input>`, `<bash-stdout>`, `<bash-stderr>`, `[Request interrupted`. Notification: text starting
with `<task-notification>`. A slash command block (`<command-name>/x</command-name>` with non-empty
`<command-args>`) is a task rendered `/x args`.

### `tasky/store.py`

```python
TASK_STATUSES = ("queued", "running", "done", "failed", "interrupted", "cancelled")
TERMINAL = ("done", "failed", "interrupted", "cancelled")
TASK_KINDS = ("prompt", "delegation")
class Store:
    def __init__(self, db_path: Path, max_result: int = 8000): ...   # creates dirs + schema
    @classmethod
    def open(cls, config: Config) -> "Store": ...
    def close(self) -> None: ...                                      # also __enter__/__exit__
    def rev(self) -> int: ...
    # sessions
    def upsert_session(self, session_id: str, *, cwd: str | None = None,
                       transcript_path: str | None = None, source: str = "hook",
                       at: str | None = None) -> dict: ...  # sets last_seen_at; state back to active
    def get_session(self, session_id: str) -> dict | None: ...
    def list_sessions(self) -> list[dict]: ...                        # last_seen_at desc
    def update_session(self, session_id: str, **fields) -> dict: ...  # title, auto_pull, pull_chain, state
    def end_session(self, session_id: str, at: str | None = None) -> int: ...  # running -> interrupted
    # tasks
    def create_task(self, *, kind: str, body: str, status: str, source: str,
                    cwd: str | None = None, session_id: str | None = None,
                    parent_id: int | None = None, title: str | None = None,
                    prompt_id: str | None = None, external_id: str | None = None,
                    agent_id: str | None = None, result: str | None = None,
                    created_at: str | None = None, started_at: str | None = None,
                    finished_at: str | None = None) -> dict: ...
    def get_task(self, task_id: int) -> dict | None: ...
    def get_task_by_external_id(self, external_id: str) -> dict | None: ...
    def find_task_by_agent_id(self, agent_id: str) -> dict | None: ...
    def update_task(self, task_id: int, **fields) -> dict: ...
    def delete_task(self, task_id: int) -> bool: ...
    def list_tasks(self, *, status: str | Iterable[str] | None = None,
                   session_id: str | None = None, cwd: str | None = None,
                   kind: str | None = None, limit: int | None = None) -> list[dict]: ...
    def latest_running_task(self, session_id: str, prompt_id: str | None = None,
                            kind: str | None = "prompt") -> dict | None: ...
    def running_children(self, parent_id: int) -> list[dict]: ...   # delegations only
    def claim_task(self, task_id: int, *, expected_status: str = "queued",
                   **fields) -> dict | None: ...  # atomic check-and-update; None if lost
    def interrupt_running(self, session_id: str, *, kind: str = "prompt",
                          exclude_ids: Iterable[int] = ()) -> int: ...
    def interrupt_session(self, session_id: str) -> int: ...        # every running task
    def session_has_tasks_not_from(self, session_id: str, source: str) -> bool: ...
    def next_queued(self, session_id: str | None, cwd: str | None) -> dict | None: ...
    def move_task(self, task_id: int, before_id: int | None) -> dict: ...
    def state(self, done_limit: int = 500) -> dict: ...  # {"rev", "sessions", "tasks"}
```

Semantics: `create_task` validates `kind`/`status` (`ValueError`), derives `title` with `make_title`
when missing, truncates `result` to `max_result` (suffix `"\n…[truncated]"`), sets `position` to
`max(position)+1` for `queued`, sets `started_at` for `running` and `finished_at` for terminal statuses
when not given; on an `external_id` conflict it returns the existing row unchanged. `update_task`
accepts `title, body, status, result, session_id, prompt_id, agent_id, parent_id, position,
started_at, finished_at`; unknown fields or invalid status raise `ValueError`, a missing id raises
`KeyError`; moving to `running` sets `started_at` to now, moving to a terminal status sets
`finished_at` to now, moving to `queued` clears both and assigns a tail position. `list_tasks` orders
queued tasks by `position`, all others by `COALESCE(started_at, created_at)` descending.
`latest_running_task` orders by `started_at` desc then id desc. `next_queued` prefers the lowest
position bound to `session_id`, else the lowest position with `session_id IS NULL` and matching `cwd`.
`move_task(id, before_id)` places the task just before `before_id`, or last when `None`. `state()`
returns every non-terminal task plus the newest `done_limit` terminal tasks.

### `tasky/hooks.py`

```python
def handle(event: dict, store: Store, config: Config, env: Mapping[str, str]) -> dict | None: ...
def main(stdin: TextIO, stdout: TextIO, env: Mapping[str, str] | None = None) -> int: ...  # always 0
```

| Event | Behaviour |
|---|---|
| `SessionStart` | `upsert_session`. On `resume`, `interrupt_session(session)` first (every running task, delegations included). If `source` in `resume`/`compact` and the session has running, interrupted or session-bound queued tasks: return `additionalContext` with up to `context_items` lines `#<id> [<status>] <title>` under a one-line header. Else `None`. |
| `UserPromptSubmit` | `upsert_session`. Queue prefix → create `queued` task (`source="chat"`, session-bound) and return block with reason `Tasky: queued #<id> — <title>`; prefix only → block with usage hint. Notification → for each parsed item close the delegation (by `external_id == tool_use_id`, else `agent_id == task_id`) with `status` `done` (or `failed` when status is `failed`/`killed`) and `result`; then set its parent `prompt_id=<new>` and `status="running"`. Ignore → `None`. Notifications never touch a `cancelled` task, never erase a stored result, and reopen a parent only from `running`, `done` or `interrupted`. Task text with `TASKY_TASK_ID` env naming a task that is `queued` or `running` whose `session_id` is empty or this session: bind `session_id`, `prompt_id`, status `running`. Otherwise create `running` prompt task (`source="hook"`) and reset `pull_chain=0`. Returns `None` unless blocking. |
| `PreToolUse` (`Agent`/`Task`) | Create `running` delegation with `external_id=tool_use_id`, `title=description`, `body=prompt`, parent = `latest_running_task(session, prompt_id)`. |
| `PostToolUse` (`Agent`/`Task`) | Find by `external_id`; if `tool_response.isAsync` store `agent_id`; else mark `done` with the response text (string, or joined `text` blocks from `content`). |
| `SubagentStop` | Task by `agent_id` (fallback: newest running delegation in session with no `agent_id`) → `done`, `result=last_assistant_message`. |
| `Stop` | Target = `latest_running_task(session, prompt_id)`; only an event without `prompt_id` falls back to the latest running task. When no target is found and `TASKY_TASK_ID` names a task `running` in this session, that task is the target. Store `result`; status `done` unless `running_children(target)` is non-empty. Then `interrupt_running(session, exclude_ids=[target, TASKY_TASK_ID task])` (Claude Code sends no event for Esc). Then if session `auto_pull` and `pull_chain < max_chain`: up to 5 attempts of `next_queued` + `claim_task(status="running", session_id, prompt_id)`; on the first claim `pull_chain+1` and return `{"decision":"block","reason":<explicit instruction naming #id, then the body>}`. |
| `StopFailure` | Latest running task of the prompt → `failed`, `result` = error text if present. |
| `SessionEnd` | `end_session`. |

`main` reads stdin JSON, opens the store from `Config.from_env(env)`, calls `handle`, prints the JSON
result when not `None`. Any exception is appended to `home/hook-errors.log` with a timestamp; nothing is
printed; returns 0.

### `tasky/importer.py`

```python
def import_transcripts(store: Store, config: Config, *, dry_run: bool = False) -> dict: ...
# -> {"files": int, "sessions": int, "tasks": int, "skipped_sessions": int, "bad_lines": int}
```

Scans `claude_config_dir/projects/*/*.jsonl` (never `subagents/`). Skips a session whose row has a
source other than `import` or that has tasks from another source. Damaged lines and files are counted
and skipped. Foreground delegations close from their `tool_result`; leftovers close as `done` at the
end of the file, including when a file fails part-way, and a re-run repairs rows left `running`.
Non-string timestamps, cwds and ids are ignored. `#token=` values in results are redacted. Imported
sessions are marked `ended`. Prompt tasks use `external_id="import:<uuid>"`; delegations
`external_id=<tool_use id>` from assistant `tool_use` blocks named `Agent`/`Task`. Result of a prompt =
last assistant `text` block before the next prompt. Imported sessions use `source="import"`.

### `tasky/worker.py`

```python
PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
class WorkerError(Exception): ...
def run_task(store: Store, config: Config, task_id: int, permission_mode: str = "default",
             popen=subprocess.Popen) -> dict: ...
```

Order: task exists; mode valid; `bypassPermissions` only with `allow_bypass`; `cwd` is a directory;
`claim_task(status="running", session_id)` or raise `TaskNotQueued(WorkerError)`; then register the
session (`source="worker"`). Any failure after the claim marks the task `failed`. Rejections before the claim leave the task unchanged. The body is written
to `log_dir/task-<id>.prompt` (0600) and never appears in argv. The launched process is the detached
supervisor `bin/tasky supervise <id> <session> <prompt> <log> -- <claude_bin> -p --session-id <session>
--permission-mode <mode>`, with the launching session's variables removed (decision 8) and
`TASKY_TASK_ID` set. `tasky/supervise.py` feeds the prompt on stdin, logs output,
waits, removes the prompt file and marks a still-running task `failed` (non-zero exit) or
`interrupted` (zero exit without a result).

### `tasky/server.py`

```python
def make_server(config: Config, host: str = "127.0.0.1", port: int | None = None, *,
                web_dir: Path | None = None, token: str | None = None) -> ThreadingHTTPServer: ...
def serve(config: Config, port: int | None = None, open_browser: bool = False) -> int: ...
```

Each request opens its own `Store`. Every `/api/*` request needs `X-Tasky-Token` (401 otherwise);
static routes are public. `serve` prints `http://127.0.0.1:<port>/#token=<token>`. Responses are JSON (`application/json; charset=utf-8`) except
static files. Errors: `{"error": "<message>"}`.

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `/api/version` | – (`X-Tasky-Nonce` optional) | 200 `{"rev": n}` with the token (plus `"proof"` when a nonce is sent); 200 `{"proof"}` only, for a valid nonce without a token |
| GET | `/api/state` | – | 200 `{"rev", "sessions", "tasks", "config": {"queue_prefix", "max_chain", "port", "allow_bypass"}}` |
| POST | `/api/tasks` | `{"body": str (required, non-blank), "cwd": str, "title"?: str, "session_id"?: str}` | 201 task (`queued`, `source="ui"`) |
| PATCH | `/api/tasks/<id>` | any of `status, title, body` or `{"before_id": int\|null}` to reorder | 200 task |
| DELETE | `/api/tasks/<id>` | – | 200 `{"deleted": true}` |
| POST | `/api/tasks/<id>/run` | `{"permission_mode": str}` | 200 task |
| PATCH | `/api/sessions/<id>` | `{"auto_pull"?: bool, "title"?: str}` | 200 session |
| POST | `/api/import` | `{}` | 200 import report |
| GET | `/`, `/app.js`, `/app.css` | – | static from `tasky/web/` |

400 on invalid JSON, values or `Content-Length`, 401 on a missing or wrong token, 403 on Host or
content-type violations, 404 on unknown or malformed ids and paths, 409 when a task is no longer
queued, 413 on bodies over 1 MiB, 500 JSON on unexpected errors.

### `tasky/cli.py`

`tasky serve [--port N] [--open]` · `tasky ui` (start a detached server if `/api/version` does not
answer, print the URL) · `tasky import [--dry-run]` · `tasky add TEXT [--cwd DIR] [--session ID]` ·
`tasky list [--status S] [--json]` · `tasky done ID` · `tasky cancel ID` ·
`tasky run ID [--permission-mode M]` · `tasky status [--short]` · `tasky hook` · hidden
`tasky supervise`. `bin/tasky` is a Python script that puts the plugin root, never the caller's cwd,
first on `sys.path`.
`status --short` prints `▶<running> ⏸<queued> ⚠<interrupted+failed>` for status lines.

## Risks / Trade-offs

- **Hook latency**: every prompt starts a Python process (~40 ms). Accepted; tool hooks are narrowly
  matched so the cost is per prompt, not per tool call.
- **Undocumented payload drift**: fields such as `tool_response.agentId` may change. Every lookup has a
  fallback and every failure is swallowed and logged.
- **Auto-pull spends tokens**: each pulled task is a full model turn. It is off by default, per
  session, and capped by `max_chain`.
- **Headless permission prompts**: a worker in `default` mode cannot approve tools interactively. The
  dashboard explains the modes; `bypassPermissions` requires `TASKY_ALLOW_BYPASS=1`.
- **Same-user escalation**: any process running as the user can read the token. With
  `TASKY_ALLOW_BYPASS=1`, a Claude Code session allowed to run shell commands could start a worker
  with more permissions than its own. Documented; the variable is off by default.
- **Interrupted prompts are inferred**: Claude Code emits no event on Esc, so they are marked at the
  next `Stop` or on resume. A prompt folded into a running turn can be marked interrupted early.
- **Result attribution after notifications** re-opens the parent task, so its result is the latest
  report rather than the first reply. This is the more useful value for a human scanning the board.
