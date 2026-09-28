## MODIFIED Requirements

### Requirement: Board shows what needs the user
Needs attention SHALL hold failed tasks, interrupted tasks, and the newest task of an open session
whose reply ends with a question, each with its reason. The "needs answer" mark SHALL only appear on
the newest task of its session. Cards SHALL show the start of the reply, duration, files edited,
output tokens and follow-ups; Done SHALL be grouped by day and session; the latest recap of each
open session SHALL be shown above the columns and recaps SHALL appear among the tasks where they
happened; open sessions running hooks older than the dashboard SHALL be flagged. A running task that
has shown no sign of life — no newer session activity, no growth in its session's transcript file,
no growth in its worker's log file — for longer than a configurable threshold SHALL move to Needs
attention as interrupted, with a reason stating how long it has been idle; a task whose session or
worker still shows activity SHALL stay running regardless of how long it has been running, and the
sweep SHALL never move a task that has already finished.

#### Scenario: Task with no sign of life moves to Needs attention
- **WHEN** a running task's session has had no newer prompt, its session's transcript file has not
  grown, and its worker's log has not grown for longer than the configured threshold
- **THEN** the task moves to Needs attention as interrupted, with a reason naming how long it has
  been idle and that its session or worker is gone

#### Scenario: Long-running worker is not swept
- **WHEN** a running task's session or worker log keeps growing past the configured threshold
- **THEN** the task stays in Running, however long it has been running

#### Scenario: Threshold not yet reached
- **WHEN** a running task has shown no sign of life for less than the configured threshold
- **THEN** the task stays in Running

#### Scenario: Finished task is left alone
- **WHEN** a task has already finished (done, failed, interrupted or cancelled)
- **THEN** the sweep leaves it exactly as it was, whatever its age
