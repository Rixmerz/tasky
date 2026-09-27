## Context

See proposal.md for motivation. Every Tasky launch goes through `worker._launch`, which starts
`tasky supervise` detached; the supervisor is the process that finally runs `claude -p`. The CLI
accepts `--model <alias>` and `--effort <level>`. Tasky is dependency-free Python; the classifier
lives in another plugin (muscle-memory's `mm-classifier`, Node) and its model half needs a local
Laya server.

## Goals / Non-Goals

**Goals:** route at launch, fail to today's behaviour, let the user forbid models, show what was
decided.

**Non-Goals:** subagent routing, per-task override, starting Laya — see proposal.md.

## Decisions

**Route in the supervisor, not in `_launch`.** `_launch` runs inside `/api/tasks/<id>/run` and
the scheduler tick; a classifier call there (~1 s warm, up to the 10 s timeout) would stall the
dashboard. The supervisor is already detached, so the only cost is a later `claude` start. The
supervisor appends the flags to the argv it was given and writes the decision to the task.

**Shell out to `mm-classifier`, do not reimplement it.** The definitions are ordinary
`mm-classifier` files, so they can be tested with `mm-classifier test` and edited with its lint.
Resolution: `TASKY_MM_CLASSIFIER`, else the newest
`~/.claude/plugins/cache/*/muscle-memory/*/bin/mm-classifier.mjs`; `node` from `PATH`. A rules
hit needs no server; only the model half returns exit 3 when Laya is down, which routes to
`default`.

**Coarse question plus chains.** The routing table has 14 kinds, past what one Laya question
handles (~10). Root `task-kind` asks one of `plan`, `debug`, `build`, `review`, `test`, `docs`,
`explore`, `chat`, `frontier`, `other`; `debug`, `build` and `review` chain to narrow questions.
Keyword rules (Spanish and English) come first in each. Real prompts from the user's Tasky
database are the test examples, so `mm-classifier test` reports the rule and Laya halves.

**Fable only by tag.** The guidance reserves Fable for measured frontier work, which no text
reveals. A `#fable` tag in the task is the only way in; a model-chosen `frontier` is dropped.

**Omit what is already the default.** `high` is the CLI default, so it is not passed; Haiku takes
no effort. A launch the router cannot improve stays byte-identical, which is also why every
existing worker test keeps passing unchanged.

**Settings in `meta`.** `meta` is `key -> INTEGER`, enough for booleans (`router_enabled`,
`router_model_<name>`); a missing model key means allowed. Server-side because the scheduler and
CLI launch tasks with no browser open.

## Risks / Trade-offs

- [Zero-shot Laya is weak on custom schemas (~50% on 10 classes, per its vendor)] → rules carry
  most decisions; low confidence falls to the group's safe default (`implement`, `debug-repro`,
  `review-routine`) or to `default`.
- [Opus `xhigh` costs far more than the session default] → router off by default, per-model
  switches, and the chosen model shown on every task.
- [A plugin update moves `mm-classifier.mjs`] → resolved at every launch, not cached.
- [The worker's first turn starts ~1 s later] → accepted; nothing waits on it.

**Rules only, measured.** On 25 real prompts from the user's Tasky database, Laya's zero-shot
answer to the root question matched the intended kind 3 times (12%, below chance on 10 labels),
often at 0.8–0.9 confidence; its `frontier` label drew answers regardless of the text. Keyword
rules written from the same 25 prompts, plus a final catch-all rule to `other`, picked the
intended kind for 18 of the 24 held-out prompts (75%), with no Laya call and no server. The Laya
questions stay in the definitions, unreachable, for when a trained head replaces the catch-all.

**Verified end to end** with a stand-in `claude` binary that records its arguments: `plan` →
`--model opus --effort xhigh`; `#fable` with Fable switched off → `--model opus --effort max`;
`explore` → `--model haiku`; an unrecognised task → no flags. `/run` still answered in 3–5 ms.

## Migration Plan

Three nullable columns added through the existing `PRAGMA table_info` migration. Rollback: turn
the router off; the columns are ignored.
