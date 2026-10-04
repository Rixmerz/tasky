## 1. MCP

- [x] 1.1 `board` tool in `tasky/mcp.py`: counts line first, then up to five titles per column, prompts
      only, scoped to this repository unless `scope` is `all`
- [x] 1.2 Tests: counts by column, delegations left out, other repositories left out unless `scope`
      is `all`, an empty board is one line, and `tools/list` names the tool

## 2. Mod

- [x] 2.1 `mod/` plugin `tasky-mod`: status line from the counts line, cleared when the board is
      empty; `tasky-board` command opening a pane; reads at session start, at every turn's end and on
      a `refresh_seconds` timer
- [x] 2.2 Mod tests under `claude plugin test mod`: parsing, the status line following the board
      across a timer tick and a turn's end, the pane drawn on terminal and desktop, `refresh_seconds`
      0, and an unreachable tasky leaving the session untouched
- [x] 2.3 Marketplace entry, README section, CHANGELOG
