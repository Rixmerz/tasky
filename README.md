# Tasky

A visual task ledger for [Claude Code](https://claude.com/claude-code). Tasky records every
prompt you give a session, every subagent it delegates to, and every result, then shows them on a
local dashboard: what is running now, what is waiting, what needs your attention and what finished.

It is fed entirely by Claude Code hooks, so recording costs **zero model tokens**. There is no MCP
server for the model to remember to call, and nothing is lost when a conversation is compacted.

Built for people who hand an agent more work than they can keep in their head, and especially for
anyone whose attention is the scarcest resource in the room.

## What it does

- **Captures automatically.** Real prompts become tasks; `Agent` calls become child tasks; the
  final assistant message becomes the result. Background subagents are matched to their parent and
  closed when they report back. A prompt you interrupt with Esc moves to "needs attention" when
  the session finishes its next turn or is resumed.
- **Queues from the chat for free.** Start a prompt with `++` and it is stored as a queued task
  and blocked before it reaches the model: zero turns, zero cost.
- **Drains the queue when you want it to.** Turn on auto-pull for a session and, each time it
  finishes a turn, it picks up the next queued task for its project.
- **Survives compaction.** When a session is resumed or compacted, it is reminded of its
  unfinished tasks.
- **Three ways to start a task.** Run it now as its own headless session, put it after the last
  one in its project's run queue, or run it in parallel as a clone of a session that already has
  the context.
- **Remembers the past.** Import your existing Claude Code transcripts so the board is not empty
  on day one.
- **Shows results as cards, not markdown.** Each result opens with its bottom line; every point,
  step, table and code block gets its own card, and a reply that ends with a question is marked
  "waiting for you" right on the collapsed card.
- **Ships an ADHD-friendly output style.** Pick `tasky:Focus Cards` and replies come back answer
  first, one idea per block, in the shape the dashboard turns into cards.

## Install

Requirements: Claude Code, Python 3.10 or newer on `PATH` as `python3`. No other dependencies.

Inside Claude Code:

```text
/plugin marketplace add Rixmerz/tasky
/plugin install tasky@tasky
```

Restart Claude Code so the hooks load. From then on every session is recorded.

To use the command line outside Claude Code, link the shim somewhere on your `PATH`:

```sh
ln -s "$(ls -d ~/.claude/plugins/cache/tasky/tasky/*/ | tail -1)bin/tasky" ~/.local/bin/tasky
```

Or run it from a clone: `./bin/tasky --help`.

## Use

### Open the dashboard

```text
/tasky:ui
```

or, from a terminal, `tasky ui --open`. Both print an access link like
`http://127.0.0.1:7733/#token=…`. Open that link; the tab keeps the token until you close it, and the
page refreshes itself within two seconds of any change and loads nothing from the network. If the
token is rotated or the tab is closed, run `tasky ui` again for a fresh link.

The board uses the full width of the screen, one column per stage:

| Column | What is there |
| --- | --- |
| **Inbox** | Tasks you created that are not scheduled yet |
| **Up next** | Each project's run queue. Top to bottom is the order they will run in |
| **Running** | **Queue runner** (the task started from Up next) above **Parallel** (everything else running) |
| **Needs attention** | Interrupted or failed tasks, with Run again and Back to Inbox |
| **Done** | Finished tasks, newest first |

A task is one line: a status dot, its title and how long ago. Click the title for the full text, the
result and the session it ran in. Inbox and Up next rows carry three small buttons, Run now, After
last and Parallel with context; the "..." menu on every row repeats them with words and holds the rest
(edit, move, back to Inbox, mark done, cancel, delete). A row whose result ends with a question shows
a "Needs answer" mark. Empty columns shrink so the ones with work get the width.

Create tasks from the bar at the top. Its "to" picker lists your active sessions first (sending a
task to one ties it to that session), then each active project, and everything else under "Other".
The sliders button on the right holds the project filter and the permission mode the row buttons use;
an active filter shows as a chip you can click to clear. Drag rows to reorder Up next or to move them
between Inbox and Up next; with the keyboard, focus a row's handle, press Space, move with the arrow
keys and press Enter. Rows cycle through five colour rails so neighbours are easy to tell apart. On a
phone, one column shows at a time.

The search box next to the bar (press `/`) finds past tasks by any word in their title, prompt or
reply, across everything Tasky has recorded, not just what the board shows. Every word must match,
the newest 50 hits come first, the project filter applies, and clicking a hit opens it in the side
panel: read an old answer again instead of asking Claude, at no token cost. Esc clears it.

### Queue a task

| Where | How | Model tokens |
| --- | --- | --- |
| Any session | `++ write the migration for invoices` | 0 |
| Dashboard | The quick-add line at the top | 0 |
| Terminal | `tasky add "write the migration for invoices" --cwd path/to/project` | 0 |

A task queued from a session is bound to that session. A task queued from the dashboard or the
terminal belongs to its project directory and can be picked up by any session working there.

Telling Claude directly ("after this, also do X") works too, and is recorded, but Claude decides
when to do it. Queued tasks wait for you or for auto-pull.

### Let a session work through its queue

Toggle **auto-pull** on a session in the dashboard. When that session ends a turn, Tasky hands it
the next queued task: first the ones bound to the session, then the unbound ones for its project.

Each pulled task is a normal model turn, so it spends tokens like any prompt. A session pulls at
most 5 tasks in a row before waiting for you again (`TASKY_MAX_CHAIN`).

### Start a task

Every card in Inbox and Up next has three buttons:

| Button | What happens | Context |
| --- | --- | --- |
| **Run now** | Starts a headless session right away | None, a fresh session |
| **After last** | Moves the task to the end of its project's run queue | None, a fresh session when its turn comes |
| **Parallel** | Starts right away as a clone of the project's session | The whole conversation of that session |

From a terminal:

```sh
tasky run 12 --permission-mode acceptEdits    # Run now
tasky enqueue 12 --permission-mode acceptEdits # After last
tasky run 12 --mode fork                       # Parallel with context
```

**The run queue.** Tasky runs one task at a time per project from Up next, in order, and starts the
next one when the previous worker exits. If a task from the queue fails or is interrupted, that
project's queue pauses so the next task does not build on a broken state; the Running column shows
why, with a Resume button. Projects' queues run independently of each other.

**Parallel with context.** Tasky clones the task's own session, or else the latest session you worked
in for that project (preferred over headless worker sessions), with
`claude --resume <session> --fork-session`. The clone gets a new session id and the original
conversation is not changed. Two things to know:

- The clone replays the whole original conversation as input, so it costs more than a fresh run.
- The result lands on the Tasky card. The original session never sees it.

Every run starts `claude -p` in the task's directory as a detached process with its own session id. The
task text is passed on standard input, never as a command-line argument. Its hooks attach to the
existing task, so the result lands on the same card. A small supervisor waits for the process and
marks the task failed if it exits without reporting a result. Output goes to
`~/.local/share/tasky/logs/task-<id>.log`.

A task can only be started once: a double click or two runs racing for it start one process.

A headless session cannot ask you for permission. Pick the mode deliberately:

| Mode | Effect in a headless run |
| --- | --- |
| `default` | Tools that need approval are refused |
| `acceptEdits` | File edits are allowed; other gated tools are refused |
| `plan` | Read-only planning |
| `bypassPermissions` | Everything is allowed without asking. Disabled unless `TASKY_ALLOW_BYPASS=1` is set where the dashboard or CLI runs. Use only in a sandbox or a throwaway checkout |

### Import history

```sh
tasky import --dry-run
tasky import
```

Reads `~/.claude/projects/*/*.jsonl`. It is idempotent, tolerates damaged lines, skips subagent
transcripts, and skips any session that hooks or workers have already recorded. Imported sessions
are shown as ended.

### Readable results and the Focus Cards style

Results are rendered as a digest: the first sentence becomes the headline, each paragraph that opens
with a bold lead-in becomes a card, numbered `**1 →**` steps keep their numbers, and a final question
is highlighted as waiting for you. Replies in any style work; "Show original" reveals the raw text.

Tasky also ships an output style that produces exactly that shape: answer first, one idea per block,
bold lead-ins that carry the meaning, warnings next to what they affect, and a blocking question last.
Tasky never switches your style for you. To use it, run `/output-style` in Claude Code and choose
`tasky:Focus Cards`, or set it in your settings:

```json
{ "outputStyle": "tasky:Focus Cards" }
```

The reply shape is inspired by [attention-span](https://github.com/alexgreensh/attention-span). The
Focus Cards text is original and MIT-licensed; if you already use an attention-span style, keep it:
its replies render as cards too.

### Put the counters in your status line

`tasky status --short` prints `▶2 ⏸3 ⚠1` (running, queued, needs attention) and reads only the local
database. Add it to your own status line command if you want the numbers always visible.

### Updating

```sh
claude plugin marketplace update tasky
claude plugin update tasky@tasky
```

Then restart Claude Code or run `/reload-plugins`, and recreate the `~/.local/bin/tasky` link if you
made one, because it points into the versioned plugin directory.

### Command reference

```text
tasky ui [--port N] [--open]        start the dashboard in the background if needed, print the URL
tasky serve [--port N] [--open]     run the dashboard in the foreground
tasky add TEXT [--cwd DIR] [--session ID]
tasky list [--status S] [--cwd DIR] [--limit N] [--json]
tasky done ID | tasky cancel ID
tasky run ID [--mode now|fork] [--permission-mode MODE]
tasky enqueue ID [--permission-mode MODE]
tasky import [--dry-run]
tasky status [--short]
```

## How it works

| Hook | What Tasky does | Output |
| --- | --- | --- |
| `SessionStart` | Registers the session. On resume, marks tasks left running by the old process as interrupted. On resume or compaction, lists unfinished tasks | Context, only when there is something unfinished |
| `UserPromptSubmit` | Records the prompt, handles `++`, closes subagents on task notifications | Blocks `++` prompts only |
| `PreToolUse` / `PostToolUse` (`Agent`) | Records delegations | None |
| `SubagentStop` | Stores the subagent's final message | None |
| `Stop` | Stores the turn's final message, marks earlier interrupted prompts, pulls the next task when auto-pull is on | Continues the session only with auto-pull |
| `StopFailure` / `SessionEnd` | Marks failed or interrupted work | None |

Tool hooks are matched to exactly `Agent` or `Task`, so Tasky does not start for every `Bash` or `Read`
call. Every hook exits successfully and prints nothing when something goes wrong; errors go to `~/.local/share/tasky/hook-errors.log`. A broken Tasky never breaks a session.

### What costs tokens

| Action | Tokens |
| --- | --- |
| Recording prompts, delegations and results | 0 |
| Queueing with `++`, the dashboard or the CLI | 0 |
| Dashboard, search, CLI and status line | 0 |
| Context reminder after resume or compaction | A few lines, only when tasks are unfinished |
| Auto-pull | One normal turn per pulled task |
| Run now and the run queue | One normal headless session per task |
| Parallel with context | One headless session per task, starting with the cloned conversation as input |

## Data and privacy

Everything stays on your machine, in one SQLite database readable only by your user:

```text
$TASKY_HOME/tasky.db   (default: $XDG_DATA_HOME/tasky or ~/.local/share/tasky)
```

Tasky stores prompt text, subagent prompts and final assistant messages (results are capped at
8000 characters, and dashboard access links in them are redacted). It never modifies your Claude Code settings or transcripts. Delete the directory
to erase everything.

## Security

The dashboard binds to `127.0.0.1` only. Because it can start Claude Code processes, it also:

- requires a random access token on every API call. The token lives in `$TASKY_HOME/token`
  (mode 0600) and is re-read on every request, so deleting the file revokes every open tab;
- proves it holds the token before anyone sends it: `tasky ui` and the page first ask the server for
  an HMAC of a random nonce and the port, and only send the token when the answer checks out. A
  program squatting on the port never receives it;
- rejects requests whose `Host` header is not `127.0.0.1` or `localhost` at its port, which blocks
  DNS rebinding;
- accepts changes only as JSON with the token header, which a cross-site page cannot send without
  a CORS preflight that Tasky never approves;
- serves a strict Content Security Policy with no inline scripts and no third-party resources;
- refuses `bypassPermissions` workers unless `TASKY_ALLOW_BYPASS=1` is set;
- keeps its database, token, prompts and logs readable only by your user.

What it does not protect against:

- **Programs running as your user.** They can read the token and use Tasky, just as they could run
  `claude` themselves. That includes a Claude Code session you allowed to run shell commands: it could
  start a worker in another directory, and with `TASKY_ALLOW_BYPASS=1` set, with more permissions than
  its own. Leave the variable unset unless you need it.
- **The access link in your transcript.** `/tasky:ui` prints the link into the conversation, so the
  token is stored in that session's transcript (also private to your user). Delete
  `$TASKY_HOME/token` to rotate it; Tasky redacts it from every result it stores.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TASKY_HOME` | `$XDG_DATA_HOME/tasky` or `~/.local/share/tasky` | Database, logs and error log |
| `TASKY_PORT` | `7733` | Dashboard port |
| `TASKY_QUEUE_PREFIX` | `++` | Prompt prefix that queues instead of sending |
| `TASKY_MAX_CHAIN` | `5` | Tasks auto-pull may take in a row |
| `TASKY_MAX_RESULT` | `8000` | Characters of each result kept |
| `TASKY_CONTEXT_ITEMS` | `10` | Tasks listed in the resume or compaction reminder |
| `TASKY_CLAUDE_BIN` | `claude` | Binary used for parallel workers |
| `TASKY_ALLOW_BYPASS` | unset | Set to `1` to allow `bypassPermissions` workers |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Where transcripts are imported from |

Hooks read these from the environment of the Claude Code process.

## Limitations

- Hook payloads are not all documented. Tasky was verified against Claude Code 2.1.272 and falls
  back gracefully when a field is missing, but a future change may reduce what it can match.
- A prompt you type while the agent is busy is recorded when Claude Code submits it, not when you
  type it. If Claude Code folds it into the running turn, the earlier prompt of that turn can show
  as interrupted.
- Claude Code sends no event when you press Esc, so an interrupted prompt is only marked when the
  session finishes its next turn or is resumed.
- A run queue task left `running` by a killed worker blocks its project's queue until you cancel it.
- When a background subagent reports back, its parent task's result becomes the final report
  rather than the first reply.
- Search only sees the stored part of a reply (the first `TASKY_MAX_RESULT` characters, 8000 by
  default), and ignores letter case for ASCII only: "Ó" and "ó" are different letters to it.

## Development

```sh
pytest -q
ruff check .
claude --plugin-dir . -p "hello"          # try the plugin without installing it
TASKY_HOME=$(mktemp -d) ./bin/tasky serve  # dashboard against an empty database
```

The behaviour contract lives in `openspec/specs/`; the design and history of v0.1.0 are in `openspec/changes/archive/2026-09-15-add-tasky-v1/`.

## License

[MIT](LICENSE)
