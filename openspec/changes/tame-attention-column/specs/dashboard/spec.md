## MODIFIED Requirements

### Requirement: Soft delete
Deleting a task from the dashboard SHALL hide it and its delegations from the board and the
dashboard search, keeping the row for the history sync and the MCP tools. A hidden queued task SHALL
be cancelled so it never runs. A hidden task SHALL be restorable. The system SHALL also offer hiding
and restoring many tasks in one request, with the same semantics applied to each id.

#### Scenario: Undo
- **WHEN** the user deletes a task and presses Undo
- **THEN** the task is back on the board as it was

#### Scenario: Bulk hide and undo
- **WHEN** the user archives several attention cards at once and then presses Undo
- **THEN** every one of them is hidden, then every one of them is back on the board as it was

### Requirement: Board shows what needs the user
Needs attention SHALL hold failed tasks, interrupted tasks, and the newest task of an open session
whose reply ends with a question, each with its reason. The "needs answer" mark SHALL only appear on
the newest task of its session. Cards SHALL show the start of the reply, duration, files edited,
output tokens and follow-ups; Done SHALL be grouped by day and session; the latest recap of each
open session SHALL be shown above the columns and recaps SHALL appear among the tasks where they
happened; open sessions running hooks older than the dashboard SHALL be flagged. When Needs attention
holds cards from more than one project, they SHALL be grouped by project, collapsed and expanded
independently of each other; with a single project the column SHALL stay a flat list. The column
header SHALL offer an action, confirmed in the page rather than by a browser dialog, that hides every
attention card older than a day at once, and that action SHALL only appear when such a card exists.

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
