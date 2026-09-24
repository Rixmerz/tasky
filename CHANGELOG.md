# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.12.0] - 2026-09-24

### Added

- Quick diagram: draws the mapped areas and the imports between them (JavaScript, TypeScript,
  Python, Go) with Archify's CLI, no model and no cost, in layers, validated against the pinned
  commit and repaired from Archify's diagnostics; the page opens inside the Architecture tab.
  Also `tasky architecture --quick`.

### Changed

- Draw with Archify runs at effort low: on the same repository it cost $2.13 in 8 minutes against
  $2.22 in 14 minutes at the default effort, with the same diagram. Its hint now says ~$2–3.

## [0.11.1] - 2026-09-24

### Changed

- An embedded diagram names the dashboard's origins in `frame-ancestors` instead of `'self'`,
  which a sandboxed page's opaque origin does not reliably match in every browser.

## [0.11.0] - 2026-09-24

### Added

- History sync uses the repository's areas: a problem's or milestone's topic is the area it
  belongs to (an alias becomes the area's name), records may name the specs they are about, and
  words the developer wrote for an area become aliases (never on an area the developer edited).
- Problems and milestones link to areas through their specs, and each spec lists them.
- `search_history` climbs a fixed ladder of free steps: every word plus the areas the question
  names by name, alias or the start of one; then any word; then the areas that have history.
- View shows an Archify diagram inside the Architecture tab, sandboxed and served from a
  short-lived link.

### Changed

- History sync runs at effort `high` and Map areas at effort `medium`, chosen by measuring the same
  real prompts at every level: lower sync efforts dropped the chain of attempts.
- Area names and aliases are matched without accents ("sesion" finds "sesión").

## [0.10.0] - 2026-09-24

### Added

- Draw with Archify: when the Archify skill is installed, one click starts a headless session that
  draws the repository's architecture from code evidence (Tasky's areas as boundary names) into
  `docs/architecture/`, limited to edits in the checkout, Archify's CLI and read-only git. The
  repository is scanned again when it finishes, and Open shows the diagram in the browser.
- Scans list Archify diagrams and their rendered pages.

## [0.9.0] - 2026-09-24

### Added

- Architecture view: per repository, its areas (business or technical parts, with aliases, folders
  and kind), the specs and decisions that describe each one, its History problems and milestones,
  and how much work touched it (turns, files, last edit), counted from every edit Tasky copied.
- Scan (free) reads OpenSpec capabilities and changes, spec-kit features, Kiro specs, ADRs in
  Nygard, MADR and home-grown styles, Archify `*.architecture.json` and Graphify
  `graphify-out/graph.json`. A repository with no areas adopts Archify boundaries or Graphify
  communities.
- Map areas: one Sonnet or Opus call proposes the vocabulary; paths and specs it returns are
  checked against the scan, and areas you edited keep their name and description.
- Areas can be added, renamed, edited and deleted from the dashboard (`/api/areas`).
- Board cards and the task panel show the areas of the files a task edited.
- MCP tool `get_architecture` and CLI command `tasky architecture [--map]`.

## [0.8.1] - 2026-09-24

### Fixed

- The repair also merges a finished task whose text Claude Code had absorbed into another turn:
  sessions still running pre-0.8.0 hooks kept giving such messages the turn's reply. A text that was
  also sent as a prompt of its own stays a task.

## [0.8.0] - 2026-09-24

### Fixed

- A prompt cancelled with Esc before Claude replied no longer stays on the board, whatever you send
  next: the transcript proves there was no reply and no tool call, so the prompt is dropped. Before,
  only a resend at least 80% similar was caught, so cancelling to rewrite a prompt left a
  duplicate. A prompt Claude had started working on is kept and marked interrupted.
- A message typed while Claude is working no longer becomes a separate task that takes the turn's
  reply and leaves the real task "interrupted" with none. It is a follow-up of the running task.
  This is why Needs attention filled up with tasks that had been answered.
- Prompts a subagent receives, subagents' messages to their parent (`<agent-message>`) and session
  commands such as `/compact …` are no longer recorded as tasks.
- Assistant messages carry no prompt id in the transcript, so a turn's files could not be found.
  They now take the id of the prompt before them.
- The "needs answer" mark shows only on the newest task of a session: an older reply ending in a
  question was already answered by the next prompt.
- Titles of prompts that start with a paste no longer read `<pasted_content …>`.
- The `last_session` MCP tool skips sessions with no recorded prompt.

### Added

- Smart search: after the plain results, a button asks Haiku which tasks match the question by
  meaning, with a one-line reason each. About 6¢ the first time and 2–3¢ after, thanks to the
  prompt cache; it runs only when you press it.
- Recaps: Claude Code's summaries when you come back to a session are copied, shown large above the
  board for each open session, placed among the tasks where they happened, and returned by the
  `last_session` MCP tool.
- Tokens per task and per session, subagents included, and files edited per task, from the
  transcripts at no cost.
- Cards show the start of the reply, duration, files edited, output tokens and follow-ups. Needs
  attention says why each task is there. Done is grouped by day and session. Empty columns fold
  into a thin strip.
- A warning for open sessions still running an older tasky's hooks (Claude Code loads a plugin when
  a session starts): restart them to get the fixes.
- The permission mode is chosen in the composer when adding a task and kept on it.
- Delete hides a task instead of erasing it, with Undo; the history sync still learns from it.
- Subagent transcripts are copied too.

### Changed

- Schema version 6. The upgrade repairs existing rows: split turns are merged into their first task,
  prompts proved unanswered are dropped, recorded session commands are removed, and transcripts are
  read again from the start to fill in tokens and recaps (messages are not copied twice). Import
  history runs the repair again once old transcripts are copied.
- The dashboard no longer shows `++`; the chat prefix is unchanged (`TASKY_QUEUE_PREFIX`).

## [0.7.2] - 2026-09-24

### Fixed

- The `/compact` copy buttons copy the instructions only. Claude Code collapses a long paste into
  "[Pasted text]", so a `/compact` inside it was sent as a plain message instead of running; type
  `/compact`, a space, then paste.

## [0.7.1] - 2026-09-24

### Changed

- Suggested `/compact` instructions are always in English and telegraphic (names, paths, ids and
  quotes stay verbatim), and ask for a terse summary that still keeps each decision's reason and
  each failed attempt's cause in full: the summary stays in context for the rest of the session,
  so that is where brevity pays; the reasons are what stop a failed fix from being retried.
  Problems and milestones stay in the developer's language.

## [0.7.0] - 2026-09-24

### Added

- Full conversations: every message, tool call and tool result of every session is copied into the
  ledger at the end of each turn, before each compaction (new `PreCompact` hook) and at session
  end, incrementally and with a per-call byte budget, so it survives Claude Code's 30-day
  transcript cleanup. `tasky import` and the dashboard's Import history copy existing transcripts.
  Files touched by Edit/Write/Read calls are recorded with each message.
- Secret scrubbing before anything is stored: private keys, provider keys and tokens, JWTs, bearer
  tokens and password-style assignments become `[redacted]`.
- Suggested `/compact` instructions per open session, produced by the history sync in the same
  model call, shown under "Suggested /compact" in the History panel and as "Copy /compact" in the
  task side panel.
- MCP tools `search_conversations` (accent-insensitive search over every stored message, with the
  turns around each hit) and `last_session`.
- A favicon.
- A Credits section in the README naming the projects Tasky builds on or borrows from, with their
  licenses.

### Changed

- Schema version 5: `messages`, `messages_fts` and `transcript_offsets` tables; sessions gain
  `compact_prompt` and `compact_at`.

## [0.6.1] - 2026-09-24

### Removed

- Haiku as a history sync model. The sync has to judge whether a fix really failed across tasks
  days apart; Haiku recorded an unapplied idea as a solution in testing, and saved only about two
  cents per sync because the fixed instructions dominate the cost. Sonnet (default) and Opus remain.

## [0.6.0] - 2026-09-24

### Added

- Problems keep the ordered chain of attempts made to fix them: outcome (worked, failed, partial,
  pending), why it failed, the dates it was believed correct and shown wrong, a verbatim evidence
  quote checked against the cited task, and the tasks and commits it came from. Problems can be
  open, solved or recurring.
- History is grouped by repository (its `origin` remote, else the main checkout), so worktrees and
  clones share it, and can be viewed per repository or across all of them.
- The History panel has four views: Timeline, Problems, Dead ends and a Map
  (repository → topics → problems and milestones → attempts), plus a text filter.
- The sync model is chosen per sync (Haiku, Sonnet or Opus); Sonnet is the default.
- An MCP server, `tasky`, bundled with the plugin: `search_history` (accent-insensitive full-text
  search), `get_problem`, `dead_ends`, `search_tasks` and `record_attempt`, which lets the agent
  record an attempt and its outcome without a sync.

### Changed

- Schema version 4 replaces the 0.5.0 history tables; existing records are migrated (a 0.5.0 dead
  end becomes a problem whose first attempt failed) and the sync cursor carries over.
- `/api/insights` is replaced by `GET /api/history` and `POST /api/history/sync`;
  `TASKY_INSIGHTS_*` by `TASKY_HISTORY_MODEL` and `TASKY_HISTORY_MAX_BATCHES`.

### Fixed

- A model numbering new records itself no longer makes the sync drop them: an id it was not shown
  is treated as a new record.

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
