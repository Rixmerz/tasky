## 1. Foundation

- [x] 1.1 `tasky/config.py` with `Config.from_env` and `now_iso`, plus tests
- [x] 1.2 `tasky/prompts.py` classification, notification parsing, queue prefix and titles, plus tests
- [x] 1.3 `tasky/store.py` schema, revision triggers and the full `Store` contract, plus tests

## 2. Behaviour

- [x] 2.1 `tasky/hooks.py` event handling and fail-safe `main`, `bin/tasky-hook`, `hooks/hooks.json`, plus tests covering every event row
- [x] 2.2 `tasky/importer.py` idempotent transcript import, plus tests with synthetic transcripts
- [x] 2.3 `tasky/worker.py` detached headless runs with safety checks, plus tests with a fake process launcher
- [x] 2.4 `tasky/server.py` JSON API, static files and localhost protections, plus tests
- [x] 2.5 `tasky/web/` dashboard (`index.html`, `app.js`, `app.css`) with no external assets

## 3. Packaging

- [x] 3.1 `tasky/cli.py`, `tasky/__main__.py`, `bin/tasky`, plus tests
- [x] 3.2 `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `commands/ui.md`
- [x] 3.3 `README.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore` excluding `.vise/` and `.claude/`

## 4. Verification

- [x] 4.1 Full test suite and `ruff check .` green
- [x] 4.2 End-to-end run of the plugin in a headless Claude Code session with an isolated data directory
- [x] 4.3 Dashboard rendered and measured in a real browser at desktop and phone widths
- [x] 4.4 Adversarial code review and security audit findings resolved
- [x] 4.5 Repository audited for personal references and secrets before the first commit

## 5. Delivery

- [x] 5.1 GitHub repository `tasky` created, initial commit pushed
