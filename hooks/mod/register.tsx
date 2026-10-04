// The mod: tasky's board, without leaving the terminal.
//
// Tasky's dashboard is a browser tab, and a tab is one more place attention has
// to go. This reads the same board through tasky's own `board` MCP tool and
// shows it where the person already is: a line on the status bar while
// anything is running, queued or waiting on them, and a pane on request.
//
// Every hook fails open: a board that cannot be read leaves the last one shown.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Board } from '../../types'
import { boardLine, serverFor, toBoard } from './board'

const PANE = 'tasky-board'
const COMMAND = 'tasky-board'
const FALLBACK = ['plugin_tasky_tasky', 'tasky']

const board = atom({ plugin: 'tasky', key: 'board' } as const, null as Board | null)

async function refresh($: EngineInterface): Promise<void> {
  let servers = FALLBACK
  try {
    const found = serverFor((await $.tool.list()).map(t => t.name), 'board', 'tasky')
    if (found) servers = [found]
  } catch {
    // An unreadable tool list is no reason not to try the usual names.
  }
  for (const server of servers) {
    try {
      const result = await $.mcp.call(server, 'board', {})
      if (result.isError) continue
      const text = result.content
        .filter(block => block.type === 'text' && block.text)
        .map(block => block.text)
        .join('\n')
      const now = toBoard(text)
      if (!now) continue
      await update($, board, () => now)
      $.ui.status(boardLine(now))
      return
    } catch {
      // Not this name: try the next.
    }
  }
}

export const register: Register = (on, options) => {
  const seconds = Number((options as Record<string, unknown> | undefined)?.refresh_seconds ?? 60)

  on('session.start', async ($, e, next) => {
    try {
      await $.command.register({
        name: COMMAND,
        description: "Show tasky's board: what is running, queued and needs you",
      })
      await refresh($)
      if (seconds > 0) {
        $.clock.every(seconds * 1000, () => {
          void refresh($).catch(() => undefined)
        })
      }
    } catch {
      // Fail open.
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    try {
      await refresh($)
    } catch {
      // Fail open.
    }
    return done
  })

  on('command.run', { command: COMMAND }, async $ => {
    await refresh($)
    await $.ui.open({ id: PANE, title: 'tasky' })
    return { text: boardLine(await read($, board)) ?? 'tasky: nothing running, queued or waiting.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const now = await read($, board)
    if (!now) {
      return (
        <Box flexDirection="column">
          <Text dimColor>Tasky's board could not be read. Is the tasky plugin enabled?</Text>
        </Box>
      )
    }
    return (
      <Box flexDirection="column">
        {now.text.split('\n').map(line => (
          <Text bold={!line.startsWith(' ')}>{line}</Text>
        ))}
      </Box>
    )
  })
}
