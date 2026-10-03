import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { boardLine, serverFor, toBoard } from './board'

// The text tasky's `board` tool returns (tasky/mcp.py, _board).
const BUSY = [
  'running 1, queued 2, needs attention 1',
  'running:',
  '  task #7 [running] migrate invoices',
  'queued:',
  '  task #8 [queued] write the parser',
  '  task #9 [queued] update the docs',
  'needs attention:',
  '  task #5 [failed] deploy staging',
].join('\n')
const IDLE = 'running 0, queued 0, needs attention 0'

describe('reading the board', () => {
  test('the counts line becomes the status line', () => {
    expect(toBoard(BUSY)).toEqual({ running: 1, queued: 2, attention: 1, text: BUSY })
    expect(boardLine(toBoard(BUSY))).toBe('tasky ▶1 ⏸2 ⚠1')
  })

  test('an idle board takes no room, and a foreign answer is no board', () => {
    expect(boardLine(toBoard(IDLE))).toBe(undefined)
    expect(toBoard('No such tool')).toBe(null)
    expect(boardLine(null)).toBe(undefined)
  })

  test('the server is read off the tool list', () => {
    expect(serverFor(['mcp__plugin_tasky_tasky__board', 'Read'], 'board', 'tasky')).toBe('plugin_tasky_tasky')
    expect(serverFor(['mcp__other__board'], 'board', 'tasky')).toBe(null)
  })
})

function engine(on: On) {
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('turn.complete', () => ({ text: '' }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
}

const START = { cwd: '/repo', surface: 'terminal', isInteractive: true } as const

describe('in the engine', () => {
  test('the status line follows the board, and the pane draws it', async ($, on) => {
    engine(on)
    const clock = mock.clock(on)
    let answer = BUSY
    const statuses: (string | undefined)[] = []
    on('tool.list', () => ({ value: [{ name: 'mcp__plugin_tasky_tasky__board', description: '', mcp: true }] }))
    on('mcp.call', () => ({ value: { content: [{ type: 'text', text: answer }], isError: false } }))
    on('ui.status', ($, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })

    await $.session.start(START)
    expect(statuses.at(-1)).toBe('tasky ▶1 ⏸2 ⚠1')

    for (const surface of ['terminal', 'desktop'] as const) {
      const pane = await $.ui.mount({
        plugin: 'tasky-mod',
        surface,
        component: 'Pane',
        requestId: 'tasky-board',
        props: {
          title: 'tasky',
          isFocused: false,
          bodyColumns: 80,
          placement: 'dock',
          scroll: { offset: 0, bodyRows: 30 },
          view: {},
        },
      })
      expect(await pane.find({ type: 'Text', text: /deploy staging/ })).toBeDefined()
      await pane.unmount()
    }

    // A background worker finishes between turns: the timer picks it up.
    answer = BUSY.replace('running 1, queued 2', 'running 0, queued 2')
    await clock.advance(60_000)
    expect(statuses.at(-1)).toBe('tasky ▶0 ⏸2 ⚠1')

    // Everything finished: the read at the end of a turn clears the line.
    answer = IDLE
    await $.turn.complete({ reason: 'answer', answer: 'done', durationMs: 10, isAborted: false, turnId: 't1' })
    expect(statuses.at(-1)).toBe(undefined)
  })

  test('refresh_seconds 0 reads only at turn boundaries', { options: { refresh_seconds: 0 } }, async ($, on) => {
    engine(on)
    const clock = mock.clock(on)
    let reads = 0
    on('tool.list', () => ({ value: [{ name: 'mcp__plugin_tasky_tasky__board', description: '', mcp: true }] }))
    on('mcp.call', () => {
      reads += 1
      return { value: { content: [{ type: 'text', text: IDLE }], isError: false } }
    })
    on('ui.status', () => ({ value: undefined }))
    await $.session.start(START)
    await clock.advance(10 * 60_000)
    expect(reads).toBe(1)
  })

  test('with tasky unreachable the session goes on and nothing is drawn', async ($, on) => {
    engine(on)
    mock.clock(on)
    const statuses: (string | undefined)[] = []
    on('tool.list', () => ({ value: [] }))
    on('mcp.call', () => {
      throw new Error('no such server')
    })
    on('ui.status', ($, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    const started = await $.session.start(START)
    expect(started.cwd).toBe('/repo')
    expect(statuses.length).toBe(0)
  })
})
