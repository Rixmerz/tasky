## 1. Router core

- [x] 1.1 `router.pinned_choice` and a pin-first path in `router.decide` (router on or off, switches ignored, unknown model ignored); verify unit tests for each pin value
- [x] 1.2 `router.preview_key`, `router.refresh_previews` and `router.preview`; verify tests for once-per-text classification, cached failures, switch changes without reclassifying, and pending after an edit

## 2. Store

- [x] 2.1 Columns `pinned_model`, `pinned_effort`, `preview_key`, `preview_kind`, `preview_note` in the schema and `_migrate_v11_to_v12`; schema version 12; verify migration tests

## 3. Launch

- [x] 3.1 `supervise._route` records the decision when the router is on or the task is pinned; verify argv for a pinned model with the router on and off, a session-default pin, and the unchanged no-router argv

## 4. API and dashboard

- [x] 4.1 `PATCH /api/tasks/<id>` `route` with validation; `route_preview` and `router` in `/api/state`; background preview pass kicked from the version poll, task create/edit and router settings; verify server tests
- [x] 4.2 Preview/pin tag on Inbox and Up next rows; Model and Effort selects in the drawer; refetch the state after a router settings change; verify the row tags in a running dashboard against a temporary `TASKY_HOME` (the drawer selects were not clicked through)

## 5. Docs and verification

- [x] 5.1 README "Model router" section
- [x] 5.2 `ruff check .`, `pytest -q`, `node --test tests/web/digest.test.mjs` green; a stub end-to-end launch shows the pinned flags in `claude`'s argv
