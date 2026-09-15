## ADDED Requirements

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
