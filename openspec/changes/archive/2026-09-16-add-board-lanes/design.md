## Context

Tasky 0.2.0 stores tasks in SQLite, starts workers with `worker.run_task`, and serves a dashboard that
groups tasks vertically. The visual direction for the new board is fixed by the designer brief kept
with this change (`design-brief.md`). This document fixes the data model, the scheduling rules and
the API the board uses.

## Goals / Non-Goals

**Goals:** a board whose order is the execution order; three start modes per task; a run queue that
advances by itself per project; cloned runs that reuse a session's conversation.

**Non-Goals:** cross-project ordering, running more than one task at a time from one project's queue,
editing a running worker's prompt, merging a clone's result back into its source conversation.

## Decisions

### 1. Data model (schema version 2)

New nullable columns on `tasks`, added with `ALTER TABLE` when `PRAGMA user_version` is 1:

| Column | Values | Meaning |
| --- | --- | --- |
| `lane` | `NULL`, `'serial'` | `NULL` is Inbox while queued; `'serial'` is the project's run queue |
| `run_mode` | `NULL`, `'now'`, `'serial'`, `'fork'` | how a worker started the task; `NULL` for session prompts |
| `permission_mode` | a `PERMISSION_MODES` value | stored when the task joins the run queue or starts |
| `fork_of` | session id | the session a `fork` run cloned |

New table `lanes (cwd TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, reason TEXT)`, with the same
revision triggers as `tasks`. A missing row means not paused.

A fresh database is created with the version 2 schema directly. Migration runs inside the existing
retrying `_initialize` and is idempotent (columns are added only if `PRAGMA table_info` lacks them).

`lane` keeps its value after the task starts, so the board can tell which running and finished tasks
came from the queue. Only `status = 'queued'` tasks are shown in Inbox or Up next.

### 2. Scheduler

`tasky/scheduler.py` exposes `kick(store, config, cwd=None) -> list[dict]`. For each directory (only
`cwd` when given) that has queued `lane='serial'` tasks, is not paused, and has no `running` task with
`run_mode='serial'`, it starts the head of the queue and returns the tasks it started.

The check and the claim are one `BEGIN IMMEDIATE` transaction in `Store.claim_serial_head(cwd,
session_id)`, which returns the claimed task or `None`. This closes the race where two kicks both see
an idle queue and claim different tasks. The worker is then launched from the claimed task through the
same code path as `run_task`.

`kick` is called by: the enqueue endpoint, the lane resume endpoint, `supervise` after it records the
worker's exit, and the server's periodic sync (the same throttle as session titles, every 3 seconds).
Launch errors mark that task `failed` and pause the lane, so a broken directory does not spin.

`supervise`, after its existing status bookkeeping, pauses the lane with a reason when the task came
from the queue (`run_mode='serial'`) and ended `failed` or `interrupted`, then calls `kick(cwd)`. A task
the Stop hook marked `done` leaves the lane running.

### 3. Start modes

`worker.run_task(store, config, task_id, permission_mode="default", mode="now", popen=...)`.

- `now`: current behavior plus `run_mode='now'`.
- `fork`: source session = the task's `session_id` if that session exists, else the resumable session (one with a
  transcript) whose `cwd` equals the task's `cwd`, preferring sessions the user drove over headless
  worker sessions and then the latest `last_seen_at`. The source's transcript file must exist. The source id must match the canonical UUID
  pattern, and its `cwd` must be an existing directory, or `WorkerError("no session in <cwd> to
  clone")` is raised before the claim. The command gains `--resume <source> --fork-session` before
  `--session-id <new>`, runs with the source session's `cwd`, and the task stores `fork_of`.
- `serial` is never passed by clients; the scheduler uses it.

Verified on Claude Code 2.1.272: `claude -p --resume P --fork-session --session-id F` with the prompt on
standard input keeps P's conversation and reports session id F.

### 4. Auto-pull

`Store.next_queued` adds `AND lane IS NULL` to both queries. Tasks in the run queue belong to the
scheduler only.

### 5. API contract

All mutations need the token header and `Content-Type: application/json`, like today.

`GET /api/state` adds:

```json
{
  "tasks": [{"...": "existing fields", "lane": null, "run_mode": null, "permission_mode": null, "fork_of": null}],
  "lanes": [{"cwd": "/path/project", "paused": true, "reason": "task #12 failed"}]
}
```

`POST /api/tasks/<id>/run` body `{"permission_mode": "acceptEdits", "mode": "now" | "fork"}`. `mode`
defaults to `now`. 200 with the task; 400 for a bad mode, bad permission mode or nothing to clone; 404;
409 when not queued.

`POST /api/tasks/<id>/enqueue` body `{"permission_mode": "acceptEdits", "before_id": 17 | null}`. Sets
`lane='serial'` and `permission_mode`, places the task before `before_id` or at the end, then calls
`kick(cwd)`. 200 with the task as it is after the kick (it may already be `running`); 400 for a bad
permission mode, a `before_id` that is not a queued task in the same lane, or a task without `cwd`; 404;
409 when not queued.

`PATCH /api/tasks/<id>` additionally accepts `"lane": null` to send a queued task back to Inbox.
`before_id` keeps reordering among queued tasks.

`PATCH /api/lanes` body `{"cwd": "/path/project", "paused": false}`. Resuming calls `kick(cwd)`.
200 with the lane row.

`bypassPermissions` stays refused unless `TASKY_ALLOW_BYPASS=1`, for every endpoint that takes a mode.

### 6. CLI

`tasky run <id> [--mode now|fork] [--permission-mode M]` and `tasky enqueue <id> [--permission-mode M]`.

### 7. Board

The frontend follows `design-brief.md`. Column membership:

| Column | Tasks |
| --- | --- |
| Inbox | `status='queued'` and `lane` is null, ordered by `position` |
| Up next | `status='queued'` and `lane='serial'`, ordered by `position`, grouped by project when unfiltered |
| Running: queue runner | `status='running'` and `run_mode='serial'` |
| Running: parallel | every other `running` prompt, with running delegations nested under their parent |
| Needs attention | `interrupted` or `failed` |
| Done | `done` and `cancelled`, newest first |

A project can be cloned when `state.sessions` has a session with that `cwd` and a `transcript_path`. Drag uses pointer events;
Inbox to Up next calls enqueue with `before_id`; within Up next it calls `PATCH before_id`; Up next to
Inbox calls `PATCH lane: null`. The refresh loop does not re-render while a drag is active.

## Risks / Trade-offs

- A clone replays the source conversation as input tokens; the card says so.
- The clone's result stays on the Tasky card; the source conversation never sees it.
- A queue task left `running` by a killed supervisor blocks its lane until cancelled; the existing
  cancel action is the recovery.
- Pausing on failure can surprise a user who expected the queue to continue; the board shows the
  paused state and a Resume action next to the reason.
