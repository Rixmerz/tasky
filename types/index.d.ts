/** Tasky's `board` answer: the counts line, and the full text for the pane. */
export type Board = {
  running: number
  queued: number
  attention: number
  text: string
}

declare module 'claude-code' {
  interface PluginState {
    tasky: {
      board: Board | null
    }
  }
}
