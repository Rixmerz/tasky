## 1. Data and scheduling

- [x] 1.1 Schema version 2: new `tasks` columns, `lanes` table with revision triggers, idempotent migration from version 1, tests for fresh and migrated databases
- [x] 1.2 Store: `lane`/`run_mode`/`permission_mode`/`fork_of` fields, `claim_serial_head`, lane get/set/list, `next_queued` ignores the run queue, move within lanes
- [x] 1.3 `tasky/scheduler.py` `kick`, with tests for order, one-at-a-time under concurrency, pause, independent projects
- [x] 1.4 Worker `mode` now/fork/serial, fork source selection and validation, tests on the built command
- [x] 1.5 Supervisor pauses the lane on failed or interrupted queue tasks and kicks the lane on exit

## 2. API and CLI

- [x] 2.1 `/api/state` lanes and new task fields; run `mode`; `enqueue`; `PATCH lane`; `PATCH /api/lanes`; periodic kick in the server sync; tests for every status code
- [x] 2.2 CLI `run --mode` and `enqueue`

## 3. Board

- [x] 3.1 Top bar: create pill with a grouped "to" picker (active sessions, projects, other), settings popover with project filter and permission selector, filter chip
- [x] 3.2 Columns and Running sections per the membership table, paused lane banner with Resume
- [x] 3.3 Card actions Run now, After last, Parallel with context, with the unavailable state
- [x] 3.6 Five-tint repeating card colours per column, light and dark, contrast computed
- [x] 3.4 Pointer drag and keyboard reorder with the drag guard on refresh
- [x] 3.5 Visual system: shadcn-style row list in plain CSS, light and dark, 400 / 1024 / 1440 layouts, full-width fluid columns up to 2560 with per-column scroll, empty columns shrink

## 4. Verification and delivery

- [x] 4.1 Full test suite, `ruff check .`, node tests, `node --check`, `claude plugin validate .`, `openspec validate --all --strict`
- [x] 4.2 End-to-end with real `claude -p`: two tasks through the run queue in order, one fork run recalls its source's context
- [x] 4.3 Board measured in a real browser at 400, 1024, 1440, 1920 and 2560 pixels, including a drag
- [x] 4.4 Version 0.3.0, README and CHANGELOG
- [x] 4.5 Committed, pushed, installed plugin updated
