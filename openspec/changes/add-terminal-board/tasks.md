## 1. MCP

- [x] 1.1 `board` tool in `tasky/mcp.py`: counts line first, then up to five titles per column, prompts
      only, scoped to this repository unless `scope` is `all`
- [x] 1.2 Tests: counts by column, delegations left out, other repositories left out unless `scope`
      is `all`, an empty board is one line, and `tools/list` names the tool

## 2. Mod

- [x] 2.1 Mod in `hooks/mod/`, inside the tasky plugin: status line from the counts line, cleared when the board is
      empty; `tasky-board` command opening a pane; reads at session start, at every turn's end and on
      a `refresh_seconds` timer
- [x] 2.2 Mod tests under `claude plugin test .`: parsing, the status line following the board
      across a timer tick and a turn's end, the pane drawn on terminal and desktop, `refresh_seconds`
      0, and an unreachable tasky leaving the session untouched
- [x] 2.3 `modules` in `hooks/hooks.json`, `refresh_seconds` in tasky's `userConfig`, README section,
      CHANGELOG
