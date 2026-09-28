import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isSessionStale,
  staleSessions,
  ranVersionLabel,
  staleVersionsKey,
  hooksNoticeVisible,
  pickLatestRecaps,
  resumeCommand,
} from "../../tasky/web/notices.js";

function session(overrides = {}) {
  return {
    id: "abc123",
    state: "active",
    hook_version: "0.16.1",
    title: "Fix the thing",
    cwd: "/home/rixmerz/proj",
    ...overrides,
  };
}

// ---------- isSessionStale / staleSessions ----------

test("a session on the current version is not stale", () => {
  assert.equal(isSessionStale(session({ hook_version: "0.16.1" }), "0.16.1"), false);
});

test("an active session on an older version is stale", () => {
  assert.equal(isSessionStale(session({ hook_version: "0.16.0" }), "0.16.1"), true);
});

test("an ended session is never stale, even on an older version", () => {
  assert.equal(isSessionStale(session({ state: "ended", hook_version: "0.16.0" }), "0.16.1"), false);
});

test("a session with no hook_version recorded yet is stale (pre-dates the field)", () => {
  assert.equal(isSessionStale(session({ hook_version: null }), "0.16.1"), true);
});

test("nothing is stale before the server's own version has loaded", () => {
  assert.equal(isSessionStale(session({ hook_version: "0.16.0" }), null), false);
  assert.deepEqual(staleSessions([session({ hook_version: "0.16.0" })], null), []);
});

test("staleSessions keeps only the active, outdated ones, in order", () => {
  const current = session({ id: "s1", hook_version: "0.16.1" });
  const old1 = session({ id: "s2", hook_version: "0.16.0" });
  const ended = session({ id: "s3", state: "ended", hook_version: "0.15.0" });
  const old2 = session({ id: "s4", hook_version: null });
  assert.deepEqual(
    staleSessions([current, old1, ended, old2], "0.16.1").map((s) => s.id),
    ["s2", "s4"],
  );
});

// ---------- ranVersionLabel / staleVersionsKey ----------

test("ranVersionLabel names the version when the hooks recorded one", () => {
  assert.equal(ranVersionLabel(session({ hook_version: "0.16.0" }), "0.16.1"), "v0.16.0");
});

test("ranVersionLabel falls back to 'before <current>' when hook_version is missing", () => {
  assert.equal(ranVersionLabel(session({ hook_version: null }), "0.16.1"), "before 0.16.1");
});

test("staleVersionsKey is the same regardless of session order or how many share a version", () => {
  const a = session({ id: "a", hook_version: "0.16.0" });
  const b = session({ id: "b", hook_version: "0.16.0" });
  const c = session({ id: "c", hook_version: null });
  assert.equal(staleVersionsKey([a, b, c], "0.16.1"), staleVersionsKey([c, b, a], "0.16.1"));
});

test("staleVersionsKey changes when a genuinely new old version shows up", () => {
  const a = session({ id: "a", hook_version: "0.16.0" });
  const d = session({ id: "d", hook_version: "0.15.0" });
  assert.notEqual(staleVersionsKey([a], "0.16.1"), staleVersionsKey([a, d], "0.16.1"));
});

// ---------- hooksNoticeVisible ----------

test("nothing stale means no notice", () => {
  assert.equal(hooksNoticeVisible([], "0.16.1", ""), false);
});

test("a fresh set of stale sessions is visible when nothing was dismissed yet", () => {
  const stale = [session({ hook_version: "0.16.0" })];
  assert.equal(hooksNoticeVisible(stale, "0.16.1", ""), true);
});

test("dismissing hides the notice for the same set of old versions", () => {
  const stale = [session({ id: "a", hook_version: "0.16.0" }), session({ id: "b", hook_version: "0.16.0" })];
  const key = staleVersionsKey(stale, "0.16.1");
  assert.equal(hooksNoticeVisible(stale, "0.16.1", key), false);
});

test("restarting some of several sessions on an already-dismissed version keeps it dismissed", () => {
  const stale = [session({ id: "a", hook_version: "0.16.0" }), session({ id: "b", hook_version: "0.16.0" })];
  const dismissedKey = staleVersionsKey(stale, "0.16.1");
  // Session "a" restarted (no longer stale); "b" is a fresh session id on the same old build.
  const remaining = [session({ id: "b-new", hook_version: "0.16.0" })];
  assert.equal(hooksNoticeVisible(remaining, "0.16.1", dismissedKey), false);
});

test("restarting every session on one of two dismissed versions keeps the other quiet", () => {
  // Dismissed while two distinct old versions were around.
  const stale = [session({ id: "a", hook_version: "0.16.0" }), session({ id: "b", hook_version: null })];
  const dismissedKey = staleVersionsKey(stale, "0.16.1");
  assert.equal(dismissedKey, "before 0.16.1|v0.16.0");
  // Every v0.16.0 session restarted; only the "before 0.16.1" ones are left.
  const remaining = [session({ id: "c", hook_version: null })];
  assert.equal(hooksNoticeVisible(remaining, "0.16.1", dismissedKey), false);
});

test("a new old version reopens a dismissed notice", () => {
  const first = [session({ id: "a", hook_version: "0.16.0" })];
  const dismissedKey = staleVersionsKey(first, "0.16.1");
  const withNewVersion = [session({ id: "a", hook_version: "0.16.0" }), session({ id: "e", hook_version: "0.14.0" })];
  assert.equal(hooksNoticeVisible(withNewVersion, "0.16.1", dismissedKey), true);
});

// ---------- pickLatestRecaps ----------

function recap(overrides = {}) {
  return { id: 1, session_id: "s1", cwd: "/p", ts: "2026-09-01T00:00:00Z", ...overrides };
}

test("picks the newest recap per active session, up to the limit", () => {
  const sessionsById = new Map([
    ["s1", session({ id: "s1", state: "active" })],
    ["s2", session({ id: "s2", state: "active" })],
    ["s3", session({ id: "s3", state: "active" })],
  ]);
  const recaps = [
    recap({ id: 1, session_id: "s1" }),
    recap({ id: 2, session_id: "s2" }),
    recap({ id: 3, session_id: "s3" }),
    recap({ id: 4, session_id: "s1" }), // older duplicate for s1, already covered
  ];
  const picked = pickLatestRecaps(recaps, sessionsById, "", 3);
  assert.deepEqual(picked.map((r) => r.id), [1, 2, 3]);
});

test("skips recaps for sessions that ended or are unknown, once another one is already picked", () => {
  const sessionsById = new Map([
    ["s1", session({ id: "s1", state: "active" })],
    ["s2", session({ id: "s2", state: "ended" })],
  ]);
  const recaps = [
    recap({ id: 1, session_id: "s1" }),
    recap({ id: 2, session_id: "s2" }),
    recap({ id: 3, session_id: "unknown" }),
  ];
  assert.deepEqual(pickLatestRecaps(recaps, sessionsById, "", 3).map((r) => r.id), [1]);
});

test("falls back to the single newest recap when no active session has one", () => {
  const sessionsById = new Map([["s1", session({ id: "s1", state: "ended" })]]);
  const recaps = [recap({ id: 1, session_id: "s1" })];
  assert.deepEqual(pickLatestRecaps(recaps, sessionsById, "", 3).map((r) => r.id), [1]);
});

test("honours the project filter", () => {
  const sessionsById = new Map([["s1", session({ id: "s1", state: "active" })]]);
  const recaps = [recap({ id: 1, session_id: "s1", cwd: "/other" })];
  assert.deepEqual(pickLatestRecaps(recaps, sessionsById, "/p", 3), []);
});

// ---------- resumeCommand ----------

test("builds a cd-and-resume command when the cwd is safe", () => {
  const { validId, command, label } = resumeCommand(session({ id: "abc123", cwd: "/home/rixmerz/proj" }));
  assert.equal(validId, true);
  assert.equal(command, "cd '/home/rixmerz/proj' && claude --resume abc123");
  assert.equal(label, "Copy resume command");
});

test("drops the cd when the cwd has shell-unsafe characters, and says so", () => {
  const { command, label } = resumeCommand(session({ id: "abc123", cwd: "/tmp/it's a trap" }));
  assert.equal(command, "claude --resume abc123");
  assert.match(label, /run it in the project folder/);
});

test("refuses a session id that doesn't look like one", () => {
  const { validId, command } = resumeCommand(session({ id: "../../etc/passwd" }));
  assert.equal(validId, false);
  assert.equal(command, "");
});
