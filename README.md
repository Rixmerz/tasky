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
  closed when they report back. A message you type while Claude is working joins the running task
  instead of becoming a new one. A prompt you cancel with Esc before Claude replies is dropped, so
  cancelling to rewrite a prompt leaves one task; one Claude had started working on moves to "needs
  attention" when the session finishes its next turn or is resumed.
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
- **Keeps every conversation.** Every message, tool call and tool result, subagents' included, is
  copied into the ledger at the end of each turn, before each compaction and at session end, so it
  stays searchable after Claude Code deletes its transcripts (30 days by default). Tokens and files
  edited are counted per task, for free.
- **Keeps you oriented.** Claude Code's recaps (the summary it writes when you come back to a
  session) are shown large above the board, and among the tasks where they happened.
- **Finds old answers.** Plain search by words at no cost, and Smart search, which asks Haiku to
  match by meaning when the words differ.
- **Keeps a history of problems and what was tried.** Per repository: problems with the ordered
  chain of fixes attempted and why each failed, milestones, a map, and an MCP server so the agent
  can check what already failed before trying it again.
- **Knows the parts of each repository.** Areas (business or technical: "checkout", "auth",
  "ci") with aliases and folders, the specs and decisions that describe them (OpenSpec, spec-kit,
  Kiro, ADRs, Archify, Graphify), and where work happened, for the dashboard and the agent.
- **Suggests how to `/compact`.** After a history sync, each open session gets `/compact`
  instructions that keep what matters and drop the rest, one click to copy.
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
| **Needs attention** | What needs you, each with its reason: **Failed**, **Stopped mid-work** (interrupted after Claude started), or **Asked you** (the newest reply of an open session ends with a question) |
| **Done** | Finished tasks, grouped by day and then by session, newest first |

A card shows its title on up to two lines, the first sentence of the reply, and a facts row: how
long it took, files edited, output tokens and messages you added while it ran. Click it for the full
text, the result, those messages, the files edited, the token breakdown, the session and the recaps
around it. Inbox and Up next rows carry three small buttons, Run now, After last and Parallel with
context; the "..." menu on every row repeats them with words and holds the rest (edit, move, back to
Inbox, mark done, cancel, delete). Delete hides the task, with Undo for a few seconds; the row stays
for the history sync. The "needs answer" mark only shows on the newest task of a session. Empty
columns fold into a thin strip, which opens again when you drag a card over it.

Done groups are headed by the session's name and colour, the same colour as its cards' left bar,
with its task count and output tokens; today's groups start open. Above the columns, **Latest
recaps** shows the summary Claude Code wrote when you last came back to each open session, and
recaps also appear in Done among the tasks where they happened. A yellow bar names open sessions
that still run an older tasky's hooks: restart them.

Create tasks from the bar at the top. Its "to" picker lists your active sessions first (sending a
task to one ties it to that session), then each active project, and everything else under "Other".
Next to it, pick the permission mode the task will run with; it is kept on the task, so its run
buttons use it. The sliders button on the right holds the project filter; an active filter shows as
a chip you can click to clear. Drag rows to reorder Up next or to move them
between Inbox and Up next; with the keyboard, focus a row's handle, press Space, move with the arrow
keys and press Enter. Rows cycle through five colour rails so neighbours are easy to tell apart. On a
phone, one column shows at a time.

The search box next to the bar (press `/`) finds past tasks by any word in their title, prompt or
reply, across everything Tasky has recorded, not just what the board shows. Every word must match,
the newest 50 hits come first, the project filter applies, and clicking a hit opens it in the side
panel: read an old answer again instead of asking Claude, at no token cost. Esc clears it.

When the words you remember are not the ones used back then, press **Smart search** under the
results. It sends your search text and a compact index of the ledger (start of each prompt, messages
added during it, start of the reply, date, project) to Haiku, with no tools, and lists the tasks it
names with one line on why each matches. It costs about 6¢ the first time and 2–3¢ on the next
searches (the index is cached for a few minutes) and takes 10–30 seconds. Ids Haiku names that were
not in the index are dropped.

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

Reads `~/.claude/projects/*/*.jsonl`. It is idempotent, tolerates damaged lines, does not turn
subagent transcripts into tasks, and skips any session that hooks or workers have already recorded. Imported sessions
are shown as ended. It also copies every message of every transcript into the ledger (in the
background when started from the dashboard), subagent transcripts included, which is what keeps
them after Claude Code's 30-day cleanup. Once copied, it repairs rows earlier versions recorded
wrongly: turns split into several tasks are merged, and prompts cancelled before any reply are
dropped.

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

### Project history (the one feature that spends tokens)

The History button in the top bar keeps, per repository, what was learned while working on it:

- **Problems**, each with its symptom, cause and state (open, solved, recurring) and the ordered
  **chain of attempts**: every fix actually applied, whether it worked, failed or partly worked,
  why it failed, from when it was believed correct until when it was shown wrong, and a verbatim
  quote from the task that proves it.
- **Dead ends**: every failed attempt across problems, with what worked instead, so nobody applies
  a fix that already failed.
- **Timeline**: dated milestones, independent of versions.
- **Map**: repository → topics → problems and milestones → attempts, drawn from the same records.

History is grouped by repository (its `origin` remote), so worktrees and clones share it, and the
scope selector also shows **all repositories** as one bank of problems and solutions. Every record
links to the tasks it came from.

Nothing is generated until you press **Sync** (or run `tasky history --cwd DIR`). A sync reads
only the tasks finished since the previous one, in batches of up to 40, with the history already
kept and the repository's git log for the same days. It runs `claude -p` with the model you pick
(Sonnet by default, Opus for more thoroughness), no tools, no MCP servers, no saved
session and Tasky's own hooks off. Nothing it returns is taken on trust: records must cite tasks
it was shown, evidence must appear verbatim in them, commits must be in the log it was given, and
it can only change records of the repository being synced. The status line shows what the last
sync cost as Claude Code reports it; syncing one afternoon of work (15 tasks) with Sonnet cost
about $0.08 before 0.11.0.

The sync runs at effort `high`. On the same real batch, the default, low and medium efforts listed
the problems but recorded almost none of the fixes tried; high recorded three attempts that
worked, each with a verified quote, for about twice the cost ($0.18 against $0.08 for 7 tasks).

When the repository has areas (see Architecture), the sync is shown them and their specs: each
problem and milestone gets the area it belongs to as its topic (a topic that is an area's alias
becomes the area's name), may name the specs it is about (the requirement a problem breaks, the
change a milestone completes), and words you used for an area that are not its aliases yet
become aliases. An alias is kept only if you actually wrote it in one of the tasks, and never on
an area you edited yourself.

Reading the history is free. It also reaches the agent in two ways:

- When a session starts in a repository with failed attempts, the newest five are added to its
  context (`TASKY_DEAD_END_ITEMS`, `0` turns it off).
- The plugin ships an MCP server, `tasky`, with `search_history`, `get_problem`, `dead_ends`,
  `search_tasks` and `record_attempt`. The last one lets the agent write an attempt and its outcome
  the moment it knows, with no sync and no extra model call; those attempts are tagged "agent".
  `search_conversations` searches every stored message (with the turns around each hit) and
  `last_session` says what the latest sessions in the repository did, with their latest recap.
  `get_architecture` names the repository's areas (see below).

`search_history` climbs a fixed ladder of free steps and stops at the first one with results, so
it never turns into a hunt: every word as written, plus the problems and milestones of any area
the question names by name or alias ("inicio de sesión", or "mongo" for an area aliased
"mongodb"); then any word; and when nothing matches, the areas that have history, with how much,
to ask again by area. No step calls a model: the agent asking is already one.

After a sync, every session that is still open and had tasks in it gets suggested instructions for
Claude Code's `/compact`: what the summary must keep (the goal in progress, decisions and why, open
problems and the attempts that already failed, files in flight, your stated constraints, the next
step) and what it can drop. They are written in English and telegraphic, and ask for a terse
summary that still keeps every decision's reason and every failed attempt's cause. They appear
under **Suggested /compact** in the History panel and as
**Copy /compact text** in the task's side panel. In that session type `/compact`, a space, then
paste: the copy leaves the command out because Claude Code collapses a long paste into "[Pasted
text]" and would not run a `/compact` inside it. They come out of the same
model call as the sync, at no extra cost.

### Architecture: areas and specs

The Architecture button in the top bar shows, per repository, its **areas**: the controlled
vocabulary for where work happened. An area is a business part of the product ("checkout",
"onboarding") or a technical part of the system ("auth", "ci", "database"), not a layer, with:

- **aliases**: synonyms, the other language, and the History topics that mean it, so "pago",
  "payments" and "checkout" land in one place;
- **paths**: the folders that belong to it. A task is in the areas of the files it edited, and each
  area shows how many turns edited it, how many files, and when last, counted from every edit
  Tasky copied, including sessions with no task on the board;
- **specs**: OpenSpec capabilities and changes, spec-kit features, Kiro specs and architecture
  decision records (Nygard, MADR and home-grown styles, Spanish headings included), with their
  status, summary and requirement names;
- the **problems** and **milestones** of the History that belong to it: by topic, else by the
  specs they name, else by the files their tasks edited. Each spec also lists the problems and
  milestones that name it.

**Scan** (free) reads the repository: specs, ADRs, `*.architecture.json` from
[Archify](https://github.com/tt-a1i/archify) and `graphify-out/graph.json` from Graphify. When a
repository has no areas yet and Archify boundaries or Graphify communities exist, the scan adopts
them. **Map areas** makes one model call (Sonnet by default, Opus optional; about $0.09 for a
900-file repository with 12 specs, about $0.13 at effort medium since 0.11.0) that proposes the vocabulary from the folder tree, the specs,
the candidates, the History topics and where edits happened. Nothing it returns is trusted: paths
must exist, specs must be ones it was shown, a path belongs to one area only. You can add, rename,
edit and delete areas; an area you edited keeps its name and description when you map again, and
only gains paths, specs and aliases.

**Draw with Archify** appears when the [Archify](https://github.com/tt-a1i/archify) skill is
installed for Claude Code (`npx skills add tt-a1i/archify -g`). It starts one headless session, a
normal task on the board, that uses the skill to draw the repository's runtime architecture from
code evidence, with Tasky's areas as boundary names, into `docs/architecture/<repo>.architecture.json`
and `.html` in your checkout (not committed). The session may only edit files in the checkout, run
Archify's own CLI and a few read-only git commands. When it finishes the repository is scanned
again, the diagram's boundaries become candidate areas, **View** shows the diagram inside the
dashboard and **Open** in your browser. Inside the dashboard the page runs sandboxed from a
short-lived link: it keeps its zoom, themes and export, and cannot read Tasky's token or call its
API. It is the most expensive button in Tasky: drawing Tasky itself with Sonnet took 15
minutes and about $6 (90 turns validating and fixing the diagram until Archify accepted it).
Archify draws one repository per diagram: its code evidence is pinned to one origin and
commit, so other services can only appear as external components.

The agent reads the same map with the MCP tool `get_architecture`: the areas with their folders,
aliases and activity, or one area in detail with its specs' requirements, problems and the fixes
that failed, milestones and recent tasks. The CLI is `tasky architecture [--map]`.

### Put the counters in your status line

`tasky status --short` prints `▶2 ⏸3 ⚠1` (running, queued, needs attention) and reads only the local
database. Add it to your own status line command if you want the numbers always visible.

### Updating

```sh
claude plugin marketplace update tasky
claude plugin update tasky@tasky
```

Then restart Claude Code or run `/reload-plugins`: a session keeps the hooks of the version it
started with, and the dashboard flags open sessions still running older ones. Recreate the
`~/.local/bin/tasky` link if you made one, because it points into the versioned plugin directory.

### Command reference

```text
tasky ui [--port N] [--open]        start the dashboard in the background if needed, print the URL
tasky serve [--port N] [--open]     run the dashboard in the foreground
tasky add TEXT [--cwd DIR] [--session ID] [--permission-mode MODE]
tasky list [--status S] [--cwd DIR] [--limit N] [--json]
tasky done ID | tasky cancel ID
tasky run ID [--mode now|fork] [--permission-mode MODE]
tasky enqueue ID [--permission-mode MODE]
tasky import [--dry-run]
tasky history [--cwd DIR | --repo KEY] [--model sonnet|opus]
                                    sync a repository's history (spends tokens)
tasky architecture [--cwd DIR | --repo KEY] [--map] [--model sonnet|opus]
                                    scan specs and areas; --map defines areas (spends tokens)
tasky mcp                           serve the history and ledger as MCP tools on stdio
tasky status [--short]
```

## How it works

| Hook | What Tasky does | Output |
| --- | --- | --- |
| `SessionStart` | Registers the session. On resume, marks tasks left running by the old process as interrupted. On resume or compaction, lists unfinished tasks | Context, only when there is something unfinished |
| `UserPromptSubmit` | Records the prompt, or adds it to the running task when typed mid-turn; drops the previous prompt if it was cancelled before any reply; handles `++`; closes subagents on task notifications | Blocks `++` prompts only |
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
| Copying full conversations | 0 |
| Queueing with `++`, the dashboard or the CLI | 0 |
| Dashboard, search, CLI and status line | 0 |
| Context reminder after resume or compaction | A few lines, only when tasks are unfinished |
| Dead ends reminder at session start | A few lines, only in repositories with failed attempts |
| History sync | One model call per batch of up to 40 tasks, only when you press Sync |
| Smart search | One Haiku call (2–6¢), only when you press Smart search |
| Architecture scan and view | 0 |
| Map areas | One model call (Sonnet about 10–20¢ at effort medium), only when you press Map areas |
| Draw with Archify | One headless agent session (Sonnet about $3–7), only when you press it |
| `tasky` MCP tools | Their definitions in each session's context; results only when called |
| Auto-pull | One normal turn per pulled task |
| Run now and the run queue | One normal headless session per task |
| Parallel with context | One headless session per task, starting with the cloned conversation as input |

## Data and privacy

Everything stays on your machine, in one SQLite database readable only by your user:

```text
$TASKY_HOME/tasky.db   (default: $XDG_DATA_HOME/tasky or ~/.local/share/tasky)
```

Tasky stores prompt text, subagent prompts and final assistant messages (results are capped at
8000 characters, and dashboard access links in them are redacted), and a copy of every message of
your sessions: text, tool calls and tool results (capped at 8000, 1000 and 4000 characters).
Before a message is stored, strings that look like secrets are replaced with `[redacted]`: private
keys, provider API keys and tokens (Anthropic, OpenAI-style, GitHub, Slack, AWS, Google), JWTs,
bearer tokens and `password=`/`api_key:`-style assignments. That filter is pattern-based; a secret
in another shape is stored as typed. Token counts per API message, recaps and messages typed
mid-turn are stored too. Deleting a task in the dashboard hides it; the row stays so the history
sync can still use it. An architecture scan stores each spec's path, title, status, a
300-character summary and its requirement names, and only reads the repository; Map areas sends
the folder tree (names and file counts, no file contents), those spec lines and the History topics
to the model through `claude -p`. It never modifies your Claude Code settings or transcripts. Delete the
directory to erase everything.

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
| `TASKY_HISTORY_MODEL` | `sonnet` | Default model for history syncs |
| `TASKY_HISTORY_MAX_BATCHES` | `8` | Batches one sync reads before stopping; press again for more |
| `TASKY_DEAD_END_ITEMS` | `5` | Failed attempts added at session start; `0` turns it off |
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
- History is only as good as what Tasky recorded: the prompt and the first `TASKY_MAX_RESULT`
  characters of each reply, not the tools a session ran. The model can misread a task; every record
  cites its tasks so it can be checked.

## Development

```sh
pytest -q
ruff check .
claude --plugin-dir . -p "hello"          # try the plugin without installing it
TASKY_HOME=$(mktemp -d) ./bin/tasky serve  # dashboard against an empty database
```

The behaviour contract lives in `openspec/specs/`; the design and history of v0.1.0 are in `openspec/changes/archive/2026-09-15-add-tasky-v1/`.

## Credits

Tasky is built on, borrows from, or works alongside these projects:

| Project | What Tasky owes it | License |
| --- | --- | --- |
| [Claude Code](https://github.com/anthropics/claude-code) | Hooks, transcripts, `claude -p`, plugins and MCP: everything Tasky records and runs goes through it | Anthropic terms |
| [attention-span](https://github.com/alexgreensh/attention-span) | The reply shape behind the Focus Cards output style and the result cards (inspiration only; Focus Cards text is original) | AGPL-3.0 |
| [MemPalace](https://github.com/MemPalace/mempalace) | Ideas for the project history: facts with validity windows, repository-then-topic organisation, verbatim evidence, memory the agent can write | MIT |
| [OpenSpec](https://github.com/Fission-AI/OpenSpec) | The living-spec format this repository's `openspec/` follows | MIT |
| [SQLite](https://sqlite.org) and FTS5 | The ledger and its accent-insensitive full-text search | Public domain |

No code from these projects is included in Tasky.

## License

[MIT](LICENSE) © 2026 Tasky contributors. You may use, copy, modify and distribute Tasky, including
commercially, as long as the copyright notice and the license text come with it.
