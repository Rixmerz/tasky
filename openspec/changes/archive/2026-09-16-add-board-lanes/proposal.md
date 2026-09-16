## Why

The dashboard stacks its groups vertically and looks boxy, so the queue's order and what is running in
the background are hard to see at a glance. Starting work is also limited to one action: run a queued
task as a fresh worker. Users want to decide per task whether it starts now, waits its turn behind the
previous one, or runs in parallel with the context of a session they already have.

## What Changes

- The dashboard becomes a horizontal board: Inbox, Up next, Running (queue runner and parallel),
  Needs attention and Done, with a top bar that creates tasks and chooses the permission mode.
- Every Inbox and Up next card offers three actions: Run now, After last and Parallel with context.
- Up next is a per-project serial run queue. Tasky starts its head task when nothing from that queue is
  running in the project, and pauses the queue when a task from it fails or is interrupted.
- Parallel with context starts a worker that clones an existing Claude Code session, conversation
  included, and runs the task in the clone.
- Vertical order in Up next is execution order, changed by drag and drop or by keyboard.
- The schema gains a lane, a run mode, a stored permission mode and the cloned session per task, plus
  a table of paused queues. Existing databases migrate in place.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard`: board layout, card actions, drag reorder, new API endpoints.
- `task-queue`: serial run queue per project; auto-pull ignores tasks in that queue.
- `parallel-workers`: runs that clone a session's context.

## Impact

- Code: `tasky/store.py`, `tasky/worker.py`, `tasky/supervise.py`, new `tasky/scheduler.py`,
  `tasky/server.py`, `tasky/cli.py`, `tasky/web/*`.
- Data: schema version 2, migrated with `ALTER TABLE`; no data loss.
- Cost: a cloned run replays the source session's conversation as input tokens.
