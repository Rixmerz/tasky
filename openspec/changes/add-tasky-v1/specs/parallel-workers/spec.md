## ADDED Requirements

### Requirement: Run a queued task as a headless session
The system SHALL start a `queued` task as a detached headless Claude Code process in the task's working
directory with a newly generated session identifier and a chosen permission mode, SHALL mark the task
`running` bound to that session, and SHALL write the process output to a per-task log file. Hooks
fired by that process SHALL attach to the existing task instead of creating a duplicate.

#### Scenario: Start a worker
- **WHEN** the user runs a queued task with permission mode `acceptEdits`
- **THEN** a process is started in the task's directory with that permission mode and a new session id
- **AND** the task is `running` with that session id

#### Scenario: Worker prompt does not duplicate
- **WHEN** the worker session submits the task's prompt
- **THEN** no additional task is created and the existing task keeps its identifier

### Requirement: Worker safety checks
The system SHALL refuse to run a task that is not `queued`, SHALL refuse permission modes outside the
supported set, SHALL refuse `bypassPermissions` unless explicitly allowed by configuration, SHALL start
a task at most once even when several requests race for it, SHALL pass the task text on standard input
rather than as a command-line argument, and SHALL mark the task `failed` with the error text when the
process cannot be started.

#### Scenario: Task already running
- **WHEN** the user runs a task whose status is `running`
- **THEN** the request is rejected and no process is started

#### Scenario: Unknown permission mode
- **WHEN** the user runs a queued task with permission mode "yolo"
- **THEN** the request is rejected and the task stays `queued`

#### Scenario: Binary missing
- **WHEN** the Claude Code binary cannot be executed
- **THEN** the task is `failed` and its result contains the error

#### Scenario: Bypass not allowed
- **WHEN** the user runs a queued task with `bypassPermissions` and bypass is not enabled in configuration
- **THEN** the request is rejected with a message naming the setting, and the task stays `queued`

#### Scenario: Concurrent run requests
- **WHEN** eight run requests for the same queued task arrive at the same time
- **THEN** exactly one process is started and the other requests are rejected as already taken

#### Scenario: Task text that looks like an option
- **WHEN** a queued task's text starts with `--`
- **THEN** the text is delivered to the headless session as its prompt and never appears among the process arguments

### Requirement: Worker supervision
The system SHALL wait for each worker process and, when it exits while its task is still `running`,
SHALL mark the task `failed` with the exit code and log location for a non-zero exit, or `interrupted`
for a zero exit that reported no result. The worker SHALL NOT inherit the session variables of the
Claude Code process that launched it.

#### Scenario: Worker crashes before its hooks run
- **WHEN** a worker process exits with code 1 before any hook records a result
- **THEN** the task is `failed` and its result names the exit code and the log file

#### Scenario: Worker finished normally
- **WHEN** a worker's hooks mark its task `done` and the process exits with code 0
- **THEN** the task stays `done` with its recorded result
