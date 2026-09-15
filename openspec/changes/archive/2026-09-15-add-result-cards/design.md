## Context

Results arrive in `tasks.result` as free text from hooks (`last_assistant_message`), workers and the
importer. Replies written with an ADHD-friendly style share a shape: an opening answer sentence, then
blank-line-separated paragraphs that open with a bold lead-in such as `**→ Lead.** rest`, numbered
`**1 →** …` steps, `- ` lists, `| … |` tables, fenced code, and a blocking question last. Other styles,
subagent reports and truncated results (`…[truncated]` may cut a fence) land in the same field.

Claude Code loads `output-styles/*.md` from a plugin and names each style `<plugin>:<name>`; a plugin
style only appears in the picker (`force-for-plugin` is internal and not used). The dashboard runs
under `script-src 'self'` with no external assets, and inserts user-controlled text only as text.

attention-span, the style the author uses, is AGPL-3.0; Tasky is MIT. Its prose cannot be copied.

## Goals / Non-Goals

**Goals:** a result a person can read at a glance; a one-line summary and a waiting-for-you marker on
collapsed cards; an ADHD-friendly style any Tasky user can pick; no token in stored results.

**Non-Goals:** full CommonMark; rendering images or HTML; changing the user's selected style;
classifying results with a model.

## Decisions

1. **Original style, distinct name.** `output-styles/focus-cards.md`, frontmatter `name: Focus Cards`,
   `keep-coding-instructions: true`, English, written from scratch. It appears as
   `tasky:Focus Cards`, so it never collides with a user style. README credits attention-span.
2. **Renderer is style-independent.** A tolerant markdown-lite parser produces a digest; recognised
   card markers improve the result but are never required.
3. **Parser and renderer in their own module.** `tasky/web/digest.js`, an ES module imported by
   `app.js` and served at `/digest.js`. Pure parsing is separated from DOM building so it is testable
   with `node --test` without a browser.
4. **Redaction at the store.** `Store.create_task` and `Store.update_task` redact `#token=<value>`
   before truncating, so every writer is covered; the importer's own redaction is removed.

## Digest contract (`tasky/web/digest.js`)

```js
/** @typedef {{type:"text",text:string}|{type:"strong",children:Inline[]}|{type:"code",text:string}|{type:"link",text:string,href:string}} Inline */
/** @typedef {{type:"paragraph",inline:Inline[]}|{type:"heading",level:number,inline:Inline[]}|{type:"list",ordered:boolean,items:Inline[][]}|{type:"table",header:Inline[][],rows:Inline[][][]}|{type:"code",lang:string,text:string}} Block */
/** @typedef {{tone:"point"|"step"|"note"|"question"|"extra",step:number|null,title:Inline[]|null,body:Block[]}} Card */
/** @typedef {{headline:Inline[]|null,cards:Card[],waiting:boolean}} Digest */
export function parseDigest(text) {}            // -> Digest; never throws
export function summarize(text, limit = 160) {} // -> {summary: string, waiting: boolean}; plain text
export function renderDigest(digest, doc) {}    // -> Element built with createElement/createTextNode only
```

Block rules: blank lines separate blocks; a line starting with three backticks opens a fence closed by
the next such line or the end of text; consecutive `|` lines form a table (a separator row of dashes
is skipped; ragged rows are padded); consecutive `- `, `* ` or `N. ` lines form a list; `#`–`####`
lines are headings; everything else joins into a paragraph.

Card rules, applied in order:
- The first block, when it is a paragraph that does not open with a bold lead-in, is the headline.
- A paragraph that opens with a bold span starts a new card. The lead-in text is the bold span with a
  leading `→`, `N →` or `N.` marker removed and trailing `:` or `.` kept. `**N →**` gives
  `tone:"step", step:N`; `**Also found:**` gives `tone:"extra"`; anything else `tone:"point"`.
- Other blocks join the current card; before any card they form a `tone:"note"` card with no title.
- If the last block is a paragraph whose text ends with `?` (ignoring one trailing parenthetical group
  and trailing whitespace), or whose bold lead-in ends with `?`, it becomes its own `tone:"question"`
  card (or keeps its card with that tone when it opened one), and `waiting` is true.

Inline rules: `**…**` strong (may nest code), `` `…` `` code, `[text](url)` a link only when the URL
starts with `http://` or `https://`, otherwise plain text; unmatched markers stay literal.

`summarize` returns the headline's plain text (or the first card's title and body text when there is
no headline), whitespace-collapsed and ellipsized to `limit` characters.

## Dashboard integration

- Collapsed card: when `task.result` is non-empty, a `.result-summary` line with `summarize(...).summary`
  under the title, one line, ellipsized by CSS; a "Waiting for you" marker when `waiting` is true.
- Expanded card: the result disclosure shows `renderDigest(parseDigest(result))` and a "Show original"
  toggle revealing the existing raw `<pre>`.
- Digests are cached per task id and result string so the 2-second refresh does not rebuild them.
- Styling follows the design brief tokens; question cards use the attention colour, extra cards start
  collapsed, code blocks and tables scroll horizontally inside their card.

## Risks / Trade-offs

- **Heuristics misfire** on replies that start with bold text for other reasons: they become cards,
  which is still readable, and the original text is one click away.
- **Question detection** only looks at the last block: a question asked earlier is not flagged. It is
  the case the style guarantees.
- **Existing installs** keep 0.1.0 until the user updates the plugin; the `~/.local/bin/tasky` link
  from the README points into a versioned directory and must be recreated.
