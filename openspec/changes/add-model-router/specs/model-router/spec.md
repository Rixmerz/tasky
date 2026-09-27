## Purpose

Start each task Tasky launches on the model and effort level suited to its kind of work, under
model switches the user controls, without ever changing a launch the router cannot decide.

## ADDED Requirements

### Requirement: The router SHALL be off by default and never alter an undecided launch
The router SHALL be disabled until the user enables it. While disabled, and whenever it cannot
decide — the classifier binary or `node` is missing, the classifier exits non-zero or takes longer
than 10 seconds, its output cannot be parsed, or the kind maps to no allowed model — the `claude`
command a worker runs SHALL be byte-identical to the command Tasky runs without a router. While
the router is enabled, the task's `route_reason` SHALL say why no route was applied; while it is
disabled, the task's routing fields SHALL stay empty.

#### Scenario: Router off
- **WHEN** a task is launched while the router is disabled
- **THEN** the worker's `claude` arguments contain neither `--model` nor `--effort`

#### Scenario: Classifier unavailable
- **WHEN** the router is enabled but no `mm-classifier.mjs` can be found
- **THEN** the worker starts with no `--model`/`--effort`, and the task's `route_reason` names the
  missing classifier

### Requirement: A launched task SHALL be classified into one kind
With the router enabled, the worker supervisor SHALL, before starting `claude`, run the
`tasky/router/task-kind.json` classifier chain, whose final catch-all rule sends any task no other
rule recognises to `default` without contacting a Laya server, on `{"title", "body"}` of the task and read the
final decision's outcome as the task's kind. The kinds are `plan`, `debug-unclear`, `debug-repro`,
`feature`, `implement`, `ui`, `refactor`, `review-critical`, `review-routine`, `test`, `docs`,
`explore`, `chat`, `frontier` and `default`. `frontier` SHALL only be accepted when a rule decided
it (the task text carries the `#fable` tag); a `frontier` chosen by the model SHALL be treated as
`default`. Classification SHALL happen in the detached supervisor, never in the HTTP handler or the
scheduler tick.

#### Scenario: A rule decides the kind
- **WHEN** a task body reads "haz commit y push de los cambios"
- **THEN** its kind is `docs`, decided by a rule, without contacting the Laya server

#### Scenario: Frontier only by tag
- **WHEN** the classifier's model, not a rule, labels a task `frontier`
- **THEN** the kind is treated as `default` and no Fable route is applied

### Requirement: The routing table SHALL give each kind a first and a second choice
Each kind SHALL map to an ordered pair of (model, effort) choices:

| Kind | First | Second |
|---|---|---|
| `plan` | opus `xhigh` | sonnet `xhigh` |
| `debug-unclear` | opus `xhigh` | sonnet `high` |
| `debug-repro` | sonnet `high` | sonnet `medium` |
| `feature` | opus `xhigh` | sonnet `high` |
| `implement` | sonnet `high` | sonnet `medium` |
| `ui` | sonnet `high` | sonnet `medium` |
| `refactor` | sonnet `medium` | none |
| `review-critical` | opus `high` | sonnet `high` |
| `review-routine` | sonnet `medium` | haiku |
| `test` | sonnet `medium` | haiku |
| `docs` | sonnet `medium` | haiku |
| `explore` | haiku | sonnet `low` |
| `chat` | sonnet `low` | haiku |
| `frontier` | fable `high` | opus `max` |
| `default` | none | none |

The router SHALL apply the first choice whose model is allowed, else the second, else none. An
applied choice SHALL add `--model <alias>` (`fable`, `opus`, `sonnet`, `haiku`) and, unless the
effort is `high` or the model is `haiku`, `--effort <level>`.

#### Scenario: First choice allowed
- **WHEN** a `plan` task is launched and Opus is allowed
- **THEN** the worker runs `claude` with `--model opus --effort xhigh`

#### Scenario: High effort is not passed
- **WHEN** an `implement` task is launched and Sonnet is allowed
- **THEN** the worker runs `claude` with `--model sonnet` and no `--effort`

#### Scenario: First choice forbidden
- **WHEN** a `plan` task is launched, Opus is forbidden and Sonnet allowed
- **THEN** the worker runs `claude` with `--model sonnet --effort xhigh`

#### Scenario: Haiku takes no effort
- **WHEN** an `explore` task is launched and Haiku is allowed
- **THEN** the worker runs `claude` with `--model haiku` and no `--effort`

#### Scenario: Both choices forbidden
- **WHEN** a `frontier` task is launched and both Fable and Opus are forbidden
- **THEN** no `--model`/`--effort` is added and `route_reason` says both choices were disabled

### Requirement: The decision SHALL be recorded on the task
The applied model and effort (or null) and a one-line `route_reason` naming the kind and whether a
rule or the model decided it SHALL be stored on the task before `claude` starts, and returned with
the task by the API.

#### Scenario: Recorded route
- **WHEN** a `docs` task is routed to Sonnet `medium`
- **THEN** the task has `model` "sonnet", `effort` "medium" and a `route_reason` naming `docs`

### Requirement: Router settings SHALL be stored server-side and editable from the dashboard
The router switch and one switch per model (`fable`, `opus`, `sonnet`, `haiku`, all allowed by
default) SHALL be stored in Tasky's database. `GET /api/router` SHALL return them with the resolved
classifier path (or null) and whether the Laya server answers; `PATCH /api/router` SHALL accept
`enabled` and `models` booleans and reject anything else with 400. Both SHALL require the dashboard
token. The dashboard's settings panel SHALL show a Router section with these switches and status.

#### Scenario: Forbid Fable
- **WHEN** `PATCH /api/router` is sent `{"models": {"fable": false}}`
- **THEN** `GET /api/router` reports Fable forbidden, and no later launch is routed to Fable

#### Scenario: Invalid setting
- **WHEN** `PATCH /api/router` is sent `{"models": {"gpt": true}}`
- **THEN** it answers 400 and nothing is stored
