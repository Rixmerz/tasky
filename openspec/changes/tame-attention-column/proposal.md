## Why

On a real board, most of Needs attention is old: interrupted or failed turns from days ago that the
user never got back to, sitting next to today's genuinely urgent ones. A column that always shows a
pile of stale cards stops meaning anything, and for a user with ADHD a column that never empties is a
column the eye learns to skip. Separately, some of what lands in Needs attention is not a real task at
all: a short nudge like "--continue" or "vuelve a abrila", sent to push an interrupted or failed turn
along, currently becomes a row of its own instead of reopening the turn it was about.

## What Changes

- The Needs attention column header gets an "Archive older than a day" action, confirmed in-page (no
  browser `confirm()`), that hides every attention card older than a day at once and offers Undo.
- When Needs attention holds cards from more than one project, they are grouped by project the way
  Done already groups its cards, collapsed and expanded independently; one project stays a flat list.
- A capture-time fix: a message that only nudges a turn along ("--continue", "sigue", "continúa", "go
  on", "vuelve a <verb>") and names no work of its own now reopens the interrupted or failed turn it
  follows instead of becoming a new task. A nudge with no such turn to reopen, or one that follows a
  turn that ended cleanly, still becomes its own task, and a message that only starts the same way as
  a nudge but goes on to say something else is never folded.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard`: bulk archive of stale attention cards (reusing soft delete), project grouping in Needs
  attention.
- `task-capture`: a continuation nudge reopens the turn it nudges instead of becoming its own task.

## Impact

- Code: `tasky/store.py` (`hide_tasks`, `restore_tasks`), `tasky/server.py` (`POST /api/tasks/hide`,
  `POST /api/tasks/restore`), `tasky/prompts.py` (`is_continuation_nudge`), `tasky/hooks.py`
  (`_user_prompt_submit`), `tasky/web/index.html`, `tasky/web/app.js`, `tasky/web/app.css`.
- Data: no schema change; reuses the existing `deleted_at` soft-delete column.
- Tests: `tests/test_store.py`, `tests/test_server.py`, `tests/test_hooks.py`.
