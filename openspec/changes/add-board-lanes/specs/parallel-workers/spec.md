## ADDED Requirements

### Requirement: Run with a cloned session's context
The system SHALL start a queued task as a worker that clones an existing Claude Code session: the
task's own session when it has one, else the most recently active session recorded for the task's
directory. The clone SHALL receive a new session identifier, SHALL run in the source session's
directory, and SHALL leave the source session's conversation unchanged. The system SHALL refuse the
run, leaving the task `queued`, when no session can be cloned.

#### Scenario: Clone the project's latest session
- **WHEN** the user runs a task with mode `fork` in a directory whose latest session is S
- **THEN** a worker starts that resumes S as a new session with a new identifier, and the task records S as its source

#### Scenario: Nothing to clone
- **WHEN** the user runs a task with mode `fork` in a directory with no recorded session
- **THEN** the run is refused with an error and the task stays `queued`

#### Scenario: Invalid source session id
- **WHEN** the session chosen as the source has an identifier that is not a UUID
- **THEN** the run is refused and no process is started
