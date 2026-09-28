## MODIFIED Requirements

### Requirement: The router SHALL be off by default and never alter an undecided launch
The router SHALL be disabled until the user enables it. While disabled, and whenever it cannot
decide — the classifier binary or `node` is missing, the classifier exits non-zero or takes longer
than 10 seconds, its output cannot be parsed, or the kind maps to no allowed model — the `claude`
command a worker runs SHALL be byte-identical to the command Tasky runs without a router, unless
the user chose a model for that task (see "The user SHALL be able to choose a waiting task's
model"). While the router is enabled, the task's `route_reason` SHALL say why no route was
applied; while it is disabled, the routing fields of a task without a user choice SHALL stay
empty.

#### Scenario: Router off
- **WHEN** a task with no model chosen by the user is launched while the router is disabled
- **THEN** the worker's `claude` arguments contain neither `--model` nor `--effort`, and the
  task's `model`, `effort` and `route_reason` stay empty

#### Scenario: Classifier unavailable
- **WHEN** the router is enabled but no `mm-classifier.mjs` can be found
- **THEN** the worker starts with no `--model`/`--effort`, and the task's `route_reason` names the
  missing classifier

### Requirement: A launched task SHALL be classified into one kind
With the router enabled and no model chosen by the user for the task, the worker supervisor SHALL,
before starting `claude`, run the `tasky/router/task-kind.json` classifier chain, whose final
catch-all rule sends any task no other rule recognises to `default` without contacting a Laya
server, on `{"title", "body"}` of the task and read the final decision's outcome as the task's
kind. The kinds are `plan`, `debug-unclear`, `debug-repro`, `feature`, `implement`, `ui`,
`refactor`, `review-critical`, `review-routine`, `test`, `docs`, `explore`, `chat`, `frontier` and
`default`. `frontier` SHALL only be accepted when a rule decided it (the task text carries the
`#fable` tag); a `frontier` chosen by the model SHALL be treated as `default`. Classification
SHALL happen in the detached supervisor or in the dashboard server's background preview pass,
never in an HTTP handler or the scheduler tick. A task with a model chosen by the user SHALL NOT be
classified at launch.

#### Scenario: A rule decides the kind
- **WHEN** a task body reads "haz commit y push de los cambios"
- **THEN** its kind is `docs`, decided by a rule, without contacting the Laya server

#### Scenario: Frontier only by tag
- **WHEN** the classifier's model, not a rule, labels a task `frontier`
- **THEN** the kind is treated as `default` and no Fable route is applied

#### Scenario: A pinned task is not classified
- **WHEN** a task whose user-chosen model is Sonnet is launched with the router enabled
- **THEN** the supervisor does not run the classifier

### Requirement: The decision SHALL be recorded on the task
The applied model and effort (or null) and a one-line `route_reason` SHALL be stored on the task
before `claude` starts, and returned with the task by the API. For a routed task the reason SHALL
name the kind and whether a rule or the model decided it; for a task whose model the user chose it
SHALL say it was the user's choice, and it SHALL be recorded whether or not the router is enabled.

#### Scenario: Recorded route
- **WHEN** a `docs` task is routed to Sonnet `medium`
- **THEN** the task has `model` "sonnet", `effort` "medium" and a `route_reason` naming `docs`

#### Scenario: Recorded user choice
- **WHEN** a task the user pinned to Opus `max` is launched while the router is disabled
- **THEN** the task has `model` "opus", `effort` "max" and `route_reason` "opus max, your choice"

## ADDED Requirements

### Requirement: The user SHALL be able to choose a waiting task's model
A task SHALL carry an optional user choice: *router decides* (the default), *session default*, or
one model from the router's model list (`fable`, `opus`, `sonnet`, `haiku`) with an optional effort
from `low`, `medium`, `high`, `xhigh`, `max`. `PATCH /api/tasks/<id>` SHALL accept `route` as
`null` (router decides), `"default"` (session default) or `{"model": <alias>, "effort"?: <level>}`,
and SHALL answer 400 without changing the task for an unknown model or effort, an effort for
Haiku, or any other value. When a task with a choice is launched, the supervisor SHALL apply it
instead of classifying: a model adds `--model <alias>` and, unless the effort is absent or `high`
or the model is `haiku`, `--effort <level>`; *session default* adds nothing. The choice SHALL apply
whether or not the router is enabled and whether or not that model's switch is on — the switches
govern only the router's own choices. A stored choice naming a model the router no longer knows
SHALL be ignored, as if the router decides. The dashboard's task drawer SHALL offer the choice for
waiting tasks, listing only the router's models and marking those switched off.

#### Scenario: Pin a model and effort
- **WHEN** `PATCH /api/tasks/7` is sent `{"route": {"model": "opus", "effort": "xhigh"}}` and task
  7 is launched
- **THEN** the worker runs `claude` with `--model opus --effort xhigh` without classifying

#### Scenario: Session default
- **WHEN** a task's choice is `"default"` and it is launched with the router enabled
- **THEN** the worker's `claude` arguments are exactly those Tasky runs without a router, and
  `route_reason` says the session default was the user's choice

#### Scenario: Choice with the router off
- **WHEN** a task pinned to Haiku is launched while the router is disabled
- **THEN** the worker runs `claude` with `--model haiku` and no `--effort`

#### Scenario: Choice of a switched-off model
- **WHEN** a task pinned to Fable `high` is launched while Fable's switch is off
- **THEN** the worker runs `claude` with `--model fable` and no `--effort`

#### Scenario: Invalid choice
- **WHEN** `PATCH /api/tasks/7` is sent `{"route": {"model": "haiku", "effort": "low"}}`
- **THEN** it answers 400 and task 7's choice is unchanged

### Requirement: A waiting task SHALL show the route it would take
For every queued task, `GET /api/state` SHALL return a `route_preview` that is null when launching
it now would add no flags because the router is disabled and the user made no choice, and
otherwise an object with `source` (`pinned`, `router` or `pending`), `model`, `effort` and a
`reason`. A `router` preview SHALL be derived from a kind cached on the task and the current model
switches, so changing a switch or the router switch changes previews without classifying again.
The cached kind SHALL be computed in a background pass of the dashboard server, never in an HTTP
handler, once per combination of task title, body, classifier path and committed classifier
definitions; a classification failure SHALL be cached the same way. A task whose cached kind does
not match its current text SHALL preview as `pending`. The response SHALL also carry the router
settings. Rows in Inbox and Up next SHALL show the preview as `model · effort`, `haiku`, or
`default` for the session default, and SHALL mark a user choice as such.

#### Scenario: Preview follows a switch
- **WHEN** a waiting task's cached kind is `plan` and the user switches Opus off
- **THEN** the next `GET /api/state` previews it as Sonnet `xhigh` without running the classifier

#### Scenario: Preview of an edited task
- **WHEN** a waiting task's body is edited after its kind was cached
- **THEN** it previews as `pending` until the background pass classifies the new text

#### Scenario: Router off
- **WHEN** the router is disabled
- **THEN** waiting tasks without a user choice have a null `route_preview`, and a task pinned to
  Opus `xhigh` previews as Opus `xhigh` from `pinned`

#### Scenario: Classifier missing
- **WHEN** the router is enabled, no `mm-classifier.mjs` can be found and a task is created
- **THEN** the background pass records the failure once, and the task previews as the session
  default with a reason naming the missing classifier
