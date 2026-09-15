# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-15

### Added

- Results render as readable cards: headline first, one card per point or step, tables and code in
  their own blocks, and a "waiting for you" card when a reply ends with a question. Collapsed cards
  show the result's first sentence and the waiting marker.
- `tasky:Focus Cards`, an ADHD-friendly output style bundled with the plugin and selectable from
  `/output-style`. Original text under MIT; reply shape inspired by attention-span.

### Fixed

- Sessions are labeled with the name set by `/rename` (or Claude Code's generated name) instead of
  their id, for live and imported sessions.

### Changed

- Dashboard access links are redacted from every stored result, not only from imported ones.

## [0.1.0] - 2026-09-15

### Added

- Hook-driven capture of prompts, delegated subagents and results into a local SQLite ledger, with
  no model tokens spent. Prompts interrupted with Esc are marked at the next turn or on resume.
- Zero-token chat queue: prompts that start with `++` are stored and never reach the model.
- Opt-in auto-pull that hands a session its next queued task at the end of a turn, capped per
  chain.
- Context reminder of unfinished tasks when a session is resumed or compacted.
- Parallel headless workers that run a queued task as its own Claude Code session. The task text
  goes on standard input, each task starts at most once, and a supervisor records workers that exit
  without a result. `bypassPermissions` requires `TASKY_ALLOW_BYPASS=1`.
- Local dashboard with running, queued, needs-attention and done groups. API calls need a rotating
  access token that is only sent after the server proves it holds it; requests are protected against
  cross-site use and DNS rebinding; data files are private to the user.
- `tasky` command line: `ui`, `serve`, `add`, `list`, `done`, `cancel`, `run`, `import`, `status`.
- Idempotent import of existing Claude Code transcripts.
