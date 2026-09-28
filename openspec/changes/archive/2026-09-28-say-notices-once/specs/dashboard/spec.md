## MODIFIED Requirements

### Requirement: Board shows what needs the user
Needs attention SHALL hold failed tasks, interrupted tasks, and the newest task of an open session
whose reply ends with a question, each with its reason. The "needs answer" mark SHALL only appear on
the newest task of its session. Cards SHALL show the start of the reply, duration, files edited,
output tokens and follow-ups; Done SHALL be grouped by day and session; the latest recap of each
open session SHALL be shown above the columns, and a recap already shown there SHALL NOT also
appear among the tasks where it happened -- that session's older recaps SHALL still appear there,
unaffected. Live sessions running hooks older than the dashboard SHALL be named, each with a way to
copy its resume command or open its latest task, in one notice above the board; dismissing that
notice SHALL persist across a refresh, keyed by which old versions are currently running, so it
stays dismissed while sessions on an already-known old version start or end and reappears only for
a version it had not already covered. Elsewhere on the board -- a Done group heading, a recap card
-- an outdated session SHALL be marked by a quiet indicator carrying the same information for
assistive technology and a hover tooltip, not by repeating the notice's sentence as visible text. A running task that
has shown no sign of life — no newer session activity, no growth in its session's transcript file,
no growth in its worker's log file — for longer than a configurable threshold SHALL move to Needs
attention as interrupted, with a reason stating how long it has been idle; a task whose session or
worker still shows activity SHALL stay running regardless of how long it has been running, and the
sweep SHALL never move a task that has already finished. When Needs attention
holds cards from more than one project, they SHALL be grouped by project, collapsed and expanded
independently of each other; with a single project the column SHALL stay a flat list. The column
header SHALL offer an action, confirmed in the page rather than by a browser dialog, that hides every
attention card older than a day at once, and that action SHALL only appear when such a card exists.

#### Scenario: One recap, not two
- **WHEN** an open session's newest recap is shown in the "Latest recap" panel above the columns
- **THEN** that same recap does not also appear as a row in that session's Done group
- **AND** an older recap from the same session still appears in Done

#### Scenario: Outdated sessions are named and actionable
- **WHEN** two live sessions are running hooks older than the dashboard's own version
- **THEN** the banner above the board lists both, each with its title and project and a way to
  copy its resume command or open its latest task

#### Scenario: Dismissal survives a refresh on the same old version
- **WHEN** the user dismisses the outdated-hooks banner while two sessions run version 0.16.0,
  then reloads the dashboard and one of those two sessions is restarted while a third session
  starts, also on 0.16.0
- **THEN** the banner stays hidden

#### Scenario: Dismissal survives every session on one of several dismissed versions restarting
- **WHEN** the user dismisses the outdated-hooks banner while it names both version 0.16.0 and
  sessions with no recorded version, and every 0.16.0 session is then restarted, leaving only
  sessions with no recorded version
- **THEN** the banner stays hidden, because both versions it now names were already covered

#### Scenario: A newly outdated version reopens the notice
- **WHEN** the user dismisses the outdated-hooks banner while a session runs version 0.16.0, and a
  session on version 0.15.0 is then detected
- **THEN** the banner is shown again

#### Scenario: Badge is quiet where the banner already says it
- **WHEN** a Done group's session is running hooks older than the dashboard
- **THEN** its heading shows a small dot, not the words "older tasky", and the dot's tooltip and
  accessible name carry the full explanation

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

#### Scenario: Grouped by project
- **WHEN** Needs attention holds cards from two different projects
- **THEN** the cards are shown in two collapsible groups, one per project, each toggled on its own

#### Scenario: One project stays flat
- **WHEN** every card in Needs attention belongs to the same project
- **THEN** the cards are shown as a plain list with no group header

#### Scenario: Archive older than a day
- **WHEN** the user presses the column's archive action and confirms it in the page
- **THEN** every attention card whose age is at least a day is hidden at once, and the action offers a
  way back for what it just hid

#### Scenario: Nothing to archive
- **WHEN** every card in Needs attention is less than a day old
- **THEN** the column's archive action does not appear
