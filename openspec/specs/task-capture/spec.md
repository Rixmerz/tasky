# task-capture Specification

## Purpose
Record every real prompt, delegated subagent and final result of a Claude Code session automatically through lifecycle hooks, at zero model-token cost, and keep task states truthful when turns are interrupted, sessions end or hooks fail.

## Requirements

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

### Requirement: Results never store the dashboard token
Whenever a result is stored, from hooks, workers or import, every `#token=<value>` occurrence SHALL be
replaced with `#token=<redacted>`.

#### Scenario: Agent prints the access link
- **WHEN** a turn stops with a final message containing `http://127.0.0.1:7733/#token=abcDEF123_-xyz`
- **THEN** the stored result contains `#token=<redacted>` and not the token value

### Requirement: Full conversation copy
The system SHALL copy every user and assistant message of each session, including tool calls and
tool results, into the ledger at the end of each turn, before each compaction and at session end,
reading only transcript lines not copied yet, never storing a message twice, and replacing strings
that match known secret patterns before storing them. Copied messages SHALL remain after the
transcript file is deleted and SHALL be searchable without spending tokens.

#### Scenario: Survives transcript cleanup
- **WHEN** a session's transcript is deleted by Claude Code's cleanup after it was copied
- **THEN** its messages are still returned by a conversation search

#### Scenario: A pasted token is not stored
- **WHEN** a tool result contains a GitHub token
- **THEN** the stored message holds `[redacted]` in its place

#### Scenario: A session that ended stays ended
- **WHEN** the SessionEnd hook copies the last lines of a transcript
- **THEN** the session is still shown as ended


### Requirement: One task per turn
A message the user types while Claude is working reaches the running turn (Claude Code gives it
that turn's prompt id). It SHALL be stored as a follow-up of that turn's task, not as a task of its
own; an edited resend of a follow-up SHALL replace its earlier copy. The turn's reply SHALL go to
the turn's task. A prompt a subagent receives and a subagent's message to its parent SHALL NOT be
recorded as tasks.

#### Scenario: Message typed mid-turn
- **WHEN** the user sends "also update the changelog" while the turn for "build the favicon" runs, and the turn then ends
- **THEN** there is one task, "build the favicon", done, with the reply and one follow-up

#### Scenario: Subagent prompt
- **WHEN** a UserPromptSubmit event carries an `agent_id` or a transcript path under `subagents/`
- **THEN** no task is created and the session's transcript path is unchanged

### Requirement: Prompts cancelled before a reply are dropped
When a new prompt arrives, the previous prompt of the session SHALL be deleted if the transcript
shows no reply and no tool call for it (thinking alone is not a reply), whatever the new text is.
It SHALL be kept, and later marked interrupted, when Claude had started working. Only a transcript
copied up to its end counts as proof; without it, the previous prompt SHALL be dropped only when
the new text is the same request (equal, extended or at least 80% similar, within 30 minutes). A
prompt cancelled right before the session ends SHALL be dropped the same way at SessionEnd.

#### Scenario: Cancel, then send something longer
- **WHEN** the user sends a short prompt, presses Esc before any reply, and sends a much longer, different prompt
- **THEN** only the second prompt is a task

#### Scenario: Cancel after work started
- **WHEN** Claude had edited a file for the first prompt before Esc
- **THEN** both prompts are tasks and the first is marked interrupted when the next turn ends

### Requirement: Tokens, files and recaps per turn
Copying a transcript SHALL give every assistant message the prompt id of the user prompt before
it, SHALL record token usage once per API message (input, output, cache read, cache write), SHALL
read the session's subagent transcripts too, crediting their tokens and files to the parent turn,
and SHALL store Claude Code's recaps (away summaries) and messages typed mid-turn as messages.

#### Scenario: Usage repeated on split lines
- **WHEN** one API message is written as two transcript lines carrying the same usage
- **THEN** its tokens are counted once

### Requirement: Hooks report their version
Every UserPromptSubmit and SessionStart SHALL record the tasky version of the hook on the session,
so the dashboard can tell which open sessions still run an older release.
