## 1. Aggregation

- [x] 1.1 `Store.tokens_by_model()`: bucket `usage.model` into `fable`/`opus`/`sonnet`/`haiku`/
      `other`, sum input/output/cache tokens for today and the trailing 7 days in local time;
      verify tests for family mapping (all four families, mixed case, older model-id shapes,
      `None`, `<synthetic>`), the local-day boundary and the 7-day boundary, and rows with no
      timestamp
- [x] 1.2 `Store.state()`: add the `tokens_by_model` key; verify existing state tests still pass

## 2. Dashboard

- [x] 2.1 `index.html`/`app.js`: a hidden-by-default line in the settings popover next to the
      model router controls, rendered from `state.tokens_by_model` on every `renderAll()`, reusing
      `formatCount`/`modelLabel`; today's totals on the line, the trailing 7 days in the tooltip;
      verify by reading a backup of the real database and checking the numbers match
      `tokens_by_model()`'s own output

## 3. Verification

- [x] 3.1 `pytest -q`, `ruff check .`, `node --test tests/web/digest.test.mjs` green
- [x] 3.2 `openspec validate add-token-spend-by-model --strict` passes
