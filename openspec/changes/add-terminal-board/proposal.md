## Why

The board lives in a browser tab, and a tab is one more place attention has to go. For a user who
hands an agent more work than they can keep in their head, the question "is anything waiting on me?"
should be answerable from the terminal they are already in, at zero tokens. Claude Code 2.1.287 added
mods: TypeScript a plugin ships that runs inside Claude Code, can draw on the status line and in a
pane, and can call a connected MCP server. Tasky already has an MCP server; it has no tool that says
what is on the board.

## What Changes

- A new MCP tool, `board`: the counts of the live columns (running, queued, needs attention) for this
  repository or all of them, then the newest titles per column. The first line stands on its own, so a
  status line can show it whole. It counts prompts, as the dashboard's columns do.
- A second, opt-in plugin, `tasky-mod`, under `mod/`: it reads `board` and shows `tasky ▶1 ⏸2 ⚠1` on
  the status line while anything is running, queued or needs attention, nothing when the board is
  empty, and the board in a pane through a `tasky-board` command. It reads at the end of every turn and
  every `refresh_seconds` between turns (60 by default, 0 for turn boundaries only).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard`: the board is readable through MCP, and shown in the terminal by an opt-in mod.

## Impact

- `tasky/mcp.py`: the `board` tool. No schema change; it reads `list_tasks` and `repo_cwds`.
- `mod/`: a new plugin, listed in this repository's marketplace. It is installed only when asked,
  because a mod runs inside Claude Code with no sandbox. Its tests run under `claude plugin test mod`.
- The "Asked you" reason is not part of `board`: it reads the newest reply of an open session at render
  time, which stays the dashboard's to compute.
