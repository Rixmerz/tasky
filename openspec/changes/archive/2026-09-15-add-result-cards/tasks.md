## 1. Style

- [x] 1.1 `output-styles/focus-cards.md`, original English text with the reply shape from design.md
- [x] 1.2 Synthetic sample reply in that style at `tests/web/fixtures/focus-cards-sample.md`
- [x] 1.3 License audit: no sentence shared with the attention-span style text

## 2. Backend

- [x] 2.1 Store-level `#token=` redaction in `create_task` and `update_task`, importer helper removed, tests
- [x] 2.2 `/digest.js` static route in `tasky/server.py`, test
- [x] 2.3 `tests/test_web_digest.py` running `node --test tests/web/` (skipped without node)

## 3. Dashboard

- [x] 3.1 `tasky/web/digest.js` parser, summary and renderer per the contract, with `tests/web/digest.test.mjs` covering every scenario
- [x] 3.2 `app.js` integration: collapsed summary, waiting marker, digest in the result disclosure, show-original toggle, caching
- [x] 3.3 `app.css` styles for headline, cards, steps, question, extra, code and tables at 400px and 1280px

## 4. Verification and delivery

- [x] 4.1 Full test suite, `ruff check .`, `node --check`, `claude plugin validate .`
- [x] 4.2 Headless Claude Code run with `outputStyle` set to `tasky:Focus Cards` produces a result the digest renders as cards
- [x] 4.3 Dashboard measured in a real browser with real and synthetic results
- [x] 4.4 Version 0.2.0 in `plugin.json`, `pyproject.toml`, `tasky/__init__.py`; README and CHANGELOG updated
- [x] 4.5 Committed, pushed, installed plugin updated
