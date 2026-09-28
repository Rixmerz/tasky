## Context

See proposal.md. Classifying a task spawns `node mm-classifier.mjs` (~0.3–1 s, 10 s timeout), so
it must never run in an HTTP handler or the scheduler tick. The dashboard polls `/api/version`
and refetches `/api/state` when the revision moves; any `UPDATE tasks` bumps the
revision through a trigger. Router settings live in `meta`, which has no revision trigger.

## Goals / Non-Goals

**Goals:** show the route a waiting task would take without stalling the dashboard; let the user
fix one task's model; keep every undecided launch byte-identical to no router.

**Non-Goals:** see proposal.md.

## Decisions

**Cache the kind, derive the model at read time.** A task stores the classifier's *kind*
(`preview_kind`, with `preview_note` = `rules`/`model` or the failure) under `preview_key`, a
SHA-256 of the title, body, resolved classifier path and a digest of `tasky/router/*.json`.
`/api/state` turns the kind into model and effort with the pure `router.choose` under the current
switches. Alternatives rejected:

- *Lazy per-task endpoint with an in-memory cache*: the classification would run inside a
  request thread (forbidden), the cache dies with the server, and a board of 30 waiting tasks
  would fan out 30 requests.
- *Store the resolved model*: every switch toggle would need a reclassification pass, and a
  stale model would show until it finished.

The key covers everything the kind depends on: edited text, a newly installed or upgraded
classifier, new committed rules. A failure (classifier missing, non-zero exit, timeout) is cached
under the key too, so a broken classifier is spawned once per text, not every poll; installing
muscle-memory changes the path and so the key. A task whose key no longer matches shows
`pending` (`…`) until the next pass.

**One background pass at a time, kicked, never awaited.** `_Server.kick_previews` starts a daemon
thread when the router is on and no pass is running (non-blocking lock, as `sync_titles` does); a
kick during a pass is dropped. Kicks come from the version poll (every 3 s at most, next to the
queue kick), task creation, task edits (`title`, `body`, `route`) and router settings changes.
The pass opens its own store, classifies each stale, unpinned, queued task in turn and writes
only the three preview columns, so a concurrent claim or edit is never overwritten: a task
edited mid-classification gets the old key and is simply redone next pass. The loop body is the
synchronous `router.refresh_previews`, tested directly with an injected runner.

**The pin is two columns.** `pinned_model` is null (router decides), `"default"` (session
default, no flags) or an alias in `router.MODELS`; `pinned_effort` is null or one of
`router.EFFORTS`, ignored for Haiku. `PATCH /api/tasks/<id>` takes `route`: `null`, `"default"`
or `{"model", "effort"?}`; Haiku with an effort, an unknown model or effort, or any other shape is
a 400 and changes nothing. A stored pin naming a model no longer in `MODELS` is ignored (the task
falls back to routing) rather than failing the launch.

**A pin beats both switches.** The router switch and the model switches govern automatic
choices. A pin is an explicit per-task act, taken in a menu that labels switched-off models
"(off for the router)", and its row shows the pin even with the router off. Refusing it (or
silently falling back) would make the drawer lie about what runs. So `router.decide` checks the
pin first, before the enabled check, and the supervisor records the decision when the router is
on *or* the task is pinned. A `"default"` pin records `route_reason` "session default, your
choice" with an argv identical to no router.

**Supervisor still classifies.** The launch does not trust the cached preview: the classifier
chain may call a Laya model whose answer is not guaranteed stable, and the launch path stays
exactly as specified. The preview is advisory.

**Frontend.** Rows in Inbox and Up next show `route_preview` (the pin with a stronger outline,
`…` while pending); other columns keep showing what ran. The drawer's Task section gains a Model
select (Router decides, Session default, then the server's model list) and an Effort select shown
only for models that take one, defaulting to `high`. After a router settings change the dashboard
refetches the state, since `meta` writes do not move the revision.

## Risks / Trade-offs

- Enabling the router with many waiting tasks spawns one `node` per task in the background
  (~0.3–1 s each, serial). Acceptable: it happens once per text and never blocks a request.
- The version poll only kicks passes while a dashboard is open; a task added by the `++` hook
  with no dashboard open gets its preview when one opens, which is when it is needed.
- A pin survives "Run again" and moving back to Inbox; the user clears it from the drawer.
