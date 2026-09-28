## Why

On a real board, tasks can sit in Running for days with nothing actually working on them: the
`claude` process behind them died (a crash, a hard reset, a laptop that never woke from sleep)
without the `SessionEnd` hook or the worker supervisor ever firing to report it. Both existing
liveness paths — `tasky.supervise` reporting a terminal status when its child process exits, and
`SessionEnd`/a resumed `SessionStart` interrupting whatever a session left running — depend on an
event firing. Neither fires when the machine itself goes away. Running is supposed to mean
"something is working on this now"; a task that has shown no sign of life for days does not meet
that bar and should not keep the user waiting on it.

## What Changes

- A periodic sweep (`tasky/stale.py`, run from the server's existing periodic sync tick) moves a
  `running` task to `interrupted` (Needs attention, reason "Stopped mid-work") once nothing shows
  it is still moving — no newer session activity, no growth in its session's transcript file, no
  growth in its worker's log file — for longer than a configurable threshold
  (`TASKY_STALE_RUNNING_HOURS`, default 24, `0` disables it).
- The sweep reuses existing signals only (session `last_seen_at`, the session's transcript file's
  mtime, the worker's log file's mtime, the task's own `started_at`); it does not add a daemon, a
  PID registry, or a new liveness channel.
- A swept `serial` task pauses its project's run queue, the same as a worker that reports
  `interrupted` on its own, so the queue does not start the next task behind one that vanished.
- A swept task's existing `result` (a parent task reopened while a delegation runs keeps its
  earlier reply) is preserved; the reason is appended, not overwritten.
- The sweep is idempotent (an interrupted task is terminal, so a second pass finds nothing to do)
  and never touches an already-terminal task or deletes anything.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard`: a `running` task with no sign of life past a threshold moves itself to Needs
  attention with an explicit reason, instead of only ever moving there through a hook event.

## Impact

- Code: new `tasky/stale.py`; `tasky/config.py` (`stale_running_hours`); `tasky/server.py` (calls
  the sweep from `sync_titles`, the existing periodic tick).
- Data: no schema change.
- Docs: README Configuration table and the Needs attention description.
