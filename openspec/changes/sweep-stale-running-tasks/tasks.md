## 1. Sweep

- [x] 1.1 `tasky/config.py`: `stale_running_hours` (`TASKY_STALE_RUNNING_HOURS`, default 24, `0`
      disables)
- [x] 1.2 `tasky/stale.py`: `sweep(store, config, now=None)` — per-task evidence (session
      `last_seen_at`, transcript mtime, worker log mtime, `started_at`), threshold comparison,
      appends the reason to an existing `result` instead of overwriting it, pauses a swept
      `serial` task's lane, ends a session once none of its running tasks are left
- [x] 1.3 `tasky/server.py`: call the sweep from `sync_titles`, before `scheduler.kick`

## 2. Tests

- [x] 2.1 `tests/test_stale.py`: alive via fresh transcript, alive via fresh worker log, stale with
      reason text, threshold edge (at and just past the boundary), already-terminal task untouched,
      no session falls back to `started_at`, delegation swept, serial task pauses its lane,
      parallel task does not touch a lane, existing `result` preserved and reason appended, an
      already-ended session is swept regardless of elapsed time, second sweep is a no-op, `0`
      disables the sweep, session ends once its last running task is swept, a session with another
      live task stays active
- [x] 2.2 Full suite, `ruff check .`, `node --test tests/web/digest.test.mjs`

## 3. Docs

- [x] 3.1 README: Configuration table row, a short paragraph on the Running/Needs attention board
      description
- [x] 3.2 CHANGELOG bullet reported to the integrator (not edited directly)
