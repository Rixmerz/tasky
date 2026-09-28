## Why

Tasky already shows tokens per task and per Done-column group, and 0.16 added a model router
that can start a task on Opus, Sonnet, Haiku or Fable. Nothing ties the two together: a user who
turns the router on has no way to see whether it is actually spending where they expected, or to
notice a cheap Haiku day turning into an expensive Opus one until the bill arrives. They need a
glance-able answer to "where did today's tokens go, by model" so they can switch a model off for
the rest of the day.

## What Changes

- `Store.tokens_by_model()`: output/input/cache token totals bucketed by model family
  (`fable`/`opus`/`sonnet`/`haiku`/`other`), for today and the trailing 7 days, in local time. The
  family comes from the real model each assistant message recorded (`usage.model`, already
  captured by the existing transcript ingest since 0.8.0's re-read) — not the router's stored
  choice — so interactive turns the router never touched are counted too.
- `Store.state()` gains a `tokens_by_model` key, so the dashboard's existing poll carries it with
  no new endpoint.
- The dashboard's settings popover, next to the model router controls, shows a compact line —
  "Today: Opus 301k · Sonnet 8k · Haiku 1k out" — with the last 7 days on hover; the line is
  omitted entirely when there is no usage today.

## Capabilities

### New Capabilities

- `token-spend`: aggregating recorded token usage by model family over today and the trailing 7
  days, and showing it on the dashboard.

### Modified Capabilities

None. `Store.state()` gains one additive key; no existing field changes shape.

## Non-goals

- Per-project or per-task model breakdowns (the existing per-task/per-group token figures already
  cover that axis).
- Cost in dollars — Tasky does not track per-model pricing.
- A history or trend chart beyond today/7-day totals.

## Impact

- Edits to `tasky/store.py` (aggregation), `tasky/web/index.html` and `tasky/web/app.js`
  (rendering). No schema change: the model has been recorded on every `usage` row since the 0.8.0
  migration re-read the transcripts; no new migration and no `_SCHEMA_VERSION` bump.
