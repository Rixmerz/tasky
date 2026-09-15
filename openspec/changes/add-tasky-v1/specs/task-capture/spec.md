## ADDED Requirements

### Requirement: Real user prompts become tasks
The system SHALL record each user prompt submitted to a Claude Code session as a task in state
`running`, linked to the session, its working directory and the prompt identifier. Prompts that are
system notifications, local command output, command caveats, or slash commands without arguments
SHALL NOT create tasks.

#### Scenario: Plain prompt is recorded
- **WHEN** a session submits the prompt "refactor the login flow"
- **THEN** a task titled from that prompt exists with status `running` and the session's working directory

#### Scenario: Slash command with arguments is recorded
- **WHEN** a session submits a slash command `/review` with arguments "the payment module"
- **THEN** a running task exists whose title contains "/review the payment module"

#### Scenario: System text is ignored
- **WHEN** a session submits text that starts with a task notification or local command output tag
- **THEN** no new task is created

### Requirement: Turn completion records the result
The system SHALL mark the most recently started running task that belongs to the finished turn as
`done` and store the final assistant message as its result, truncated to a bounded length. A task
that still has a running delegation SHALL keep status `running` while its interim result is stored.
A turn that ends in an API failure SHALL mark the task `failed`.

#### Scenario: Turn finishes
- **WHEN** a running prompt task's turn stops with final message "Done, tests pass"
- **THEN** the task status is `done` and its result is "Done, tests pass"

#### Scenario: Turn finishes while a subagent is still working
- **WHEN** a turn stops while one of its delegations is still `running`
- **THEN** the prompt task remains `running` and stores the final message as its interim result

#### Scenario: Turn fails
- **WHEN** a turn ends with a stop failure event
- **THEN** the running task of that turn has status `failed`

### Requirement: Delegations are tracked as child tasks
The system SHALL record each Agent tool call as a delegation task whose parent is the prompt task of
the same turn, and SHALL complete it with the subagent's final message when the subagent stops or
when a task notification for it arrives. After such a notification, the parent task SHALL return to
`running` and follow the notification's turn, so its result reflects the final report.

#### Scenario: Background subagent completes
- **WHEN** an Agent call is launched asynchronously with agent id "a1" and later a subagent stop event for "a1" carries the message "four"
- **THEN** the delegation task has status `done` and result "four"

#### Scenario: Notification reopens the parent
- **WHEN** a task notification closes a delegation and its follow-up turn stops with message "Summary ready"
- **THEN** the parent prompt task has status `done` and result "Summary ready"

### Requirement: Session lifecycle is tracked
The system SHALL register a session on start with its working directory and transcript path, and on
session end SHALL mark the session ended and every still-running task of that session `interrupted`.
When a session is resumed, tasks left `running` by its previous process SHALL be marked `interrupted`.
When a turn stops, other running prompt tasks of that session without running delegations SHALL be
marked `interrupted`, because Claude Code reports no event when the user interrupts a turn.

#### Scenario: Session ends mid-task
- **WHEN** a session ends while one of its tasks is `running`
- **THEN** that task has status `interrupted` and the session is marked ended

#### Scenario: Prompt interrupted with Esc
- **WHEN** a prompt task is running, the user interrupts it, submits a new prompt, and that new turn stops with message "Done"
- **THEN** the new task is `done` with result "Done" and the interrupted task is `interrupted` with its result unchanged

#### Scenario: Resume after a crash
- **WHEN** a session whose task is still `running` is resumed
- **THEN** that task is `interrupted` before the session receives its context summary

### Requirement: Hooks never break Claude Code
The hook entry point SHALL exit with status 0 and produce no output when any internal error occurs,
recording the error in a local log file instead. Tool hooks SHALL only run for Agent tool calls.

#### Scenario: Corrupt input
- **WHEN** the hook entry point receives input that is not valid JSON
- **THEN** it exits with status 0, prints nothing, and appends an entry to the error log
