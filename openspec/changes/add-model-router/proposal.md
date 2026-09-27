## Why

Every task Tasky launches runs on the session's default model and effort, whatever it asks for. A
commit message and a cross-module architecture change get the same model; so does a "where is X"
search and a race-condition hunt. Anthropic's guidance maps kinds of work to a model and an effort
level (Opus 5 `xhigh` to plan and to debug unclear failures, Sonnet 5 `high` to implement and build
UI, Sonnet `medium` for docs, Haiku to explore), and Tasky is the one place that starts headless
work on the user's behalf, so it can apply that mapping at launch.

## What Changes

- A model router, **off by default**. When on, each task Tasky launches (Run now, After last,
  Parallel with context) is classified by kind, and its `claude -p` process is started with the
  `--model` and `--effort` the routing table gives that kind.
- The kind comes from a chain of `mm-classifier` definitions committed in `tasky/router/`:
  keyword rules first, a local Laya model for the rest. Tasky shells out to muscle-memory's
  `mm-classifier` bundle; it never starts the Laya server.
- Each kind has a first and a second choice. A **per-model switch** (Fable 5.1, Opus 5, Sonnet 5,
  Haiku 4.5) lets the user forbid a model: the router then takes the second choice, and if both are
  forbidden it passes no flags at all.
- Whenever the router cannot decide — off, classifier missing, Laya down, timeout, unknown kind —
  the launch command is exactly what it is today.
- The decision is stored on the task (`model`, `effort`, `route_reason`) and shown on its row.
- A Router section in the dashboard's settings panel: master switch, the four model switches, and
  whether `mm-classifier` and the Laya server were found.
- Schema: three nullable task columns, added in place.

## Capabilities

### New Capabilities

- `model-router`: classifying a launched task, choosing model and effort from the routing table
  under the user's model switches, and the settings that govern it.

### Modified Capabilities

None. The launch command is unchanged whenever the router does not decide.

## Non-goals

- Routing subagents or the user's own interactive sessions (would need a PreToolUse hook on
  `Agent`; its effort cannot be set per call).
- A per-task override in the UI, and a routing preview when a task is created.
- Starting or managing the Laya server.

## Impact

- New `tasky/router.py`, `tasky/router/*.json`; edits to `tasky/supervise.py`, `tasky/store.py`,
  `tasky/server.py`, `tasky/web/`.
- Optional runtime dependencies, only when the router is on: `node`, the muscle-memory plugin's
  `mm-classifier.mjs`, and a running Laya server for tasks no rule covers. Tasky itself stays
  dependency-free.
