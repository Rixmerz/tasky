## Why

The same fact currently shows up three times on the board. Any live session running an older
tasky is announced by a yellow banner ("4 sessions are running an older tasky... Show which"), a
matching "older tasky" badge on that session's Done group heading and on its recap card, and the
banner's own hidden list repeats the sentence again per session. Separately, the newest recap of
an open session renders twice: once in the "Latest recap" panel above the columns, and again as a
"Recap · 04:20 AM" row inside its Done group, because both are built from the same `state.recaps`
independently. For a user with ADHD, repeated notices are noise that hides what is actually new;
the board should say each fact once, where it is most useful to act on.

## What Changes

- The outdated-hooks banner becomes the single, actionable place to learn about stale sessions: it
  lists each one (title · project) with a button to copy its resume command and a button to jump
  to its latest task, reusing the same `claude --resume` command already built for the task
  drawer. The badge on a Done group heading and on a recap card becomes a quiet dot with a
  tooltip instead of repeating "older tasky" as text, since the banner already spells it out.
- Dismissing the banner persists across a refresh, keyed by which old tasky versions are currently
  running (not by which sessions): restarting some of several sessions on an already-known old
  build keeps it dismissed; a genuinely new old version reopens it.
- The "Latest recap" panel keeps the newest recap of each open session; the same recap no longer
  also appears as a row inside that session's Done group on the same day. Older recaps for that
  session still appear in Done exactly as before -- only the one already shown at the top is
  suppressed.
- The pure decision logic (which sessions are stale, the dismissal key, which recaps the top panel
  picked, and the resume command) moves into a new `tasky/web/notices.js` module, the same
  DOM-free, node-testable shape `digest.js` already uses, so `app.js` and its tests share one
  source of truth instead of app.js computing it inline in two places.

## Capabilities

### Modified Capabilities

- `dashboard`: "Board shows what needs the user" changes what the outdated-hooks flag and the
  latest-recap panel actually render, without changing the rest of the board.

## Non-goals

- Changing how a session's hooks are detected as outdated (`hook_version` vs. the server's own
  version) -- only how that fact is presented.
- Touching the task drawer's own "Older tasky" note on a session's detail line: that is read only
  when the user deliberately opens a task, not passive board noise, so it keeps its full text.
- Any change to the stored state payload (`/api/state` already ships `recaps`, `sessions[].
  hook_version` and `version`); this is a client-only change.

## Impact

- New `tasky/web/notices.js` (+ `tests/web/notices.test.mjs`); edits to `tasky/web/app.js` and
  `tasky/web/app.css`.
- `tasky/server.py`: one line adding `/notices.js` to the static file allowlist, and a matching
  test in `tests/test_server.py`.
