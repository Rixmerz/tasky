## Purpose

Let a user see whether their token spend is landing on the models they expect, so they can turn
an expensive model off for the rest of the day before the bill reflects it.

## ADDED Requirements

### Requirement: Token usage SHALL be bucketed by the real model family of each message
The system SHALL bucket every recorded assistant message into a model family — `fable`, `opus`,
`sonnet`, `haiku` or `other` — using the model the transcript recorded for that specific message
(the real, per-message model an assistant reply ran on), never the model router's stored choice
for the task the message belongs to. A message the router never touched, including an interactive
turn or one from before the router existed, SHALL still be counted under its real model. A message
with no model recorded, or a synthetic entry with no real API call behind it, SHALL be counted
under `other` rather than dropped, so totals still add up to the sum of all recorded usage.

#### Scenario: An interactive turn the router never routed
- **WHEN** a session's own reply is recorded with `model: "claude-opus-5-5"` and no task of the
  router's ever chose Opus
- **THEN** that message's tokens are counted under the `opus` family

#### Scenario: A message with no model recorded
- **WHEN** a usage row has no model, or `model` is `"<synthetic>"`
- **THEN** its tokens are counted under `other`, not dropped

#### Scenario: An older or differently-cased model id
- **WHEN** a message's model is `"claude-3-5-haiku-20241022"` or `"CLAUDE-OPUS-5-5"`
- **THEN** it is bucketed under `haiku` and `opus` respectively

### Requirement: Spend SHALL be totalled for today and the trailing 7 days, in local time
The system SHALL compute, per model family, the sum of input, output, cache-read and
cache-creation tokens for two windows measured against the local calendar day: the current day
("today", from local midnight) and the trailing 7 days ("week", the last 6 full local days plus
today). A message with no timestamp SHALL be excluded from both windows.

#### Scenario: A message from just before local midnight is not today's
- **WHEN** a message's timestamp falls one second before the local start of the current day
- **THEN** it is excluded from the "today" total but SHALL still count toward "week" if it falls
  within the trailing 7 days

#### Scenario: A message from 8 local days ago is outside the week
- **WHEN** a message's timestamp falls before the local start of 6 days ago
- **THEN** it is excluded from the "week" total

### Requirement: The dashboard SHALL show today's spend by model without adding board noise
The dashboard SHALL show today's per-family output token totals, formatted with the board's
existing abbreviated count style, in the settings popover next to the model router controls, and
SHALL show nothing there when there is no usage recorded today. The trailing 7 days' totals SHALL
be available on the same element without occupying board space (for example, as its tooltip).

#### Scenario: A day with usage on two models
- **WHEN** today's usage has Opus and Sonnet output but no Haiku or Fable output
- **THEN** the settings popover shows a line naming Opus and Sonnet with their token counts, and
  says nothing about Haiku or Fable

#### Scenario: No usage yet today
- **WHEN** no message has been recorded since local midnight
- **THEN** the settings popover shows no spend line at all
