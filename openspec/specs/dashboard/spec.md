# dashboard Specification

## Purpose
Give a person one local page that answers at a glance what is running, what is waiting, what needs attention and what finished, and let them act on tasks without spending model tokens or exposing the ledger to other programs, websites or local accounts.

## Requirements

### Requirement: Local dashboard
The system SHALL serve a single-page dashboard on the loopback interface that shows tasks grouped by
state (running, queued, needs attention, done), filterable by project, with each task's result, its
delegations, its session and a copyable resume command, and SHALL refresh within a few seconds of any
change made by hooks, the CLI or another browser tab. The page SHALL load no external resources.

#### Scenario: New task appears
- **WHEN** a hook records a new running task while the dashboard is open
- **THEN** the task appears in the running column without a manual reload

#### Scenario: Offline page
- **WHEN** the dashboard is loaded with no network access
- **THEN** it renders fully, because every asset is served locally

### Requirement: JSON API
The system SHALL expose endpoints to read the full state and a change version, create queued tasks,
update a task's status, title, body or position, delete a task, run a task as a worker, toggle a
session's auto-pull, and trigger a history import. Invalid input SHALL return a 4xx status with a JSON
error message.

#### Scenario: Create task
- **WHEN** a client posts a body "document the API" and a directory to the tasks endpoint
- **THEN** the response has status 201 and contains the new task with status `queued`

#### Scenario: Invalid status
- **WHEN** a client patches a task with status "maybe"
- **THEN** the response has status 400 and the task is unchanged

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
