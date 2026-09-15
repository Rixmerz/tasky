# focus-output-style Specification

## Purpose
Tasky bundles an ADHD-friendly output style whose replies lead with the answer and keep one idea per block, so they read well in chat and render as cards on the dashboard.

## Requirements

### Requirement: Bundled ADHD-friendly output style
The plugin SHALL ship an output style that any user can select from Claude Code's output style
picker. It SHALL instruct the model to put the answer in the first sentence, keep one idea per
blank-line-separated block, open each point with a bold lead-in that carries its meaning, number
ordered steps, keep warnings next to the point they guard, and put a blocking question last. It SHALL
keep Claude Code's coding instructions. Tasky SHALL NOT select or force the style.

#### Scenario: Style appears in the picker
- **WHEN** the plugin is installed and the user opens the output style picker
- **THEN** a style named `tasky:Focus Cards` is listed with a description

#### Scenario: Style not forced
- **WHEN** the plugin is installed and the user has another output style selected
- **THEN** that selection is unchanged

### Requirement: Replies render as cards
A reply written in the bundled style SHALL be parsed by the dashboard digest into a headline and one
card per point without falling back to plain paragraphs.

#### Scenario: Style sample round-trip
- **WHEN** the example reply shipped with the style's tests is rendered by the digest
- **THEN** it produces a headline, at least two point cards, and no unparsed bold lead-in paragraphs

### Requirement: Original licensing
The style text SHALL be original work distributed under the project license, and the README SHALL
credit the project that inspired its reply shape without copying its text.

#### Scenario: License audit
- **WHEN** the style file is compared with the attention-span style text
- **THEN** no sentence is shared between them
