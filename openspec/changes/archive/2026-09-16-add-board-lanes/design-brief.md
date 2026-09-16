> **Superseded on 2026-09-15.** The tinted-card board this brief describes was built, reviewed
> with the user and rejected as noisy. The shipped design is a shadcn-style row list written in
> plain CSS: one line per task, zinc neutrals, one accent, one warning colour, 3px colour rails
> instead of tinted fills, and a single "to" picker in the create bar. The token table and the
> membership rules below still hold; the card anatomy and palette do not.

# Tasky dashboard — design brief v3 (board)

**Revises an existing system.** Read: `tasky/web/app.css` (`:root` token block, lines 1–48),
`scratchpad/design-brief.md` (v1, rejected for layout and look), `README.md`,
`openspec/changes/add-result-cards/design.md` ("Dashboard integration"), `tasky/server.py:265`
(`allow_bypass` in `/api/state`). Visual pass: read `vis/open-1280.png` and `vis/shot-400.png`;
`vis/tok-1280.png` does not exist, so only two screenshots were reviewed. What they show: one
vertical stack, 1px borders on every card and control, 4–8px radii, three stacked `↑ ↓ Run` boxes per
card — the "cuadrado" the user named. Token names stay; values below replace v1 where they differ.
Subject: a glance board for someone with ADHD who hands work to Claude Code. One job: see the
queue's order and what is running in parallel, and schedule a new idea in one move.

## 1. Tokens — the only hexes allowed (contrast computed, `scratchpad/contrast3.py`)

| Token | Role | Light | Dark |
|---|---|---|---|
| `--ground` | page, top bar | `#EEF0F3` | `#101317` |
| `--well` | column background (sunken) | `#E4E8EC` | `#171B21` |
| `--surface` | cards, prompt pill, access card | `#FCFDFD` | `#1F242B` |
| `--raised` | hovered/lifted card, menus | `#FFFFFF` | `#282E36` |
| `--ink` | titles, body | `#1A1E23` | `#E6E9ED` |
| `--muted` | meta, positions, done titles, control outlines | `#525C67` | `#9BA5B0` |
| `--line` | decorative hairlines only (1.3:1, never a control edge) | `#CDD3DA` | `#2E353E` |
| `--live` | the one accent: running, `++`, focus, drop line, primary fill | `#0A6E7A` | `#4FC3CF` |
| `--attn` | interrupted, waiting for you | `#9A5200` | `#E8A850` |
| `--fail` | failed, destructive, bypass mode | `#B3261E` | `#F2837A` |

Text contrast, well / surface (lowest of ground/raised is ≥ 4.8). Light: ink 13.6/16.4 · muted 5.5/6.7 ·
live 4.8/5.8 · attn 4.8/5.8 · fail 5.3/6.4. Dark: ink 14.2/12.8 · muted 6.9/6.2 · live 8.3/7.5 · attn 8.4/7.5 · fail 6.8/6.2.
`--surface` text on `--live` fill 5.8 / 7.5; on `--fail` fill 6.4 / 6.2. Attention tint
`color-mix(in srgb, var(--attn) 12%, var(--surface))`: ink 13.8 / 10.2, attn 4.8 / 6.0. Drop-target
tint `color-mix(in srgb, var(--live) 10%, var(--well))`: muted 4.8 / 5.8.
**Boundaries ≥ 3:1:** inputs, selects, button groups use a 1px `--muted` outline (6.7 / 6.2 on surface)
or a `--live` fill; focus ring `--live` (5.8 / 7.5). Cards and wells are not controls and carry no edge:
they separate by lightness step and elevation. **No `opacity` on text anywhere** (a 75% faded muted
drops to 3.3:1); "fading back" is done by elevation and weight, §5. Define on `:root`, override in
`@media (prefers-color-scheme: dark)`. Only derived colours: the tints above and shadow `color-mix`es.

## 2. Type — system stacks (v1 `--font-sans` / `--font-mono`, unchanged)

| Step | Size | Weight | Line-height | Use |
|---|---|---|---|---|
| `--t-xs` | 0.75rem | 500 | 1.35 | column counts, chips, cost hint, segment labels |
| `--t-sm` | 0.8125rem | 500 | 1.4 | meta line, card buttons, section labels (Queue runner / Parallel) |
| `--t-md` | 0.9375rem | 600 | 1.35 | card titles, 2-line clamp collapsed |
| `--t-body` | 1rem | 400 | 1.55 | `++` input, expanded body, digest cards (`max-inline-size: 65ch`) |
| `--t-lg` | 1.125rem | 650 | 1.3 | column headings, digest headline |
| `--t-xl` | 1.25rem | 700 | 1.2 | app name; counters (mono, `tabular-nums`) |

Mono for: `++`, positions `1 2 3`, project chips, elapsed time, counters, code in digests.

## 3. Space, radii, elevation

Spacing `--s-1..7` = 4, 8, 12, 16, 20, 24, 32px (as rem). Card padding `--s-3`; gap between cards
`--s-2`; column inner padding `--s-2`; gap between columns `--s-4`; page gutter `--s-5`.
Radii: `--r-1` 6px (chips, drop line ends), `--r-2` 10px (buttons, selects, button groups),
`--r-3` 14px (cards), `--r-4` 20px (column wells, access card), `--r-pill` 999px (`++` pill, segments).
Elevation — shadow in light, lightness step plus shadow in dark (shadows vanish on `#101317`):

| Level | Used by | Light | Dark |
|---|---|---|---|
| 0 | ground, wells | none | none |
| 1 | resting card | `--surface`, `0 1px 2px mix(ink 7%), 0 1px 1px mix(ink 4%)` | `--surface`, `inset 0 1px 0 mix(ink 6%)` |
| 2 | hovered/focused card, sticky top bar once scrolled | `--raised`, `0 4px 14px mix(ink 10%), 0 1px 3px mix(ink 6%)` | `--raised`, `0 4px 14px mix(ground 70%)` |
| 3 | lifted card while dragging, select menus | `--raised`, `0 14px 32px mix(ink 16%), 0 3px 8px mix(ink 8%)` | `--raised`, `0 14px 32px mix(ground 85%)` |

(`mix(x N%)` = `color-mix(in srgb, var(--x) N%, transparent)`.) Hit targets: 32px desktop, 44px at ≤ 480.

## 4. Layout

Five columns fill the viewport under a one-row top bar; each column scrolls on its own, the page never
scrolls vertically at ≥ 1024. **Running is one column with two stacked sections**, not two columns:
1440 − 2×24 gutter − 4×16 gap − 12 extra seam = 1316 / 5 = **263px** per column (title ≈ 30ch per line);
six columns would be 214px and clamp most titles to 3+ lines. "Queue runner" holds at most one card per
project, so its own column would stand mostly empty. The queued-vs-running separation the user asked
for is the **seam** between Up next and Running: that gap is 28px (not 16) with a centred 1px `--line`
vertical hairline and the Running well gets no other decoration.

```
1440px
Tasky ▶3 ⏸4 ⚠1 ✓12 ( ++ what should Claude do next…  [storefront▾][no session▾] Add⏎ ) [All projects▾] [Mode: acceptEdits▾]
╭ Inbox      2 ╮ ╭ Up next    4 ╮ ┊ ╭ Running    3 ╮ ╭ Needs attn 1 ╮ ╭ Done      12 ╮  sticky headers
│╭────────────╮│ │╭────────────╮│ ┊ │ QUEUE RUNNER │ │╭────────────╮│ │ ✓ Map tax    │  done: level 0
││⠿ Rename    ││ ││1 Write the  ││ ┊ │╭────────────╮│ ││✕ Fix flaky  ││ │   Found 3 e… │
││  blog · 2h ││ ││⠿ migration  ││ ┊ ││▶ Refactor   ││ ││  Failed · 3m││ │ ✓ Update deps│
││[Run│Aft│Par]││ ││[Run│Aft│Par]││ ┊ ││  12m        ││ ││[Run again]  ││ │ Show 8 older │
│╰────────────╯│ │╰────────────╯│ ┊ │╰────────────╯│ │╰────────────╯│ │              │
│              │ │ ━━━━━━━━━━━━ │ ┊ │ PARALLEL     │ │              │ │              │  ━ drop line
│              │ │╭────────────╮│ ┊ │╭────────────╮│ │              │ │              │
│              │ ││2 Update log ││ ┊ ││▶ fork of #12││ │              │ │              │
╰──────────────╯ ╰──────────────╯ ┊ ╰──────────────╯ ╰──────────────╯ ╰──────────────╯  ┊ = 28px seam
```
**1024px:** top bar wraps to two rows (row 1: name, counters, filter, mode; row 2: the `++` pill full
width). Columns `flex: 0 0 16rem`, row scrolls horizontally with `scroll-snap-type: x mandatory`,
`scroll-snap-align: start`, `scroll-padding-inline: 24px`; ≈ 3.6 columns visible so the cut edge shows
there is more. Column order never changes.
```
400px ─────────────────────────────
│ Tasky ▶3 ⏸4 ⚠1 ✓12     [⚠ bypass]│ row 1: name, counters; bypass chip only when active
│( ++ │what next…          ▾  Add )│ row 2: pill; ▾ reveals project, session, filter, mode
│(Inbox│ Next │Running│Needs│Done )│ sticky segmented control, 48px
│ ╭─────────────────────────────╮ │
│ │1 ⠿ Write the migration      │ │
│ │  storefront · 3m            │ │
│ │[Run now│After last│Parallel]│ │
│ ╰─────────────────────────────╯ │
```
At ≤ 480 filter and mode move into the pill's `▾` disclosure, so DOM order still matches visual order.
Segmented control: `role="tablist"`, each segment count (`--t-sm` mono) over label (`--t-xs`: Inbox, Next, Running, Needs, Done — short
because a segment is 73px; the accessible name is the full column name); Needs attention count in `--attn` when > 0. First load shows Running; the choice persists in `sessionStorage`.
Counters in the top bar (every width) are buttons that scroll the column into view (or select the
segment) and move focus to its heading.

## 5. Card anatomy — `<article>` with a native `<details>`; buttons live outside `<summary>`

Collapsed, all columns: **slot** (2rem, inline-start) · title `--t-md` 2-line clamp · meta `--t-sm --muted`
(project chip · kind if not `prompt` · relative time) · one-line `.result-summary` when a result exists
· `? Waiting for you` in `--attn` when the digest ends in a question. Expanded: body, result digest
(`renderDigest`, headline `--t-lg`, digest cards are level-0 `--well` blocks with `--r-2`, no border;
question card uses the attention tint), "Show original", children list, session line, secondary actions.

| Column | Slot | Primary actions (collapsed) | Expanded adds |
|---|---|---|---|
| Inbox | `⠿` handle | `Run now │ After last │ Parallel` | Edit text, Delete |
| Up next | position `1` (mono `--muted`) with `⠿` under it; the slot is the handle | same three | Move up, Move down, Back to Inbox, Delete |
| Running · Queue runner | `▶` in `--live` | none; elapsed time in `--live` mono | Mark done, Cancel, Copy resume command |
| Running · Parallel | `▶` + kind chip `worker` / `fork of #12` / `session` | none | same as above |
| Needs attention | `⚠`/`✕` + word "Interrupted"/"Failed"; card on attention tint | `Run again`, `Back to Inbox` | Mark done, Delete |
| Done | `✓` `--muted` | none | Delete; result digest open by default |

**The three actions** are one joined button group: `--r-2`, 1px `--muted` outline and `--muted` dividers (they separate controls, so ≥ 3:1),
labels `--t-sm` `--ink`, equal thirds, 32px (44px at ≤ 480). `Run now` label in `--live`. Accessible
names carry the full sentence ("Run now in a new background worker", "Run after the last task in Up
next", "Run in parallel with this session's context"). Under the group, one `--t-xs --muted` line:
"Parallel replays the session's context" — shown on `:hover`/`:focus-within`, always under
`@media (hover: none)` and in expanded cards; always wired via `aria-describedby`. **Parallel disabled:**
`aria-disabled="true"` (stays focusable), label `--muted`, and that line reads "No session in
storefront to clone". `After last` on the last Up next card: disabled, "Already last".
**Done fades back** by being the only column whose cards sit at level 0 (no surface, no shadow) with
titles in `--muted` weight 500; hover lifts to level 2. Newest first, capped at 20, then a text button
"Show 8 older". A Done card waiting for you keeps level 1 and pins to the top.

## 6. Top bar — sticky, `--ground`, level 2 only after scroll

Order (= DOM = tab order): `Tasky` (`--t-xl`) · counters `▶3 ⏸4 ⚠1 ✓12` (mono; `⚠` in `--attn` when > 0,
each with a visually hidden word) · **`++` pill** (§9) · project filter `<select>` "All projects" ·
**Mode** `<select>` labelled "Run mode", default `acceptEdits`, applies to Run now, After last,
Parallel, Run again. Options with a hint line under the select: `acceptEdits — edits allowed, other
tools refused` · `default — gated tools refused` · `plan — read-only` · `bypassPermissions — everything,
no asking`. When `allow_bypass` is false that option is `disabled` and reads "bypassPermissions (set
TASKY_ALLOW_BYPASS=1)". When selected: select outline 2px `--fail`, and a chip beside it `⚠ No
permission checks` in `--fail` on fail-tint — visible from every column because the bar is sticky.

## 7. Drag and drop — pointer events, not native DnD (its ghost cannot be styled and ignores touch)

| State | Look |
|---|---|
| idle | `⠿` in `--muted` inside the slot; `cursor: grab` on the slot only, so text stays selectable |
| handle hover/focus | glyph `--ink`, card to level 2 |
| lifted | card follows pointer at level 3, `scale(1.02)`, `cursor: grabbing`; its origin becomes a sunken slot of equal height, `color-mix(in srgb, var(--ink) 6%, var(--well))`, `--r-3`, no border |
| over valid column | well takes the drop-target tint; valid targets are Inbox and Up next only |
| drop position | 3px `--live` bar, `--r-1` ends, centred in the gap between cards; positions renumber live |
| invalid | no tint, no bar; release returns the card to origin (160ms transform) |
| dropped | card settles (160ms), `aria-live` polite: "Write the migration moved to position 2 of 4 in Up next" |

Keyboard: the slot is a `<button>` "Reorder Write the migration, position 2 of 4". `Space` picks up
(announced), `↑`/`↓` move, `←`/`→` switch between Inbox and Up next, `Space`/`Enter` drops, `Esc` cancels.
Move up / Move down buttons in expanded cards do the same without a mode. At ≤ 480 dragging works
within the visible column only; moving across columns is `After last` / `Back to Inbox`.
Refresh every 2s never re-sorts a card that is lifted, focused or open.

## 8. States

Focus: `outline: 2px solid var(--live); outline-offset: 2px` on `:focus-visible`; cards show it on the
article when its summary is focused. Hover (pointer only): card to level 2; button segment fill
`color-mix(in srgb, var(--live) 10%, var(--surface))`.
Empty (one `--muted` line centred in a 5rem soft area of the well, no illustration):
Inbox "Type after `++` above. Ideas wait here until you schedule them." · Up next "Nothing queued. Drag a
card here or press After last." (this area is the drop target) · Queue runner "Idle. Up next's first
card runs here." · Parallel "Nothing in parallel. Run now or Parallel starts work here." · Needs attention
"Nothing needs you." · Done "Finished work and its results land here." · Filter empties the board:
"No tasks in storefront." + text button "Show all projects".
Access screen: level-1 `--surface` card, `--r-4`, centred on `--ground`, max 32rem: heading "This tab
needs a fresh link", one sentence, `tasky ui` in mono with "Copy command" (→ "Copied", live region).

## 9. Motion

`transform` and `opacity` only (opacity never on text-bearing resting states), ≤ 180ms `ease-out`,
except the drag follow (unanimated). Allowed: card enter (fade + 4px rise), drop settle, level change
on hover (shadow cross-fade 120ms), renumber shift. One loop: running `▶` opacity 1 → 0.45, 1.6s.
`prefers-reduced-motion: reduce`: all off; lifted card does not scale; drop bar and tints still show.

## 10. Signature — the `++` pill

The task input is a prompt, not a form. A `--r-pill` `--surface` capsule at level 1 with a 1px `--muted`
outline; a mono `++` in `--live` at its start (the live `config.queue_prefix`, never hardcoded), the
input with no box of its own, project and session pickers as borderless inline chips, and `Add ⏎` as a
`--live` fill pill at the end. Focus: outline becomes a 2px `--live` ring. `Enter` adds to Inbox; the project chip defaults to the active filter;
`n` or `/` focuses it from anywhere, `Esc` blurs. It is the only decorated element; the board around it
stays quiet. Visually hidden label: "New task".

## 11. Second-pass audit — what changed and why

- **Kanban of rounded cards on system-ui is the SaaS template.** The user pinned kanban, so the look is
  not spent on the board: distinctiveness goes to the `++` pill and mono for operational text, and the
  accent stays teal, not the default violet/blue. I first reached for a brighter "lively" blue; kept teal
  because the rejection was about shape, and its contrast was already proven.
- **"Softer" first became bigger radii plus the same 1px borders.** That is still boxy. Changed to no
  card or column edges at all: lightness steps (`--well` under `--surface`) and elevation, with outlines
  kept only where a control needs 3:1.
- **Done fade was `opacity: .6`.** It fails contrast (3.3:1). Changed to level 0 + muted weight.
- **Running as two columns** was the obvious reading of "separación horizontal"; the width math and the
  mostly empty runner column rejected it. The seam carries the separation instead.
- **Three separate outlined buttons per card** would repeat v1's box stack. Joined into one group.
- **Removed:** a live-tinted rail on running cards (it was becoming a second signature).
