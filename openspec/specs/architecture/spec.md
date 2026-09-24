# architecture Specification

## Purpose
Give each repository a controlled vocabulary of areas (business or technical parts, not layers) that tasks, problems, milestones and specs are placed in, so a person and the agent can see where work happened and which specs, decisions and dead ends belong to each part, reading the repository for free and spending tokens only when asked.

## Requirements

### Requirement: Scan reads specs and architecture output for free
The system SHALL read a repository's main checkout without writing to it and without a model call,
listing files through git (tracked and untracked, not ignored) or a walk that skips dependency and
build folders. It SHALL keep OpenSpec capabilities and changes (active and archived), spec-kit
features, Kiro specs and architecture decision records with their path, title, status, date, summary
and requirement names, and SHALL keep as candidate areas the boundaries of Archify
`*.architecture.json` files, the communities of Graphify `graphify-out/graph.json` and the folders
that hold code. A repository with no areas, visible or deleted, SHALL adopt Archify boundaries, or
else Graphify communities, as its areas.

#### Scenario: Spanish ADR
- **WHEN** `doc/adr/0002_typescript.md` has `* Status: **Propuesto**`, `* Date: 2019-09-03` and a `## Decisión` section
- **THEN** the spec's status is `Propuesto`, its date `2019-09-03` and its summary the decision's first paragraph

#### Scenario: Archify boundaries become areas once
- **WHEN** a repository without areas has an Archify boundary "Checkout Flow" wrapping components whose sources are under `src/checkout`
- **THEN** the scan creates the area `checkout-flow` with alias "Checkout Flow" and path `src/checkout`, and a later scan after the user deleted it does not create it again

### Requirement: Mapping areas is one checked model call on demand
Mapping SHALL run only when the user asks, as one `claude -p` call with the chosen model (Sonnet or
Opus), no tools and Tasky's hooks off, given the folder tree without file contents, the candidates,
the specs, the existing areas, the History topics and the folders where edits happened. The system
SHALL keep only paths that exist in the scan, each path in one area, specs it was shown, and names
normalised to lowercase hyphenated words. An area the user created or edited SHALL keep its name,
kind and description and only gain paths, specs and aliases; other areas the model does not return
SHALL be retired. The cost and any error SHALL be recorded on the repository, and a second mapping
SHALL be refused while one is running.

#### Scenario: Invented path dropped
- **WHEN** the model returns an area with paths `src/checkout` and `does/not/exist`
- **THEN** the area keeps only `src/checkout`

#### Scenario: User area kept
- **WHEN** the user edited area `ops` and the model returns it renamed with another description
- **THEN** it is still `ops` with the user's description and gains the returned paths

### Requirement: Work, problems and specs are placed in areas when read
A file belongs to the area with the longest path covering it, relative to the checkout or worktree it
was edited in (Claude Code worktrees inside the checkout map to the same paths). A task SHALL be in
the areas of the files it edited, most files first. Each area SHALL show the turns that edited it,
the distinct files and the date of the last edit, counted from every copied edit under the
repository, with or without a task. A problem or milestone SHALL be in the area its topic names (by
name or alias), else the areas of the specs it names, else the area most of its tasks are in. Each
spec SHALL list the problems and milestones that name it. A spec SHALL be in the areas that list it,
else the area named like its capability or feature. Folders edited in no area, History topics in no
area and specs in no area SHALL be listed.

#### Scenario: Topic alias
- **WHEN** area `checkout` has alias `pago` and a problem's topic is `Pago`
- **THEN** the problem is listed under `checkout`

#### Scenario: Placed by its spec
- **WHEN** a problem's topic `misc` names no area and the problem names `openspec/specs/checkout/spec.md`
- **THEN** the problem is listed under the area of that spec, and the spec lists the problem

#### Scenario: Edit without a task
- **WHEN** a session with no task on the board wrote `src/auth/new.ts`
- **THEN** area `auth` counts that turn and that file

### Requirement: Areas are editable and reachable by the agent
The dashboard SHALL show the Architecture view with areas, their activity, specs, problems,
milestones and recent tasks, and SHALL let the user scan, map, add, rename, edit and delete areas;
deleting SHALL hide the area. Board cards and the task panel SHALL show a task's areas. The MCP tool
`get_architecture` SHALL list the current repository's areas, or one area (by name or alias) with
its specs' requirements, problems with their failed fixes, milestones and recent tasks.

#### Scenario: Unknown area
- **WHEN** the agent calls `get_architecture` with an area that does not exist
- **THEN** the result is an error that lists the repository's areas

### Requirement: Drawing with the Archify skill
When the Archify skill is installed for Claude Code (user skills, the repository's skills or a
plugin), the dashboard SHALL offer to draw the repository's architecture. The system SHALL start one
headless task in the repository's main checkout with the chosen model, permission mode `acceptEdits`
and, besides edits, only Archify's CLI and read-only git commands allowed, asking for a diagram
backed by repository evidence with the repository's areas as boundary names, written under
`docs/architecture/`. A second drawing SHALL be refused while one runs. When the task finishes after
the last scan, the next read SHALL scan again. Opening a diagram SHALL only open a rendered page the
scan found, inside the checkout.

#### Scenario: Skill missing
- **WHEN** the user asks to draw and no Archify skill with its CLI is installed
- **THEN** the request is refused with "the Archify skill is not installed for Claude Code"

#### Scenario: Viewing a diagram in the dashboard
- **WHEN** the user views a diagram page the scan found
- **THEN** the page is served from a random link that expires after 15 minutes, with a
  Content-Security-Policy `sandbox` without `allow-same-origin`, so it cannot read the token or call
  the API

#### Scenario: Path outside the diagrams
- **WHEN** the dashboard asks to open `../../etc/passwd`
- **THEN** nothing is opened and the answer is 404

### Requirement: The History sync speaks the areas' vocabulary
When the repository has areas, a History sync SHALL show the model the areas (name, kind, aliases,
description) and up to 80 specs, current ones first. A record's topic that names an area by name or
alias SHALL be stored as the area's name. A record SHALL keep at most 3 specs, only paths of the
repository's specs. An alias the model proposes SHALL be added only when the developer wrote it in a
task of the batch, it names no area yet, the area has fewer than 12 aliases and the area was not
last edited by the developer. The sync SHALL run at effort `high` and mapping at effort `medium`.

#### Scenario: Alias never written
- **WHEN** the model proposes alias `facturación` for `checkout` and no task of the batch contains it
- **THEN** the alias is not added

### Requirement: Searching the history climbs a fixed ladder
`search_history` SHALL, at no model cost: return the records with every word plus the problems and
milestones of the areas the question names (by name, alias, a plural, or a word of 4 or more
letters that starts exactly one area's one-word name or alias), saying which areas were named; if
that finds nothing, the records with any word, saying so; and if nothing matches, the areas that
have history with their counts and aliases.

#### Scenario: A word only an alias knows
- **WHEN** area `checkout` has alias `pago`, no record contains "pago", and the agent searches
  "problemas de pago"
- **THEN** the result starts with `Areas named: checkout ("pago")` and lists checkout's problems
