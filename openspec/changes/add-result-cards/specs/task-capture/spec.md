## ADDED Requirements

### Requirement: Results never store the dashboard token
Whenever a result is stored, from hooks, workers or import, every `#token=<value>` occurrence SHALL be
replaced with `#token=<redacted>`.

#### Scenario: Agent prints the access link
- **WHEN** a turn stops with a final message containing `http://127.0.0.1:7733/#token=abcDEF123_-xyz`
- **THEN** the stored result contains `#token=<redacted>` and not the token value
