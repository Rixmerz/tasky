// Pure functions over what tasky's `board` tool answers (tasky/mcp.py).

import type { Board } from '../types'

const COUNTS = /^running (\d+), queued (\d+), needs attention (\d+)$/

/** The board, or null when the answer is not the board's shape. */
export function toBoard(text: string): Board | null {
  const body = text.trim()
  const match = COUNTS.exec(body.split('\n', 1)[0] ?? '')
  if (!match) return null
  return { running: Number(match[1]), queued: Number(match[2]), attention: Number(match[3]), text: body }
}

/**
 * The status line, or undefined when there is nothing to show: an idle board
 * takes no room. The glyphs are the ones `tasky status --short` prints.
 */
export function boardLine(board: Board | null): string | undefined {
  if (!board || board.running + board.queued + board.attention === 0) return undefined
  return `tasky ▶${board.running} ⏸${board.queued} ⚠${board.attention}`
}

/** The server name `$.mcp.call` takes, read off the tool list. */
export function serverFor(toolNames: readonly string[], tool: string, hint: string): string | null {
  const suffix = `__${tool}`
  for (const name of toolNames) {
    if (!name.startsWith('mcp__') || !name.endsWith(suffix)) continue
    const server = name.slice('mcp__'.length, name.length - suffix.length)
    if (server.toLowerCase().includes(hint)) return server
  }
  return null
}
