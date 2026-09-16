## MODIFIED Requirements

### Requirement: Local dashboard
The system SHALL serve a single-page dashboard on the loopback interface that shows tasks as a
horizontal board with the columns Inbox, Up next, Running, Needs attention and Done, filterable by
project, with each task's result, its delegations, its session and a copyable resume command, and SHALL
refresh within a few seconds of any change made by hooks, the CLI or another browser tab. The Running
column SHALL separate the task started from Up next from tasks running in parallel. A top bar SHALL
create tasks and choose the permission mode used by card actions. The page SHALL load no external
resources.

#### Scenario: New task appears
- **WHEN** a hook records a new running task while the dashboard is open
- **THEN** the task appears in the Running column without a manual reload

#### Scenario: Offline page
- **WHEN** the dashboard is loaded with no network access
- **THEN** it renders fully, because every asset is served locally

#### Scenario: Created task lands in Inbox
- **WHEN** the user types "update the changelog" in the top bar and submits it
- **THEN** a queued task with that body appears in the Inbox column

#### Scenario: Queue runner apart from parallel work
- **WHEN** one task started from Up next and one task started with Run now are running in a project
- **THEN** the Running column shows the first under the queue runner section and the second under the parallel section

#### Scenario: Wide screen uses its width
- **WHEN** the dashboard is 1920 or 2560 pixels wide
- **THEN** the columns stretch across the full width with only a small side gutter, and each column scrolls on its own inside the viewport height

#### Scenario: Narrow screen
- **WHEN** the dashboard is 400 pixels wide
- **THEN** one column is visible at a time, a control switches columns, and the page does not scroll horizontally

### Requirement: JSON API
The system SHALL expose endpoints to read the full state and a change version, create tasks, update a
task's status, title, body, lane or position, delete a task, run a task now or as a clone of a session,
add a task to the end of or a given place in its project's run queue, pause or resume a project's run
queue, toggle a session's auto-pull, and trigger a history import. Invalid input SHALL return a 4xx
status with a JSON error message.

#### Scenario: Create task
- **WHEN** a client posts a body "document the API" and a directory to the tasks endpoint
- **THEN** the response has status 201 and contains the new task with status `queued` and no lane

#### Scenario: Invalid status
- **WHEN** a client patches a task with status "maybe"
- **THEN** the response has status 400 and the task is unchanged

#### Scenario: Invalid run mode
- **WHEN** a client runs a task with mode "later"
- **THEN** the response has status 400 and the task stays `queued`

## ADDED Requirements

### Requirement: Three actions per waiting card
Each queued card in Inbox or Up next SHALL offer Run now, After last and Parallel with context, using
the permission mode chosen in the top bar. Parallel with context SHALL state that it replays the
session's context, and SHALL be unavailable, with the reason shown, when the project has no session to
clone.

#### Scenario: Run now
- **WHEN** the user presses Run now on an Inbox card
- **THEN** the task starts as a fresh worker and moves to the parallel section of Running

#### Scenario: After last
- **WHEN** the user presses After last on an Inbox card while Up next holds two tasks for its project
- **THEN** the task becomes the third task in Up next for that project

#### Scenario: No session to clone
- **WHEN** a project has never had a Claude Code session recorded
- **THEN** its cards show Parallel with context as unavailable with the text "No session in <project> to clone"

### Requirement: Repeating card colours
Cards stacked in a column SHALL cycle through five tints in a repeating pattern by their place in that
column, so adjacent cards differ. Tints SHALL not reuse the colours that signal running, attention or
failure, SHALL keep text contrast of at least 4.5:1 in light and dark themes, and SHALL never be the
only signal of a task's status.

#### Scenario: Seven cards in a column
- **WHEN** a column shows seven cards
- **THEN** cards one to five use five different tints and cards six and seven repeat the first two

#### Scenario: Pattern kept after reorder
- **WHEN** the user drops the last Up next card at the top
- **THEN** the tints follow the new order and no two adjacent cards share a tint

### Requirement: Drag to reorder the run queue
The dashboard SHALL let the user drag a card within Up next to change its place, and drag a card from
Inbox into Up next at a chosen place, with an equivalent keyboard interaction. Refreshes SHALL not
disturb a drag in progress.

#### Scenario: Drag reorder
- **WHEN** the user drags the third Up next card above the first
- **THEN** after the drop and a reload, that task is first in Up next

#### Scenario: Keyboard reorder
- **WHEN** the user focuses an Up next card's handle, presses Space, presses the up arrow and presses Enter
- **THEN** the task moves one place up

#### Scenario: Refresh during drag
- **WHEN** another client changes a task while the user is dragging a card
- **THEN** the drag continues and the change appears after the drop
