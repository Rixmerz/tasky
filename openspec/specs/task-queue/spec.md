# task-queue Specification

## Purpose
Let a person queue work for later from the chat, the dashboard or the terminal without spending tokens, optionally let a session work through that queue on its own, and remind a resumed or compacted session of what it still has to do.

## Requirements

### Requirement: Queue from chat without spending tokens
The system SHALL treat a prompt that begins with the queue prefix (default `++`, configurable) as a
request to queue: it SHALL create a `queued` task for that session and working directory and block the
prompt so it never reaches the model, returning a short confirmation to the user.

#### Scenario: Prefixed prompt is queued and blocked
- **WHEN** a session submits "++ write the migration for invoices"
- **THEN** a task with status `queued` and body "write the migration for invoices" exists
- **AND** the hook response blocks the prompt with a reason that names the queued task number

#### Scenario: Empty queue request
- **WHEN** a session submits only the prefix with no text
- **THEN** no task is created and the prompt is blocked with a usage hint

### Requirement: Queue from dashboard or CLI
The system SHALL allow creating queued tasks for a working directory, optionally bound to a session,
and SHALL keep an explicit order among queued tasks that can be changed.

#### Scenario: Add from CLI
- **WHEN** the user runs the add command with text "update the changelog" for a directory
- **THEN** a `queued` task with that body exists for that directory

#### Scenario: Reorder
- **WHEN** a queued task is moved before another queued task
- **THEN** listing queued tasks returns them in the new order

### Requirement: Opt-in queue draining at turn end
When a session has auto-pull enabled, the system SHALL, at the end of a turn, take the next queued
task bound to that session, or else the next unbound queued task for the same working directory, mark
it `running` for that session, and continue the session with that task's text. Tasks in a project's run
queue SHALL never be taken this way. The number of consecutive pulls without a new user prompt SHALL be
capped (default 5). With auto-pull disabled, turn end SHALL never continue the session.

#### Scenario: Pull next task
- **WHEN** auto-pull is enabled and a turn stops while a queued task outside the run queue exists for the session's directory
- **THEN** the task becomes `running` for that session and the hook response continues the session with the task text

#### Scenario: Run queue task is not pulled
- **WHEN** auto-pull is enabled and the only queued task for the directory is in its run queue
- **THEN** the turn ends without pulling

#### Scenario: Chained result lands on the pulled task
- **WHEN** a pulled task's turn stops with message "Migration written"
- **THEN** the pulled task is `done` with that result and the original prompt task's result is unchanged

#### Scenario: Chain cap reached
- **WHEN** the session has already pulled the maximum number of tasks since the last user prompt
- **THEN** the turn ends without pulling, and the queued tasks stay `queued`

#### Scenario: Auto-pull disabled
- **WHEN** auto-pull is disabled and queued tasks exist
- **THEN** the hook produces no continuation

### Requirement: Context recovery on resume and compaction
On a session start whose source is resume or compaction, the system SHALL inject a bounded summary of
that session's unfinished tasks (running, interrupted and queued) as additional context, and SHALL
inject nothing when there are none or when the source is a fresh start.

#### Scenario: Compaction with pending work
- **WHEN** a session is compacted while it has one running and two queued tasks
- **THEN** the session start response contains additional context listing those three tasks

#### Scenario: Nothing pending
- **WHEN** a session resumes with no unfinished tasks
- **THEN** the session start hook produces no output

### Requirement: Serial run queue per project
The system SHALL keep, per working directory, an ordered run queue of tasks with a stored permission
mode. When the queue is not paused and no task started from it is running in that directory, the
system SHALL start the first task in the queue as a worker. It SHALL check this when a task joins the
queue, when a worker exits, when a queue is resumed, and periodically while the dashboard server runs.
A task from the queue that ends `failed` or `interrupted` SHALL pause that queue until it is resumed.

#### Scenario: Next task starts after the previous one
- **WHEN** the running task from a project's run queue finishes `done` and two tasks remain queued there
- **THEN** the first of them starts, and the second stays `queued`

#### Scenario: One at a time
- **WHEN** two tasks join an idle project's run queue at the same moment
- **THEN** exactly one of them is `running`

#### Scenario: Failure pauses the queue
- **WHEN** the running task from a project's run queue ends `failed`
- **THEN** the queue is paused and the next task stays `queued` until the user resumes it

#### Scenario: Projects run independently
- **WHEN** projects A and B each have a task in their run queue and nothing running
- **THEN** both tasks start
