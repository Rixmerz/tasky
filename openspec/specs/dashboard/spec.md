# dashboard Specification

## Purpose
Give a person one local page that answers at a glance what is running, what is waiting, what needs attention and what finished, and let them act on tasks without spending model tokens or exposing the ledger to other programs, websites or local accounts.

## Requirements

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

### Requirement: Localhost-only protections
The server SHALL bind only to the loopback interface, SHALL reject requests whose Host header is not
the loopback address or `localhost` at the served port, and SHALL reject state-changing requests that
lack the access token header or a JSON content type, so that other websites cannot trigger actions
such as starting workers.

#### Scenario: DNS rebinding attempt
- **WHEN** a request arrives with Host "evil.example:7733"
- **THEN** the server responds 403

#### Scenario: Cross-site form post
- **WHEN** a POST to run a task arrives as a form submission without the access token
- **THEN** the server rejects it and no worker is started

### Requirement: API access token
Every API request SHALL carry an access token that is stored in a file readable only by the user,
compared in constant time, and handed to the dashboard through the URL fragment of the link printed by
the dashboard commands. Requests without a valid token SHALL receive 401, and the page SHALL show how
to obtain a valid link instead of the board.

#### Scenario: Another local account calls the API
- **WHEN** a client that cannot read the token file requests the state endpoint
- **THEN** the server responds 401 and returns no task data

#### Scenario: Opening the printed link
- **WHEN** the user opens the link printed by the dashboard command
- **THEN** the board renders, the token is remembered by the browser, and the fragment is removed from the address bar

#### Scenario: Stale bookmark
- **WHEN** the dashboard is opened without a valid token
- **THEN** the page explains how to open it from Claude Code or the terminal and stops polling

### Requirement: Private data files
The data directory SHALL be created readable only by the user, and the database, token, prompt and
log files SHALL be readable only by the user.

#### Scenario: Default umask
- **WHEN** the ledger is created under a permissive umask
- **THEN** the data directory has mode 0700 and the database has mode 0600

### Requirement: Results render as readable digests
The dashboard SHALL render a task's result as a digest instead of raw text: the first paragraph as a
headline; each paragraph that opens with a bold lead-in (optionally prefixed by an arrow or a step
number) as its own card titled by that lead-in; lists, tables, fenced code and headings as their own
readable blocks inside the current card; and a final paragraph that ends with a question as a
distinct "waiting for you" card. Results without that shape SHALL still render as paragraphs, lists,
tables and code. Inline bold, inline code and http(s) links SHALL be rendered; all text SHALL be
inserted as text, never as markup. The original text SHALL remain viewable.

#### Scenario: Reply in the focus shape
- **WHEN** a result has an opening sentence followed by three paragraphs that start with `**→ Lead.**`
- **THEN** the digest shows the opening sentence as the headline and three cards titled by their lead-ins

#### Scenario: Numbered steps and a closing question
- **WHEN** a result contains paragraphs starting with `**1 →**` and `**2 →**` and ends with a paragraph ending in "?"
- **THEN** the cards show step numbers 1 and 2, and the last card is marked as waiting for the user

#### Scenario: Plain prose
- **WHEN** a result is three plain paragraphs with no bold lead-ins
- **THEN** the first paragraph is the headline and the others render as paragraphs

#### Scenario: Markup in a result
- **WHEN** a result contains `<img src=x onerror=alert(1)>`
- **THEN** it is displayed as literal text and no element is created from it

#### Scenario: Truncated code fence
- **WHEN** a result ends inside an unclosed fenced code block
- **THEN** the remaining text renders as code and the rest of the digest still renders

#### Scenario: Unsafe link
- **WHEN** a result contains a markdown link whose target is `javascript:alert(1)`
- **THEN** the link text is shown without a clickable link

### Requirement: Collapsed cards summarize results
A collapsed task card with a result SHALL show the result's headline as one ellipsized line of plain
text, and SHALL show a "waiting for you" marker when the result ends with a question.

#### Scenario: Done task at a glance
- **WHEN** a done task's result starts with "Migration written and tests pass."
- **THEN** its collapsed card shows that sentence under the title without expanding

#### Scenario: Agent asked a question
- **WHEN** a done task's result ends with "Should I also update the seed data?"
- **THEN** its collapsed card shows the waiting-for-you marker

### Requirement: Sessions show their Claude Code name
The dashboard SHALL label a session with the name Claude Code shows for it: the latest name set with
`/rename`, else the latest generated name, else the session id. A rename SHALL reach open dashboards
within a few seconds without a new prompt being sent.

#### Scenario: Session renamed while the dashboard is open
- **WHEN** the user runs `/rename tasky main` in a session the dashboard lists
- **THEN** the dashboard shows "tasky main" for that session instead of its id

#### Scenario: Imported session with a generated name
- **WHEN** a transcript that was never renamed but carries a generated name is imported
- **THEN** the imported session is labeled with the generated name

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

### Requirement: Search past tasks
The dashboard SHALL offer a search box that finds tasks whose title, prompt or reply contains every
word typed, across the whole ledger rather than only the tasks the board holds, newest first, within
the active project filter. Each hit SHALL show where the words matched and open in the side panel.
Searching SHALL spend no model tokens, SHALL treat typed `%`, `_` and `\` as literal characters, and
SHALL require the dashboard token like every other API route.

#### Scenario: Old answer found by a word in its reply
- **WHEN** a task finished months ago, is older than the board's Done slice, and its reply mentions "NODE_EXTRA_CA_CERTS"
- **THEN** typing "node_extra_ca_certs" in the search box lists that task, and clicking it shows the full reply

#### Scenario: Every word must match
- **WHEN** the user searches "deploy staging" and one task mentions both words while another mentions only "deploy"
- **THEN** only the first task is listed

#### Scenario: Leaving search
- **WHEN** the user presses Esc in a non-empty search box
- **THEN** the box clears and the board returns

### Requirement: Project history
The dashboard SHALL keep, per repository, problems with the ordered chain of attempts made to fix
them (outcome, why it failed, when it was believed correct and when it was shown wrong, evidence and
source tasks) and dated milestones, and SHALL show them as a timeline, a problem list, a list of
failed attempts and a map, for one repository or all of them. Folders that share a git `origin`
SHALL share one history. The history SHALL be built only when the user asks for a sync with a model
they choose, which SHALL read only tasks finished since the previous sync, SHALL run with no tools
and with Tasky's hooks disabled, SHALL discard records citing tasks the model was not shown, SHALL
keep an evidence quote only if it appears verbatim in a cited task, and SHALL show the cost of the
last sync. Reading the history SHALL spend no tokens. A session that starts in a repository with
failed attempts SHALL receive the most recent ones as context, up to a configurable count that can
be zero. The history SHALL be searchable and writable by the agent through MCP tools.

#### Scenario: A fix that did not hold stays on record
- **WHEN** one task records a fix, a later task says it did not work and names the real cause, and the user syncs
- **THEN** the problem shows the first attempt as failed with why and its evidence, followed by the fix that worked, and the failed one is listed under Dead ends

#### Scenario: Sync reads only new work
- **WHEN** a repository was synced and two tasks finished afterwards
- **THEN** the next sync sends the model those two tasks and none of the earlier ones

#### Scenario: The agent checks before repeating a fix
- **WHEN** an agent calls search_history with words from a bug that already has a failed attempt
- **THEN** it receives the problem with every attempt in order, including the failed one and why it failed

#### Scenario: One bank across repositories
- **WHEN** the user picks "All repos" in the History panel
- **THEN** problems, attempts and milestones of every repository are listed together, each labelled with its repository

### Requirement: Smart search
After the plain search results, the dashboard SHALL offer a Smart search button that sends the
search text to Haiku with a compact index of the ledger (prompt start, follow-ups, reply start,
date, project) and lists the tasks it names with a one-line reason each, and the call's cost. The
recent-task index SHALL be the same for every question so the prompt cache serves it. Ids not in
the index SHALL be dropped. The model call SHALL run with no tools, no MCP servers, no saved
session and Tasky's hooks off, and only when the user presses the button.

#### Scenario: Words that do not match
- **WHEN** the user searches "icon for the browser tab" and the task says "create a favicon"
- **THEN** the plain search finds nothing and Smart search lists the favicon task with its reason

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

### Requirement: Permission mode chosen when adding
The composer SHALL let the user pick the permission mode a task will run with, stored on the task;
running or queueing it without naming a mode SHALL use the stored one.

#### Scenario: Stored mode is used at launch
- **WHEN** the user adds a task with permission mode "plan" and later runs it without naming a mode
- **THEN** the task runs with permission mode "plan"

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
sweep SHALL never move a task that has already finished. Because a session runs one turn at a time,
a running prompt with a newer prompt after it in the same session SHALL move to Needs attention at
once, however active the session still is, without ending the session; a running delegation whose
parent task has ended and that has itself shown no sign of life for longer than the threshold SHALL
move too. When Needs attention
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

#### Scenario: A turn the session has moved past
- **WHEN** a session the user is still using has a running prompt from days ago and newer prompts
  after it
- **THEN** that old prompt moves to Needs attention as interrupted, and the session stays active

#### Scenario: A background agent still at work is left alone
- **WHEN** a delegation's parent turn has ended but the delegation started, or last wrote to its
  worker log, less than the threshold ago
- **THEN** the delegation stays in Running

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

### Requirement: Compact dashboard as cards
The History panel SHALL offer, for one repository, to group its finished tasks into cards, only
when that repository's history sync is up to date and not running and some finished task is in
no card yet. The system SHALL run it in the background with Haiku, in batches of up to 60 tasks
past a per-repository cursor, showing the model the cards kept so far, the tasks with the files
they edited, the areas, the problems and milestones of those tasks and the open problems, and the
git log of the same days. A card SHALL keep only tasks, problems, milestones, existing cards and
commits the model was shown, each task in one card; a criterion SHALL be "stated" only when its
quote appears in a prompt of the card's tasks, otherwise "inferred". The files of a card SHALL be
the files its tasks edited, from the recorded edits. The Cards tab SHALL show the cards as a board
by status, and a card SHALL be removable. The cards SHALL be written in the language of the
developer's prompts, detected without a model from the developer's own words (pasted blocks,
fenced code and identifiers left out), from their latest prompts in any repository when the
batch's are too short, or from `TASKY_LANGUAGE` when set; it SHALL be named in the system
prompt, the prompt and the schema. A card that comes back in another language SHALL be sent back
once, with only its wording, for translation unless `TASKY_CARDS_TRANSLATE=0`, and the run SHALL
report what told the language, how many cards were translated, at what cost, and how many were
not. The MCP server SHALL offer
`search_cards` (words, status, scope) and `get_card` (one card in full).

#### Scenario: History behind
- **WHEN** the repository has 3 finished tasks the history sync has not read
- **THEN** the button is disabled saying the history must be synced first, and the API answers 409

#### Scenario: Invented quote
- **WHEN** the model gives a criterion with a quote no prompt of the card contains
- **THEN** the criterion is kept and marked inferred

#### Scenario: Card in the wrong language
- **WHEN** the developer's prompts are in Spanish and Haiku returns a card in English
- **THEN** only that card's title, objective, description and criteria go back for translation,
  its tasks stay as they were, and its stated quote keeps the developer's words

#### Scenario: Short prompts
- **WHEN** a batch's prompts are "dale" and "fix ci" and the developer's earlier prompts are Spanish
- **THEN** the cards are asked for in Spanish and the run says the earlier prompts told it

#### Scenario: Pasted log
- **WHEN** a Spanish prompt carries a long English CI log in a pasted block
- **THEN** the log does not count and the cards are asked for in Spanish
