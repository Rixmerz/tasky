## ADDED Requirements

### Requirement: A continuation nudge reopens its stuck turn

A new prompt turn whose whole text only pushes an earlier turn along and names no work of its own (for
example "--continue", "sigue", "continúa", "go on", or "vuelve a <verb>") SHALL NOT become a task of
its own when the session's most recent prompt task is `interrupted` or `failed`: it SHALL instead be
added as a follow-up of that task and reopen it to `running`, un-hiding it first if the user had
archived or deleted it from the dashboard in the meantime. A nudge with no earlier prompt task in the
session, or whose earlier prompt task ended in `done`, SHALL become a task of its own as usual. A
message that merely begins the same way as a nudge but goes on to name real work SHALL NOT be treated
as a nudge.

#### Scenario: Nudge reopens an interrupted turn
- **WHEN** a turn "migrate the invoices table" is `interrupted` and the next prompt in that session is
  "--continue"
- **THEN** no new task is created, the interrupted task is `running` again with "--continue" as a
  follow-up

#### Scenario: Nudge un-hides an archived turn
- **WHEN** an interrupted turn was hidden from the dashboard and the next prompt in that session is
  "--continue"
- **THEN** the turn is un-hidden, reopened to `running`, and back on the board with the nudge as a
  follow-up

#### Scenario: Nudge reopens a failed turn
- **WHEN** a turn is `failed` and the next prompt in that session is "vuelve a abrila"
- **THEN** the failed task is reopened to `running` with the nudge as a follow-up, instead of a new
  task being created

#### Scenario: No turn to reopen
- **WHEN** "--continue" is the first prompt of a session
- **THEN** it becomes a task of its own, as any other prompt would

#### Scenario: Cleanly finished turn is not reopened by a nudge
- **WHEN** a turn ended `done` and the next prompt in that session is "--continue"
- **THEN** "--continue" becomes a new task rather than reopening the finished one

#### Scenario: A look-alike longer message is not folded
- **WHEN** the next prompt is "vuelve a rediseñar la alpaca desde cero", which only starts the way a
  nudge does
- **THEN** it becomes a task of its own

#### Scenario: A real repeated one-word command is not folded
- **WHEN** the next prompt is "next", used to step to a different item rather than to nudge a stuck
  turn
- **THEN** it becomes a task of its own
