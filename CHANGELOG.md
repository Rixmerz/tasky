# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.5.0] - 2026-09-24

### Added

- Project history: a History panel with dead ends (fixes believed correct that were not), problems
  and solutions, and dated milestones, each citing the tasks it came from. Built by
  **Sync with Haiku** or `tasky history`, which reads only the tasks finished since the previous
  sync plus the project's git log, and is the only Tasky feature that spends tokens.
- Sessions that start in a project with dead ends get the newest five in their context
  (`TASKY_DEAD_END_ITEMS`).
- `GET /api/insights`, `POST /api/insights/sync` and `GET /api/tasks/<id>`.
- `TASKY_HOOKS_OFF=1` makes every Tasky hook a no-op; the history sync sets it on its own model call.

### Changed

- The database schema moves to version 3; existing databases gain the new tables in place.

## [0.4.1] - 2026-09-24

### Fixed

- A prompt cancelled with Esc and sent again, as is or lightly edited, is recorded as one task
  instead of two. The earlier row is dropped only while it has no reply and no delegations, is
  running or interrupted, and is under 30 minutes old; prompts under 20 characters must match
  exactly.
- The test suite no longer launches the real `claude` or writes to the real ledger when a test
  kicks the run queue.

## [0.4.0] - 2026-09-24

### Added

- Search in the dashboard's top bar finds past tasks by the words in their title, prompt or reply,
  across the whole ledger, not only the tasks the board shows. Every word must match; results come
  newest first, respect the project filter, highlight the match and open in the side panel, so an
  old answer can be read again without asking the model. Press `/` to focus it and Esc to clear it.
- `GET /api/search?q=…&cwd=…` returns up to 50 matching tasks and whether there were more.

### Changed

- The `/` shortcut now focuses search; `n` still focuses the new-task bar.

## [0.3.0] - 2026-09-15

### Added

- The dashboard is a full-width board: Inbox, Up next, Running (queue runner and parallel), Needs
  attention and Done. Each task is a single line; everything else opens in a side panel. Empty
  columns shrink, each column scrolls on its own, rows cycle through five colour rails, and phones
  show one column at a time.
- Three actions per waiting row: Run now, After last and Parallel with context, as icon buttons
  and again, with words, in the row's menu.
- The create bar sends a task "to" an active session or a project; active ones come first and stale
  folders sit under "Other". The project filter and permission mode live behind one button, and an
  active filter shows as a clearable chip.
- Per-project run queue: Tasky runs Up next one task at a time in order, and pauses a project's queue
  when one of its tasks fails or is interrupted.
- Parallel runs that clone an existing session with `--resume --fork-session`, so the task starts with
  that conversation's context.
- Drag and drop, with a keyboard alternative, to reorder the run queue and move cards between Inbox
  and Up next.
- `tasky enqueue` and `tasky run --mode fork`.

### Changed

- Auto-pull never takes tasks that are in a project's run queue.
- The database schema moves to version 2; existing databases are migrated in place.

## [0.2.0] - 2026-09-15

### Added

- Results render as readable cards: headline first, one card per point or step, tables and code in
  their own blocks, and a "waiting for you" card when a reply ends with a question. Collapsed cards
  show the result's first sentence and the waiting marker.
- `tasky:Focus Cards`, an ADHD-friendly output style bundled with the plugin and selectable from
  `/output-style`. Original text under MIT; reply shape inspired by attention-span.

### Fixed

- Sessions are labeled with the name set by `/rename` (or Claude Code's generated name) instead of
  their id, for live and imported sessions.

### Changed

- Dashboard access links are redacted from every stored result, not only from imported ones.

## [0.1.0] - 2026-09-15

### Added

- Hook-driven capture of prompts, delegated subagents and results into a local SQLite ledger, with
  no model tokens spent. Prompts interrupted with Esc are marked at the next turn or on resume.
- Zero-token chat queue: prompts that start with `++` are stored and never reach the model.
- Opt-in auto-pull that hands a session its next queued task at the end of a turn, capped per
  chain.
- Context reminder of unfinished tasks when a session is resumed or compacted.
- Parallel headless workers that run a queued task as its own Claude Code session. The task text
  goes on standard input, each task starts at most once, and a supervisor records workers that exit
  without a result. `bypassPermissions` requires `TASKY_ALLOW_BYPASS=1`.
- Local dashboard with running, queued, needs-attention and done groups. API calls need a rotating
  access token that is only sent after the server proves it holds it; requests are protected against
  cross-site use and DNS rebinding; data files are private to the user.
- `tasky` command line: `ui`, `serve`, `add`, `list`, `done`, `cancel`, `run`, `import`, `status`.
- Idempotent import of existing Claude Code transcripts.
