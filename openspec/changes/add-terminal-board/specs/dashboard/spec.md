## ADDED Requirements

### Requirement: The board in the terminal

The MCP server SHALL offer `board`: for this repository, or every repository with `scope` set to
`all`, a first line giving the count of prompt tasks running, queued, and needing attention (failed or
interrupted), followed by the newest titles of each non-empty column. Delegations SHALL NOT be counted,
as the dashboard nests them under their prompt. An opt-in mod, `tasky-mod`, SHALL show those counts on
Claude Code's status line while any is above zero and show nothing otherwise, and SHALL show the board
in a pane on request. It SHALL read the board at the end of every turn and, unless set to 0, every
configured number of seconds between turns. A board that cannot be read SHALL leave the session
untouched.

#### Scenario: Counts on the status line
- **WHEN** one prompt is running, two are queued and one failed in this repository
- **THEN** the status line shows `tasky ▶1 ⏸2 ⚠1`

#### Scenario: A worker finishes between turns
- **WHEN** the running task finishes while the session is idle
- **THEN** the status line updates within the refresh interval without a prompt

#### Scenario: Nothing to show
- **WHEN** nothing is running, queued or needs attention
- **THEN** the status line shows nothing from tasky
