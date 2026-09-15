## Why

Claude Code sessions accumulate work faster than a person can track it: prompts pile up while the
agent is busy, some of them are delegated to background subagents, and after a context compaction
or a closed terminal nobody remembers what was asked, what finished, what the result was, and what
is still waiting. Tracking this through an MCP server costs tool calls and depends on the model
remembering to call them. Claude Code already emits every needed fact through lifecycle hooks, so a
ledger fed by hooks is deterministic and costs no model tokens unless it deliberately injects text.

## What Changes

- Add a Claude Code plugin that records every real user prompt, every delegated subagent, and their
  results into a local SQLite ledger through hooks, without model involvement.
- Add a zero-token chat queue: a prompt that starts with a configurable prefix is stored as a queued
  task and blocked before it reaches the model.
- Add opt-in queue draining: when a session finishes a turn, it can pull the next queued task.
- Add context recovery: on resume or compaction the session receives its unfinished tasks.
- Add parallel workers: a queued task can be started as its own headless Claude Code session.
- Add a local web dashboard and a CLI to view, add, reorder, complete, cancel and run tasks.
- Add a one-shot importer that fills the ledger from existing Claude Code transcripts.

## Capabilities

### New Capabilities

- `task-capture`: hook-driven recording of prompts, delegations, results and session lifecycle.
- `task-queue`: queuing from chat, dashboard or CLI, opt-in draining, and context recovery.
- `parallel-workers`: starting a queued task as a detached headless session.
- `dashboard`: local HTTP dashboard and JSON API with localhost-only protections.
- `history-import`: idempotent import of past sessions from transcript files.

### Modified Capabilities

None.

## Impact

- New Python package (standard library only, Python 3.10+), plugin manifest, hooks configuration,
  static web assets, CLI entry point and test suite.
- Writes only to a per-user data directory; never modifies Claude Code settings or transcripts.
- A failing hook must never break or slow down a Claude Code session.
