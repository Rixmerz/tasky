## 1. Classifiers

- [x] 1.1 Write `tasky/router/task-kind.json`, `debug-kind.json`, `build-kind.json`, `review-kind.json` with keyword rules first and canonical Laya questions; verify `mm-classifier test` on each prints no lint warnings
- [x] 1.2 Label real prompts from the Tasky database as examples; verify `mm-classifier test` and report the rule and Laya halves

## 2. Router core

- [x] 2.1 `tasky/router.py`: routing table, choice under model switches, flag building, classifier resolution and call with a 10 s timeout, frontier-only-by-rule; verify unit tests for every requirement scenario with an injected classifier runner
- [x] 2.2 `Store`: `model`, `effort`, `route_reason` columns and migration; router settings in `meta`; verify store and migration tests

## 3. Launch

- [x] 3.1 `supervise`: when enabled, route before starting `claude`, append flags, record the decision; verify supervise tests for routed, disabled and failing-classifier launches

## 4. API and dashboard

- [x] 4.1 `GET`/`PATCH /api/router` with token gate and validation; verify server tests
- [x] 4.2 Router section in the settings panel and the model/effort badge on task rows; verify in the running dashboard

## 5. Docs and verification

- [x] 5.1 README section, CHANGELOG 0.4.0, version bumps
- [x] 5.2 `ruff check .` and `pytest -q` green; one live routed launch end to end
