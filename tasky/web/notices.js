// Pure logic for "say each thing once": which live sessions are running an
// old tasky build, whether the outdated-hooks banner has anything new to
// report since it was last dismissed, which recap is already shown at the
// top of the board (so Done doesn't repeat it), and how to build the
// resume command a stale session needs. No DOM here, so this loads under
// node:test the same way digest.js does.

/** @typedef {{id:string,state:string,hook_version:string|null,title:string|null,cwd:string|null}} Session */
/** @typedef {{id:number,session_id:string,cwd:string|null,ts:string}} Recap */

const SESSION_ID_RE = /^[A-Za-z0-9-]{1,64}$/;
const UNSAFE_CWD_CHARS = /['\\\x00-\x1f\x7f]/;

/** A live session whose hooks predate this server: it still records the old, wrong way. */
export function isSessionStale(session, version) {
  return !!session && !!version && session.state === "active" && session.hook_version !== version;
}

/** Live sessions running an older tasky than the server, in their given order. */
export function staleSessions(sessions, version) {
  if (!version) return [];
  return (sessions || []).filter((s) => isSessionStale(s, version));
}

/** What a stale session's hooks are running, for the "v0.16.0" / "before 0.16.1" phrasing. */
export function ranVersionLabel(session, version) {
  return session.hook_version ? `v${session.hook_version}` : `before ${version}`;
}

/**
 * A key standing for "which old versions are around right now" (sorted, so
 * session order doesn't matter). Stored on dismiss and compared against as
 * a set of covered versions, not as a whole-string match -- see
 * hooksNoticeVisible.
 */
export function staleVersionsKey(stale, version) {
  const labels = new Set(stale.map((s) => ranVersionLabel(s, version)));
  return [...labels].sort().join("|");
}

/**
 * Whether the outdated-hooks banner has something worth saying: there is
 * at least one stale session running a version the dismissed key doesn't
 * already cover. This is a subset check, not equality, so restarting some
 * of several sessions that share an already-known old version shrinks the
 * live set without reopening the banner -- only a version the dismissed
 * key doesn't mention does that.
 */
export function hooksNoticeVisible(stale, version, dismissedKey) {
  if (stale.length === 0) return false;
  const dismissed = new Set(dismissedKey ? dismissedKey.split("|") : []);
  return stale.some((s) => !dismissed.has(ranVersionLabel(s, version)));
}

/**
 * The newest recap of each live session (up to `limit`, newest first);
 * when no live session has one, the single newest recap there is. Honours
 * a project filter the same way the board columns do. `sessionsById` is a
 * Map or anything with a matching `.get`.
 */
export function pickLatestRecaps(recaps, sessionsById, projectFilter, limit) {
  const filtered = (recaps || []).filter((r) => !projectFilter || r.cwd === projectFilter);
  const seen = new Set();
  const picked = [];
  for (const r of filtered) {
    const session = sessionsById.get(r.session_id);
    if (!session || session.state !== "active" || seen.has(r.session_id)) continue;
    seen.add(r.session_id);
    picked.push(r);
    if (picked.length === limit) break;
  }
  if (picked.length === 0 && filtered.length > 0) picked.push(filtered[0]);
  return picked;
}

/**
 * The `claude --resume` command that gets a session's terminal back, and
 * whether it is even safe to build one (a session id survived from the
 * transcript filename, so it is trusted more loosely than user input, but
 * still checked before it goes anywhere near a shell command).
 */
export function resumeCommand(session) {
  if (!SESSION_ID_RE.test(session.id)) return { validId: false, command: "", label: "" };
  const cwdSafe = !!session.cwd && !UNSAFE_CWD_CHARS.test(session.cwd);
  const command = cwdSafe
    ? `cd '${session.cwd}' && claude --resume ${session.id}`
    : `claude --resume ${session.id}`;
  const label = cwdSafe ? "Copy resume command" : "Copy resume command (run it in the project folder)";
  return { validId: true, command, label };
}
