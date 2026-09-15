# history-import Specification

## Purpose
Fill the ledger from Claude Code transcripts that existed before Tasky was installed, so past sessions and their results are visible on day one, without ever duplicating work that hooks or workers already recorded.

## Requirements

### Requirement: Import past sessions from transcripts
The system SHALL scan the Claude Code projects directory for session transcripts and create sessions,
prompt tasks and delegation tasks from them, using the same text classification as live capture. Each
prompt's result SHALL be the last assistant text before the next prompt. Delegations SHALL be completed
from task notifications that reference their tool use. The last prompt of a transcript with no
assistant text after it SHALL be `interrupted`; all other imported tasks SHALL be `done`.

#### Scenario: Import a transcript
- **WHEN** a transcript contains two user prompts, each followed by assistant text
- **THEN** two `done` tasks exist with results equal to the last assistant text after each prompt

#### Scenario: Unanswered last prompt
- **WHEN** a transcript ends with a user prompt and no assistant reply
- **THEN** that task is `interrupted`

#### Scenario: Meta entries skipped
- **WHEN** a transcript contains meta entries, tool results, compaction summaries and task notifications
- **THEN** none of them create prompt tasks

### Requirement: Import is idempotent and does not duplicate live capture
Running the import repeatedly SHALL NOT create duplicate tasks, and sessions that already have tasks
recorded by hooks SHALL be skipped. Subagent transcripts SHALL be ignored. Unreadable lines SHALL be
skipped without aborting the import.

#### Scenario: Second run
- **WHEN** the import runs twice over the same transcripts
- **THEN** the task count after the second run equals the count after the first

#### Scenario: Session captured live
- **WHEN** a transcript belongs to a session that already has hook-recorded tasks
- **THEN** the import creates no tasks for that session

#### Scenario: Corrupt line
- **WHEN** a transcript contains a line that is not valid JSON
- **THEN** the import completes and imports the valid entries
