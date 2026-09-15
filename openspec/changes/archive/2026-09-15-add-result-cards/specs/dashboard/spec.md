## ADDED Requirements

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
