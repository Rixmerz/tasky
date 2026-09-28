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
assistive technology and a hover tooltip, not by repeating the notice's sentence as visible text.

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

#### Scenario: A newly outdated version reopens the notice
- **WHEN** the user dismisses the outdated-hooks banner while a session runs version 0.16.0, and a
  session on version 0.15.0 is then detected
- **THEN** the banner is shown again

#### Scenario: Badge is quiet where the banner already says it
- **WHEN** a Done group's session is running hooks older than the dashboard
- **THEN** its heading shows a small dot, not the words "older tasky", and the dot's tooltip and
  accessible name carry the full explanation
