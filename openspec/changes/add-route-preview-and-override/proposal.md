## Why

With the model router on, the user learns which model a task used only after it ran: the row's
model badge appears once the supervisor has classified and launched it. There is no way to see
that a long refactor is about to start on Haiku, or that a quick question will spend Opus `xhigh`,
and no way to say "this one on Opus" short of turning the whole router off.

## What Changes

- **Route preview.** While the router is on, every task waiting in Inbox and Up next shows the
  model and effort it would start on (`opus · xhigh`, `haiku`, or `default` for the session
  default). The classifier's kind is computed once per text in a server background thread and
  cached on the task; model and effort are derived from it when the board is read, so a model
  switch or the router switch shows on every row at once without classifying again.
- **Per-task model.** From the task's side drawer the user picks *Router decides* (the default),
  *Session default*, or a model from `router.MODELS` plus an effort (none for Haiku; `high` is
  the CLI default and is not passed). The choice is stored on the task; the supervisor applies it
  instead of classifying, and records `route_reason` as "…, your choice".
- A chosen model applies whether or not the router is on and whether or not that model's switch
  is on: the switches steer the router's automatic choices, the pin is the user's explicit act
  for one task. A pinned row shows its model regardless of the router switch, so nothing is
  applied invisibly.
- `PATCH /api/tasks/<id>` accepts `route`; `GET /api/state` adds `route_preview` to waiting tasks
  and the router settings.
- Schema 11 → 12: five nullable task columns (`pinned_model`, `pinned_effort`, `preview_key`,
  `preview_kind`, `preview_note`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-router`: a pinned task bypasses classification and is recorded as the user's choice,
  also while the router is off; waiting tasks carry a preview of their route.

## Non-goals

- Choosing the model when the task is created (the composer); the drawer covers any waiting task.
- Reusing the cached preview kind at launch: the supervisor still classifies, so a preview is
  advisory and can differ only if the classifier answers differently for the same text.
- Previews for tasks that are not waiting (running, done): those already show what ran.

## Impact

- `tasky/router.py` (pin, preview, `refresh_previews`), `tasky/supervise.py`, `tasky/store.py`
  (migration v11 → v12), `tasky/server.py` (`route` field, preview thread, state), `tasky/web/`.
- No new dependencies. Classification still runs only outside HTTP handlers and the scheduler
  tick.
