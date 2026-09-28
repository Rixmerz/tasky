## 1. Pure logic module

- [x] 1.1 `tasky/web/notices.js`: `isSessionStale`/`staleSessions`, `ranVersionLabel`/`staleVersionsKey`,
  `hooksNoticeVisible`, `pickLatestRecaps`, `resumeCommand`; verify with `tests/web/notices.test.mjs`
  covering staleness, the dismissal key's version-not-session semantics, recap picking (limit,
  ended/unknown sessions, project filter, fallback), and resume-command safety on an unsafe id/cwd

## 2. Wire the banner and badges

- [x] 2.1 `app.js`: serve `/notices.js` from `server.py`'s static allowlist; `renderHooksBanner` reads/writes
  the dismissal key from `localStorage`, and each listed session gets a "Copy resume command" and
  "Open" action; `renderSessionLine` reuses `resumeCommand` instead of its own inline copy
- [x] 2.2 `app.css`/`app.js`: the Done-group and recap-card "older tasky" badge becomes a quiet dot
  (`title` + `aria-label`, no visible text) instead of repeating the sentence

## 3. Recap dedupe

- [x] 3.1 `app.js`: `latestRecaps()` delegates to `pickLatestRecaps`; `renderDoneGroups` computes the top
  panel's recap ids once and threads them through `doneGroupItems` so a Done group skips a recap
  already shown at the top, while still showing that session's older recaps

## 4. Verification

- [x] 4.1 `ruff check .`, `pytest -q`, `node --test tests/web/notices.test.mjs tests/web/digest.test.mjs`
  all green
- [x] 4.2 Visual check against a read-only copy of the real database: banner with actionable rows,
  quiet dots on Done/recap cards, no duplicate recap in Done
