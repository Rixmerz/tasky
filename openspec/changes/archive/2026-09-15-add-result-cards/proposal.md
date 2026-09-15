## Why

Task results are what an agent actually delivered, but the dashboard shows them as raw markdown in
a monospace block. For a reader with ADHD that is a wall of text: the one sentence that matters is
buried, and there is no way to tell at a glance which finished task is waiting for an answer.
People who already use an ADHD-friendly output style get replies with a predictable shape (answer
first, one bold lead-in per point), and that shape can be turned into cards. Other users have no such
style available from Tasky.

## What Changes

- Render every task result as a readable digest: the bottom line as a headline, one card per point,
  numbered steps, lists, tables and code blocks, and a highlighted "waiting for you" card when the
  reply ends with a question. The raw text stays one click away.
- Show the result's first sentence as a one-line summary on collapsed cards, and mark tasks whose
  result is waiting for the user.
- Ship an original ADHD-friendly output style with the plugin, selectable by any user from the
  output style picker, whose replies render cleanly as cards.
- Redact dashboard access links from every stored result, not only imported ones.

## Capabilities

### New Capabilities

- `focus-output-style`: the bundled output style and its contract with the renderer.

### Modified Capabilities

- `dashboard`: results are rendered as digests and collapsed cards carry a summary.
- `task-capture`: stored results never contain the dashboard access token.

## Impact

- New `output-styles/focus-cards.md` and `tasky/web/digest.js`; changes to `tasky/web/app.js`,
  `tasky/web/app.css`, `tasky/server.py` (one static route), `tasky/store.py` (redaction), README and
  CHANGELOG. Version 0.2.0.
- No change to Claude Code settings: the style is offered in the picker, never activated by Tasky.
- The style text is original work under the MIT license. It follows the same reply shape as the
  AGPL-licensed attention-span project, credited as inspiration, without copying its text.
