## 1. Bulk archive

- [x] 1.1 Store: `hide_tasks`/`restore_tasks`, same semantics as `hide_task`/`restore_task` (hides
      delegations, cancels a queued task) for many ids in one transaction; tests for hiding, restoring,
      and unknown ids among the given ones
- [x] 1.2 Server: `POST /api/tasks/hide` and `POST /api/tasks/restore`, `{"ids": [...]}` bodies, 400 on
      a malformed or empty list; tests for status codes, auth, and that hidden ids drop out of
      `/api/state`
- [x] 1.3 Board: header button on Needs attention, shown only when a card is older than a day; an
      in-page confirm strip (no `confirm()`); Undo toast restoring exactly the archived ids

## 2. Project grouping

- [x] 2.1 Board: group Needs attention cards by project, reusing Done's group component (swatch, tint
      cycle, collapsible), only when more than one project is present; default open

## 3. Continuation nudges

- [x] 3.1 `tasky/prompts.py`: `is_continuation_nudge`, a narrow, whole-message match (not a prefix) so
      a real request that happens to start the same way is never folded
- [x] 3.2 `tasky/hooks.py`: a nudge with a previous prompt task in the session that is `interrupted` or
      `failed` reopens it (adds the nudge as a followup, sets it `running`) instead of creating a task
- [x] 3.3 Tests: a nudge folds into an interrupted/failed turn, does not fold with no previous turn or
      after a cleanly finished one, a look-alike longer message is not folded, a real repeated one-word
      command ("next") is not folded, and the reopened task reaches Stop and finishes normally

## 4. Verification

- [x] 4.1 `pytest -q`, `ruff check .`, `node --test tests/web/digest.test.mjs`
- [x] 4.2 `openspec validate tame-attention-column --strict`
