// Tasky dashboard client. No build step, no dependencies.

import { parseDigest, summarize, renderDigest } from "./digest.js";

const POLL_MS = 2000;
const DONE_PAGE_SIZE = 20;
const SEARCH_MIN_CHARS = 2;
const SEARCH_DEBOUNCE_MS = 250;
const SNIPPET_BEFORE = 60;
const SNIPPET_AFTER = 140;
const TOKEN_STORAGE_KEY = "tasky.token";
const MODE_STORAGE_KEY = "tasky.permissionMode";
const TAB_STORAGE_KEY = "tasky.mobileColumn";
const SESSION_ID_RE = /^[A-Za-z0-9-]{1,64}$/;
const UNSAFE_CWD_CHARS = /['\\\x00-\x1f\x7f]/;
const TAB_ORDER = ["inbox", "upnext", "running", "attention", "done"];

const STATUS_GLYPH = {
  running: "▶",
  queued: "⏸",
  interrupted: "⚠",
  failed: "✕",
  done: "✓",
  cancelled: "—",
};

const STATUS_WORD = {
  running: "Running",
  queued: "Queued",
  interrupted: "Interrupted",
  failed: "Failed",
  done: "Done",
  cancelled: "Cancelled",
};

const PERMISSION_HINTS = {
  default: "Gated tools refused.",
  acceptEdits: "Edits allowed, other tools refused.",
  plan: "Read-only.",
  bypassPermissions: "Everything, no asking.",
};

/** @typedef {{id:number,session_id:string|null,parent_id:number|null,kind:string,title:string,body:string,status:string,result:string|null,cwd:string|null,project:string,lane:string|null,run_mode:string|null,permission_mode:string|null,fork_of:string|null,position:number|null,created_at:string,started_at:string|null,finished_at:string|null}} Task */

class UnauthorizedError extends Error {}
class NetworkError extends Error {}
class NotVerifiedError extends Error {}

// ---------- app state ----------

let state = {
  rev: -1,
  sessions: [],
  tasks: [],
  lanes: [],
  config: { queue_prefix: "++", max_chain: 5, port: 7733, allow_bypass: false },
};
let tasksById = new Map();
let sessionsById = new Map();
// Keyed by task id; rebuilt only when task.result changes, so the 2-second
// refresh never re-parses or re-renders a digest the user has open.
let resultDigestById = new Map();
let projectFilter = "";
let multiProject = false;
let doneShown = DONE_PAGE_SIZE;
let pollTimer = null;
let pollInFlight = false;
let refreshInFlight = false;
let accessDenied = false;
let verified = false;
let token = null;
let memoryToken = null;
let permissionMode = "acceptEdits";
let activeTab = "running";

// Current render-order snapshots, rebuilt every renderAll(); the drag code
// reads these instead of re-deriving column membership mid-drag.
let inboxOrderIds = [];
let upnextOrderIds = [];

// ---------- DOM refs ----------

const el = {
  accessState: document.getElementById("access-state"),
  accessCommand: document.getElementById("access-command"),
  accessCopy: document.getElementById("access-copy"),
  reconnecting: document.getElementById("reconnecting-status"),
  topbar: document.getElementById("topbar"),
  board: document.getElementById("board"),
  errorBanner: document.getElementById("error-banner"),
  errorBannerText: document.getElementById("error-banner-text"),
  errorBannerDismiss: document.getElementById("error-banner-dismiss"),
  announcer: document.getElementById("announcer"),
  projectFilter: document.getElementById("project-filter"),
  topbarExtra: document.getElementById("topbar-extra"),
  modeSelect: document.getElementById("mode-select"),
  modeHint: document.getElementById("mode-hint"),
  bypassChip: document.getElementById("bypass-chip"),
  quickAddForm: document.getElementById("quick-add"),
  quickAddInput: document.getElementById("quick-add-input"),
  quickAddChips: document.getElementById("quick-add-chips"),
  quickAddTarget: document.getElementById("quick-add-target"),
  filterChip: document.getElementById("filter-chip"),
  filterChipLabel: document.getElementById("filter-chip-label"),
  settingsToggle: document.getElementById("settings-toggle"),
  quickAddPrefix: document.getElementById("quick-add-prefix"),
  inboxEmptyPrefix: document.getElementById("inbox-empty-prefix"),
  tablist: document.getElementById("tablist"),
  inboxCount: document.getElementById("inbox-count"),
  upnextCount: document.getElementById("upnext-count"),
  runningCount: document.getElementById("running-count"),
  attentionCount: document.getElementById("attention-count"),
  doneCount: document.getElementById("done-count"),
  inboxList: document.getElementById("inbox-list"),
  inboxEmpty: document.getElementById("inbox-empty"),
  upnextList: document.getElementById("upnext-list"),
  upnextEmpty: document.getElementById("upnext-empty"),
  runnerList: document.getElementById("runner-list"),
  runnerEmpty: document.getElementById("runner-empty"),
  runnerLabel: document.getElementById("runner-label"),
  parallelLabel: document.getElementById("parallel-label"),
  parallelList: document.getElementById("parallel-list"),
  parallelEmpty: document.getElementById("parallel-empty"),
  laneBanners: document.getElementById("lane-banners"),
  attentionList: document.getElementById("attention-list"),
  attentionEmpty: document.getElementById("attention-empty"),
  doneList: document.getElementById("done-list"),
  doneEmpty: document.getElementById("done-empty"),
  doneShowMore: document.getElementById("done-show-more"),
  importHistory: document.getElementById("import-history"),
  filterEmpty: document.getElementById("filter-empty"),
  filterEmptyProject: document.getElementById("filter-empty-project"),
  filterReset: document.getElementById("filter-reset"),
  clipboardFallback: document.getElementById("clipboard-fallback"),
  tplCard: document.getElementById("tpl-card"),
  tplChild: document.getElementById("tpl-child"),
  drawerBackdrop: document.getElementById("drawer-backdrop"),
  drawer: document.getElementById("drawer"),
  drawerTitle: document.getElementById("drawer-title"),
  drawerClose: document.getElementById("drawer-close"),
  drawerMeta: document.querySelector("#drawer .drawer-meta"),
  drawerEditOpen: document.querySelector("#drawer .drawer-edit-open"),
  drawerEdit: document.querySelector("#drawer .drawer-edit"),
  drawerEditTextarea: document.querySelector("#drawer .drawer-edit-textarea"),
  drawerEditSave: document.querySelector("#drawer .drawer-edit-save"),
  drawerEditCancel: document.querySelector("#drawer .drawer-edit-cancel"),
  drawerBody: document.querySelector("#drawer .drawer-body"),
  drawerTaskSection: document.querySelector("#drawer .drawer-task-section"),
  drawerResultWrap: document.querySelector("#drawer .drawer-result-wrap"),
  drawerResultDigest: document.querySelector("#drawer .drawer-result-digest"),
  drawerShowOriginal: document.querySelector("#drawer .drawer-show-original"),
  drawerResultText: document.querySelector("#drawer .drawer-result-text"),
  drawerChildren: document.querySelector("#drawer .drawer-children"),
  drawerChildrenWrap: document.querySelector("#drawer .drawer-children-wrap"),
  drawerSessionLine: document.querySelector("#drawer .drawer-session-line"),
  searchForm: document.getElementById("search"),
  searchInput: document.getElementById("search-input"),
  searchResults: document.getElementById("search-results"),
  searchHeading: document.getElementById("search-heading"),
  searchList: document.getElementById("search-list"),
  searchClear: document.getElementById("search-clear"),
  tablist: document.getElementById("tablist"),
};

// ---------- access token ----------
//
// The token lives in memory and in sessionStorage: it survives a reload in
// the same tab and disappears when the tab closes. A stray localStorage copy
// from an older version is removed on load.

function clearLegacyLocalStorageToken() {
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // storage unavailable (private mode, disabled cookies) - nothing to clear
  }
}

function readStoredToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return memoryToken;
  }
}

function storeToken(value) {
  try {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, value);
  } catch {
    memoryToken = value;
  }
}

function initToken() {
  clearLegacyLocalStorageToken();
  const fromUrl = new URLSearchParams(location.hash.replace(/^#/, "")).get("token");
  if (fromUrl) {
    token = fromUrl;
    storeToken(fromUrl);
    history.replaceState(null, "", location.pathname + location.search);
  } else {
    token = readStoredToken();
  }
}

// ---------- server verification ----------
//
// The server is verified before the token is ever sent: a fresh nonce is
// sent to GET /api/version with no token header, and the returned proof is
// checked against an HMAC computed locally from the stored token. Only a
// matching proof unlocks requests that carry X-Tasky-Token.

function randomNonceHex() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function bufferToHex(buffer) {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function computeExpectedProof(nonce) {
  if (!token) return null;
  try {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey("raw", enc.encode(token), { name: "HMAC", hash: "SHA-256" }, false, [
      "sign",
    ]);
    const message = `${nonce}:${location.port}`;
    const signature = await crypto.subtle.sign("HMAC", key, enc.encode(message));
    return bufferToHex(signature);
  } catch {
    return null;
  }
}

/** Verifies the server holds the token before any token-bearing request is sent. */
async function verifyServer() {
  if (!(window.crypto && window.crypto.subtle)) {
    showAccessState();
    return false;
  }
  const nonce = randomNonceHex();
  let res;
  try {
    res = await fetch("/api/version", { headers: { "X-Tasky-Nonce": nonce } });
  } catch {
    verified = false;
    showReconnecting();
    return false;
  }
  if (res.status !== 200) {
    verified = false;
    showAccessState();
    return false;
  }
  let data;
  try {
    data = await res.json();
  } catch {
    verified = false;
    showAccessState();
    return false;
  }
  const expected = await computeExpectedProof(nonce);
  if (expected === null || !data || typeof data.proof !== "string" || data.proof !== expected) {
    verified = false;
    showAccessState();
    return false;
  }
  verified = true;
  hideReconnecting();
  return true;
}

async function ensureVerified() {
  if (accessDenied) return false;
  if (verified) return true;
  return verifyServer();
}

// ---------- fetch helpers ----------

async function doFetch(path, options) {
  try {
    return await fetch(path, options);
  } catch (err) {
    verified = false;
    throw new NetworkError(err.message);
  }
}

async function apiGet(path) {
  if (!(await ensureVerified())) throw new NotVerifiedError();
  const res = await doFetch(path, { headers: { "X-Tasky-Token": token || "" } });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(`GET ${path} failed (${res.status})`);
  return res.json();
}

async function apiMutate(method, path, body) {
  if (!(await ensureVerified())) throw new NotVerifiedError();
  const res = await doFetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Tasky-Token": token || "" },
    body: JSON.stringify(body ?? {}),
  });
  if (res.status === 401) throw new UnauthorizedError();
  const data = await res.json().catch(() => ({}));
  if (res.status === 409) {
    throw new Error(data.error || "already taken by another run or session");
  }
  if (!res.ok) {
    throw new Error(data.error || `${method} ${path} failed (${res.status})`);
  }
  return data;
}

function showError(message) {
  el.errorBannerText.textContent = message;
  el.errorBanner.hidden = false;
}

el.errorBannerDismiss.addEventListener("click", () => {
  el.errorBanner.hidden = true;
});

function handleApiError(err) {
  if (err instanceof UnauthorizedError) {
    showAccessState();
  } else if (err instanceof NetworkError) {
    showReconnecting();
  } else if (err instanceof NotVerifiedError) {
    // verifyServer() already surfaced the right state (reconnecting or
    // access-denied); nothing further to show here.
  } else {
    showError(err.message);
  }
}

function showAccessState() {
  if (accessDenied) return;
  accessDenied = true;
  stopPolling();
  el.errorBanner.hidden = true;
  hideReconnecting();
  el.topbar.hidden = true;
  el.tablist.hidden = true;
  el.board.hidden = true;
  el.accessState.hidden = false;
}

function showReconnecting() {
  if (accessDenied) return;
  el.reconnecting.textContent = "Reconnecting…";
}

function hideReconnecting() {
  el.reconnecting.textContent = "";
}

function announce(message) {
  el.announcer.textContent = "";
  // Force a DOM mutation so repeated identical messages still get announced.
  window.setTimeout(() => {
    el.announcer.textContent = message;
  }, 30);
}

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ---------- polling ----------

async function poll() {
  if (pollInFlight || accessDenied) return;
  pollInFlight = true;
  try {
    const version = await apiGet("/api/version");
    if (version.rev !== state.rev) {
      await refresh();
    } else if (!dragActive()) {
      // No data change, but relative times (e.g. running elapsed) advance.
      renderAll();
    }
  } catch (err) {
    handleApiError(err);
  } finally {
    pollInFlight = false;
  }
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const next = await apiGet("/api/state");
    if (next.rev < state.rev) return; // stale response, a newer one already applied
    applyState(next);
  } finally {
    refreshInFlight = false;
  }
}

let pendingState = null;

function applyState(next) {
  if (dragActive()) {
    pendingState = next;
    return;
  }
  const nextTasksById = new Map(next.tasks.map((t) => [t.id, t]));
  for (const [id, prev] of tasksById) {
    const now = nextTasksById.get(id);
    if (now && isTerminal(now.status) && !isTerminal(prev.status)) {
      announce(`${now.title} ${STATUS_WORD[now.status].toLowerCase()}`);
    }
  }
  state = next;
  tasksById = nextTasksById;
  sessionsById = new Map(next.sessions.map((s) => [s.id, s]));
  for (const id of resultDigestById.keys()) {
    if (!nextTasksById.has(id)) resultDigestById.delete(id);
  }
  el.quickAddPrefix.textContent = next.config.queue_prefix;
  el.inboxEmptyPrefix.textContent = next.config.queue_prefix;
  renderAll();
}

function startPolling() {
  stopPolling();
  poll();
  pollTimer = window.setInterval(poll, POLL_MS);
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (accessDenied) return;
  if (document.hidden) {
    stopPolling();
  } else {
    startPolling();
  }
});

// ---------- derived data ----------

function isTerminal(status) {
  return status === "done" || status === "failed" || status === "interrupted" || status === "cancelled";
}

function topLevelTasks() {
  return state.tasks.filter((t) => t.parent_id == null || !tasksById.has(t.parent_id));
}

function childrenOf(taskId) {
  return state.tasks.filter((t) => t.parent_id === taskId);
}

function matchesFilter(task) {
  return !projectFilter || task.cwd === projectFilter;
}

function projectLabel(cwd) {
  if (!cwd) return "(no project)";
  const parts = cwd.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : cwd;
}

/**
 * Every project the ledger knows, with what makes it worth listing: its
 * active sessions, whether anything is still open in it, and when it was
 * last touched. "Active" means a live session or a task that is not
 * finished; everything else (old worktrees, test scratch folders) is real
 * data but goes under "Other" so it never crowds the choices that matter.
 */
function projectActivity() {
  const byCwd = new Map();
  const touch = (cwd, when) => {
    if (!cwd) return null;
    let entry = byCwd.get(cwd);
    if (!entry) {
      entry = { cwd, label: projectLabel(cwd), active: false, last: "", sessions: [] };
      byCwd.set(cwd, entry);
    }
    if (when && when > entry.last) entry.last = when;
    return entry;
  };
  for (const t of state.tasks) {
    const entry = touch(t.cwd, t.finished_at || t.started_at || t.created_at);
    if (entry && !isTerminal(t.status)) entry.active = true;
  }
  for (const s of state.sessions) {
    const entry = touch(s.cwd, s.last_seen_at);
    if (entry && s.state === "active") {
      entry.active = true;
      entry.sessions.push(s);
    }
  }
  const all = Array.from(byCwd.values());
  // Folders that share a name (worktrees, scratch copies) get their parent too.
  const byLabel = new Map();
  for (const entry of all) byLabel.set(entry.label, (byLabel.get(entry.label) || 0) + 1);
  for (const entry of all) {
    if (byLabel.get(entry.label) > 1) entry.label = entry.cwd.split("/").filter(Boolean).slice(-2).join("/");
  }
  for (const entry of all) entry.sessions.sort((a, b) => (a.last_seen_at < b.last_seen_at ? 1 : -1));
  all.sort((a, b) => (a.last < b.last ? 1 : a.last > b.last ? -1 : 0));
  return { active: all.filter((e) => e.active), other: all.filter((e) => !e.active) };
}

function mostRecentActiveSession() {
  const active = state.sessions.filter((s) => s.state === "active");
  active.sort((a, b) => (a.last_seen_at < b.last_seen_at ? 1 : -1));
  return active[0] || null;
}

function activeSessionsForCwd(cwd) {
  return state.sessions.filter((s) => s.state === "active" && s.cwd === cwd);
}

/**
 * Sessions in this directory that Parallel can actually clone: same cwd and
 * a transcript to resume from (a session without one can't be replayed).
 * What makes Parallel available at all (design.md §7, amended).
 */
function resumableSessionsForCwd(cwd) {
  return state.sessions.filter((s) => s.cwd === cwd && !!s.transcript_path);
}

/**
 * Which of those the server will actually pick: the most recent session a
 * person drove (source !== "worker") ahead of a headless worker session.
 */
function preferredForkSource(cwd) {
  const candidates = resumableSessionsForCwd(cwd);
  const driven = candidates.filter((s) => s.source !== "worker");
  const pool = driven.length > 0 ? driven : candidates;
  return pool.slice().sort((a, b) => (a.last_seen_at < b.last_seen_at ? 1 : -1))[0] || null;
}

function laneByCwd(cwd) {
  return (state.lanes || []).find((l) => l.cwd === cwd) || null;
}

function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffMs = Date.now() - then;
  const mins = Math.max(0, Math.round(diffMs / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  return `${days}d`;
}

function taskTimeLabel(task) {
  if (task.status === "running") return relativeTime(task.started_at);
  if (task.status === "queued") return relativeTime(task.created_at);
  if (isTerminal(task.status)) return relativeTime(task.finished_at || task.created_at);
  return relativeTime(task.created_at);
}

function forkSourceLabel(task) {
  const source = task.fork_of ? sessionsById.get(task.fork_of) : null;
  return source ? source.title || source.id : "a session";
}

/** Parses+renders a task's result once per distinct result string, so refreshes reuse the same DOM node. */
function getResultDigest(task) {
  if (!task.result) return null;
  const cached = resultDigestById.get(task.id);
  if (cached && cached.result === task.result) return cached;
  const digest = parseDigest(task.result);
  const { summary, waiting } = summarize(task.result);
  const entry = { result: task.result, summary, waiting, node: renderDigest(digest, document) };
  resultDigestById.set(task.id, entry);
  return entry;
}

function isWaiting(task) {
  const digest = getResultDigest(task);
  return !!(digest && digest.waiting);
}

function childCountLabel(taskId) {
  const kids = childrenOf(taskId);
  if (kids.length === 0) return "";
  const delegations = kids.filter((k) => k.kind === "delegation").length;
  return delegations ? `${delegations} delegation${delegations === 1 ? "" : "s"}` : "";
}

/** Up next task ids for one project, in display order (a slice of upnextOrderIds). */
function idsForCwdInUpNext(cwd) {
  return upnextOrderIds.filter((id) => {
    const t = tasksById.get(id);
    return t && t.cwd === cwd;
  });
}

function upNextPositionInfo(taskId) {
  const index = upnextOrderIds.indexOf(taskId);
  return { index, total: upnextOrderIds.length };
}

// ---------- rendering: keyed list reconciliation ----------

/**
 * Reconciles `container`'s children against `items`, reusing existing DOM
 * nodes (preserving an open overflow menu, focus, and typed text) when the
 * key still matches. Cards that are mid-drag (lifted out of the DOM tree)
 * are skipped: the drag code owns their position until it commits.
 */
function reconcileList(container, items, keyFn, createFn, updateFn) {
  const existing = new Map();
  for (const child of Array.from(container.children)) {
    existing.set(child.dataset.key, child);
  }
  let prevNode = null;
  for (const item of items) {
    const key = String(keyFn(item));
    let node = existing.get(key);
    if (node) {
      existing.delete(key);
      updateFn(node, item);
    } else if (drag && drag.taskId === keyFn(item) && drag.node) {
      // The dragged card is currently detached (appended to <body> while
      // lifted); leave it there instead of creating a duplicate.
      continue;
    } else {
      node = createFn(item);
      node.dataset.key = key;
    }
    const wantsPosition = prevNode ? prevNode.nextSibling : container.firstChild;
    if (wantsPosition !== node) {
      container.insertBefore(node, wantsPosition);
    }
    prevNode = node;
  }
  for (const leftover of existing.values()) {
    if (drag && drag.originSlot === leftover) continue;
    leftover.remove();
  }
}

const TINT_COUNT = 5;
const TINT_CLASSES = Array.from({ length: TINT_COUNT }, (_, i) => `tint-${i}`);

/**
 * Assigns the repeating 5-colour tint cycle by DOM order within one column
 * (or Running section): card N gets tint N % 5, counting from 0. Reusing
 * plain classes (not :nth-child) means a lifted card keeps its tint while
 * detached mid-drag, and the cycle is simply recomputed here on every
 * renderAll() after a drop. The drag-origin placeholder is not a task card
 * and never gets a tint.
 */
function applyTints(listEl) {
  const cards = Array.from(listEl.children).filter(
    (c) => c.classList.contains("card") && !c.classList.contains("drag-origin"),
  );
  cards.forEach((card, i) => {
    card.classList.remove("tinted", ...TINT_CLASSES);
    card.classList.add("tinted", TINT_CLASSES[i % TINT_COUNT]);
  });
}

// ---------- card rendering ----------
//
// A task is one line: drag handle or running dot, status dot, title, an
// optional "Needs answer" badge and project tag, elapsed time, then the
// row's own icon actions (Run / After last / Parallel in Inbox and Up next,
// Run again in Needs attention) and the "..." menu that repeats them with
// words plus everything secondary. Full detail (body, digest, delegations,
// session) lives in the side drawer -- never inline in the column.

function createCard(task, kind) {
  const node = el.tplCard.content.firstElementChild.cloneNode(true);
  wireTitleButton(node);
  wireOverflowToggle(node);
  updateCard(node, task, kind);
  return node;
}

function createCardFor(kind) {
  return (task) => createCard(task, kind);
}

function updateCardFor(kind) {
  return (node, task) => updateCard(node, task, kind);
}

function wireTitleButton(node) {
  node.querySelector(".title-btn").addEventListener("click", () => {
    openDrawer(Number(node.dataset.taskId));
  });
}

function wireOverflowToggle(node) {
  const btn = node.querySelector(".overflow-btn");
  const menu = node.querySelector(".overflow-menu");
  btn.addEventListener("click", (evt) => {
    evt.stopPropagation();
    if (menu.hidden) openOverflowMenu(btn, menu);
    else closeOverflowMenu();
  });
  menu.addEventListener("keydown", (evt) => {
    const items = Array.from(menu.querySelectorAll('[role="menuitem"]:not([hidden])'));
    const idx = items.indexOf(document.activeElement);
    if (evt.key === "ArrowDown") {
      evt.preventDefault();
      (items[idx + 1] || items[0])?.focus();
    } else if (evt.key === "ArrowUp") {
      evt.preventDefault();
      (items[idx - 1] || items[items.length - 1])?.focus();
    } else if (evt.key === "Escape") {
      evt.preventDefault();
      closeOverflowMenu();
      btn.focus();
    }
  });
}

function updateCard(node, task, kind) {
  node.dataset.status = task.status;
  node.dataset.taskId = String(task.id);
  node.classList.toggle("pinned", kind === "done" && isWaiting(task));

  renderHandle(node, task, kind);

  const titleBtn = node.querySelector(".title-btn");
  const glyph = titleBtn.querySelector(".status-glyph");
  const title = titleBtn.querySelector(".title");
  const time = node.querySelector(".time");
  const projectTag = node.querySelector(".tag-project");
  const answerBadge = node.querySelector(".badge-answer");

  glyph.textContent = STATUS_GLYPH[task.status] || "";
  title.textContent = task.title;
  titleBtn.title = task.title;
  // What a screen reader hears for the row: title, status, then how it runs.
  const spoken = [task.title, STATUS_WORD[task.status] || task.status];
  if (kind === "runner") spoken.push("queue runner");
  if (kind === "parallel" && task.run_mode === "fork") spoken.push(`fork of ${forkSourceLabel(task)}`);
  titleBtn.setAttribute("aria-label", `${spoken.join(", ")}. Open details`);

  time.textContent = taskTimeLabel(task);

  // The project tag only earns its place when the board mixes projects.
  const showProject = multiProject && !projectFilter && !!task.cwd;
  projectTag.hidden = !showProject;
  projectTag.textContent = showProject ? projectLabel(task.cwd) : "";
  projectTag.title = showProject ? task.cwd : "";

  const digestEntry = getResultDigest(task);
  const waiting = !!(digestEntry && digestEntry.waiting);
  answerBadge.hidden = !waiting;
  answerBadge.title = waiting ? "The result ends with a question for you" : "";

  renderPrimaryActions(node, task, kind);
  renderOverflowMenu(node, task, kind);
}

function renderHandle(node, task, kind) {
  const handle = node.querySelector(":scope > .handle");
  const runGlyph = node.querySelector(":scope > .run-glyph");
  if (kind === "inbox" || kind === "upnext") {
    runGlyph.hidden = true;
    handle.hidden = false;
    handle.dataset.taskId = String(task.id);
    handle.dataset.lane = kind;
    if (kind === "upnext") {
      const { index, total } = upNextPositionInfo(task.id);
      handle.querySelector(".position").textContent = index === -1 ? "" : String(index + 1);
      handle.setAttribute("aria-label", `Reorder ${task.title}, position ${index + 1} of ${total}`);
    } else {
      handle.querySelector(".position").textContent = "";
      handle.setAttribute("aria-label", `Reorder ${task.title}`);
    }
  } else if (kind === "runner" || kind === "parallel") {
    handle.hidden = true;
    runGlyph.hidden = false;
  } else {
    handle.hidden = true;
    runGlyph.hidden = true;
  }
}

function renderChildren(container, kids) {
  reconcileList(
    container,
    kids,
    (k) => k.id,
    (k) => {
      const node = el.tplChild.content.firstElementChild.cloneNode(true);
      updateChild(node, k);
      return node;
    },
    updateChild,
  );
}

function updateChild(node, task) {
  node.querySelector(".status-glyph").textContent = STATUS_GLYPH[task.status] || "";
  node.querySelector(".status-word").textContent = STATUS_WORD[task.status] || task.status;
  node.querySelector(".child-title").textContent = task.title;
}

function renderSessionLine(container, task) {
  const session = task.session_id ? sessionsById.get(task.session_id) : null;
  if (!session) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  container.querySelector(".session-title").textContent = session.title || session.id;
  container.querySelector(".session-state").textContent = session.state === "active" ? "Active" : "Ended";

  const toggle = container.querySelector(".auto-pull-toggle");
  toggle.checked = !!session.auto_pull;
  toggle.onchange = () => toggleAutoPull(session.id, toggle.checked);

  const copyBtn = container.querySelector(".copy-resume");
  const validId = SESSION_ID_RE.test(session.id);
  copyBtn.hidden = !validId;
  if (!validId) return;

  const cwdSafe = !!session.cwd && !UNSAFE_CWD_CHARS.test(session.cwd);
  const command = cwdSafe
    ? `cd '${session.cwd}' && claude --resume ${session.id}`
    : `claude --resume ${session.id}`;
  const label = cwdSafe ? "Copy resume command" : "Copy resume command (run it in the project folder)";

  if (copyBtn.dataset.copied !== "1") {
    copyBtn.textContent = label;
  }
  copyBtn.onclick = () => copyResumeCommand(command, copyBtn, label);
}

/**
 * The one row of primary actions a collapsed card shows (design review):
 * Inbox/Up next get the three-action group; Running gets Mark done/Cancel;
 * Needs attention gets Run again/Back to Inbox; Done gets neither. The hint
 * line stays hover/focus-only (never widens the row) and is a single short
 * sentence so it never needs to wrap.
 */
function renderPrimaryActions(node, task, kind) {
  const group = node.querySelector(".actions");
  const pair = node.querySelector(".action-pair");
  const hint = node.querySelector(".action-hint");
  const hintId = `hint-${task.id}`;
  hint.id = hintId;
  hint.textContent = "";

  if (kind === "inbox" || kind === "upnext") {
    pair.hidden = true;
    group.hidden = false;
    const runBtn = group.querySelector(".act-run");
    const afterBtn = group.querySelector(".act-after");
    const parallelBtn = group.querySelector(".act-parallel");
    const { isLast, canClone, cloneReason } = startOptions(task, kind);

    runBtn.title = "Run now (new background worker)";
    runBtn.setAttribute("aria-label", "Run now in a new background worker");
    runBtn.removeAttribute("aria-disabled");
    runBtn.onclick = () => runTask(task.id, "now");

    afterBtn.title = isLast ? "Already last in Up next" : "After last (join the run queue)";
    afterBtn.setAttribute("aria-label", "Run after the last task in Up next");
    afterBtn.setAttribute("aria-disabled", String(isLast));
    afterBtn.onclick = isLast ? null : () => enqueueTask(task.id, null);

    parallelBtn.title = canClone ? "Parallel with context (replays the session's context)" : cloneReason;
    parallelBtn.setAttribute("aria-label", "Run in parallel, replaying this session's context");
    parallelBtn.setAttribute("aria-disabled", String(!canClone));
    parallelBtn.setAttribute("aria-describedby", hintId);
    parallelBtn.onclick = canClone ? () => runTask(task.id, "fork") : null;
    hint.textContent = canClone ? "Replays the session's context." : cloneReason;
  } else if (kind === "attention") {
    group.hidden = true;
    pair.hidden = false;
    const runAgainBtn = pair.querySelector(".pair-a");
    runAgainBtn.title = "Run again";
    runAgainBtn.querySelector(".pair-a-label").textContent = "Run again";
    runAgainBtn.setAttribute("aria-label", `Run ${task.title} again`);
    runAgainBtn.onclick = () => runAgain(task.id);
  } else {
    group.hidden = true;
    pair.hidden = true;
  }
}

/** The three start options for a waiting card, shared by the row buttons and the menu. */
function startOptions(task, kind) {
  const sameCwd = idsForCwdInUpNext(task.cwd);
  const isLast = kind === "upnext" && sameCwd.length > 0 && sameCwd[sameCwd.length - 1] === task.id;
  const canClone = !!preferredForkSource(task.cwd);
  const cloneReason = canClone ? "" : `No session in ${projectLabel(task.cwd)} to clone`;
  return { isLast, canClone, cloneReason };
}

/**
 * Everything not primary for this card's column: Move up/down, Back to
 * Inbox (Up next), Edit text (Inbox), Mark done where it is not the primary
 * action (Needs attention), and Delete everywhere it is offered. Details
 * opens the side drawer and is always first.
 */
function renderOverflowMenu(node, task, kind) {
  const menu = node.querySelector(".overflow-menu");
  const detailsBtn = menu.querySelector(".menu-details");
  const editItem = menu.querySelector(".menu-edit");
  const moveUp = menu.querySelector(".menu-move-up");
  const moveDown = menu.querySelector(".menu-move-down");
  const backInbox = menu.querySelector(".menu-back-inbox");
  const markDone = menu.querySelector(".menu-mark-done");
  const cancelItem = menu.querySelector(".menu-cancel");
  const deleteBtn = menu.querySelector(".menu-delete");
  const runItem = menu.querySelector(".menu-run");
  const afterItem = menu.querySelector(".menu-after");
  const parallelItem = menu.querySelector(".menu-parallel");

  editItem.hidden = true;
  moveUp.hidden = true;
  moveDown.hidden = true;
  backInbox.hidden = true;
  markDone.hidden = true;
  cancelItem.hidden = true;
  runItem.hidden = true;
  afterItem.hidden = true;
  parallelItem.hidden = true;
  deleteBtn.hidden = kind === "runner" || kind === "parallel";

  // The row's icon buttons repeated with words, for anyone who did not
  // guess the glyphs (and for touch, where there is no tooltip).
  if (kind === "inbox" || kind === "upnext") {
    const { isLast, canClone, cloneReason } = startOptions(task, kind);
    runItem.hidden = false;
    runItem.onclick = () => {
      closeOverflowMenu();
      runTask(task.id, "now");
    };
    afterItem.hidden = false;
    afterItem.disabled = isLast;
    afterItem.onclick = () => {
      closeOverflowMenu();
      if (!isLast) enqueueTask(task.id, null);
    };
    parallelItem.hidden = false;
    parallelItem.disabled = !canClone;
    parallelItem.title = canClone ? "Replays the session's context" : cloneReason;
    parallelItem.onclick = () => {
      closeOverflowMenu();
      if (canClone) runTask(task.id, "fork");
    };
  }

  detailsBtn.onclick = () => {
    closeOverflowMenu();
    openDrawer(task.id);
  };

  if (kind === "inbox") {
    editItem.hidden = false;
    editItem.onclick = () => {
      closeOverflowMenu();
      openDrawer(task.id, { edit: true });
    };
  } else if (kind === "upnext") {
    moveUp.hidden = false;
    moveDown.hidden = false;
    backInbox.hidden = false;
    const sameCwd = idsForCwdInUpNext(task.cwd);
    const localIndex = sameCwd.indexOf(task.id);
    moveUp.disabled = localIndex <= 0;
    moveUp.onclick = () => {
      closeOverflowMenu();
      if (localIndex > 0) reorderUpNext(task.id, sameCwd[localIndex - 1]);
    };
    moveDown.disabled = localIndex === -1 || localIndex >= sameCwd.length - 1;
    moveDown.onclick = () => {
      closeOverflowMenu();
      if (localIndex === -1 || localIndex >= sameCwd.length - 1) return;
      const beforeId = localIndex + 2 < sameCwd.length ? sameCwd[localIndex + 2] : null;
      reorderUpNext(task.id, beforeId);
    };
    backInbox.onclick = () => {
      closeOverflowMenu();
      moveToInbox(task.id, null);
    };
  } else if (kind === "attention") {
    backInbox.hidden = false;
    backInbox.onclick = () => {
      closeOverflowMenu();
      backToInbox(task.id);
    };
    markDone.hidden = false;
    markDone.onclick = () => {
      closeOverflowMenu();
      updateTaskStatus(task.id, "done");
    };
  } else if (kind === "runner" || kind === "parallel") {
    markDone.hidden = false;
    markDone.onclick = () => {
      closeOverflowMenu();
      updateTaskStatus(task.id, "done");
    };
    cancelItem.hidden = false;
    cancelItem.onclick = () => {
      closeOverflowMenu();
      updateTaskStatus(task.id, "cancelled");
    };
  }

  if (deleteBtn.hidden) return;
  if (deleteBtn.dataset.confirming !== "1") {
    deleteBtn.textContent = "Delete";
  }
  deleteBtn.onclick = () => {
    if (deleteBtn.dataset.confirming === "1") {
      closeOverflowMenu();
      deleteTask(task.id);
      return;
    }
    deleteBtn.dataset.confirming = "1";
    deleteBtn.textContent = "Delete for good?";
    window.setTimeout(() => {
      if (deleteBtn.isConnected) {
        deleteBtn.dataset.confirming = "";
        deleteBtn.textContent = "Delete";
      }
    }, 4000);
  };
}

// ---------- overflow menu (open/close, one at a time) ----------

let openMenu = null;

function openOverflowMenu(btn, menu) {
  closeOverflowMenu();
  menu.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  openMenu = { btn, menu };
  const first = menu.querySelector('[role="menuitem"]:not([hidden])');
  if (first) first.focus();
}

function closeOverflowMenu() {
  if (!openMenu) return;
  openMenu.menu.hidden = true;
  openMenu.btn.setAttribute("aria-expanded", "false");
  openMenu = null;
}

document.addEventListener("click", () => closeOverflowMenu());

// ---------- details drawer ----------
//
// Frozen on open: renderDrawerContent() runs only when the drawer opens and
// after a mutation the drawer itself triggered (edit save, auto-pull
// toggle), never from the poll loop's applyState()/renderAll() -- so a
// background refresh cannot re-render or close it out from under the user.

let drawerTaskId = null;
let drawerReturnFocus = null;

function isDrawerOpen() {
  return drawerTaskId !== null;
}

/** A task from the board, or a search hit older than the slice /api/state ships. */
function findTask(taskId) {
  return tasksById.get(taskId) || searchHitsById.get(taskId);
}

function openDrawer(taskId, opts = {}) {
  const task = findTask(taskId);
  if (!task) return;
  closeOverflowMenu();
  drawerReturnFocus = document.activeElement;
  drawerTaskId = taskId;
  renderDrawerContent(task, opts.edit === true);
  el.drawer.hidden = false;
  el.drawer.setAttribute("aria-hidden", "false");
  el.drawerBackdrop.hidden = false;
  document.body.classList.add("drawer-open");
  el.drawerClose.focus();
}

function closeDrawer() {
  if (!isDrawerOpen()) return;
  drawerTaskId = null;
  el.drawer.hidden = true;
  el.drawer.setAttribute("aria-hidden", "true");
  el.drawerBackdrop.hidden = true;
  document.body.classList.remove("drawer-open");
  const returnTo = drawerReturnFocus;
  drawerReturnFocus = null;
  if (returnTo && returnTo.isConnected) returnTo.focus();
}

/** Re-populates the open drawer after a mutation the drawer itself made. */
function refreshDrawerIfOpen() {
  if (!isDrawerOpen()) return;
  const task = findTask(drawerTaskId);
  if (!task) {
    closeDrawer();
    return;
  }
  renderDrawerContent(task, !el.drawerEdit.hidden);
}

function renderDrawerContent(task, editing) {
  el.drawerTitle.textContent = task.title;

  const metaParts = [STATUS_WORD[task.status] || task.status];
  if (task.cwd) metaParts.push(projectLabel(task.cwd));
  if (task.kind !== "prompt") metaParts.push(task.kind);
  const time = taskTimeLabel(task);
  if (time) metaParts.push(time);
  el.drawerMeta.textContent = metaParts.join(" · ");

  if (task.body && task.body !== task.title) {
    el.drawerBody.textContent = task.body;
    el.drawerBody.hidden = false;
  } else {
    el.drawerBody.textContent = "";
    el.drawerBody.hidden = true;
  }

  const canEdit = task.status === "queued" && task.lane == null;
  // The Task section only exists when it adds to the header: a body longer
  // than the title, or an edit control.
  el.drawerTaskSection.hidden = el.drawerBody.hidden && !canEdit;
  el.drawerEditOpen.hidden = !canEdit;
  el.drawerEditOpen.onclick = () => {
    el.drawerEdit.hidden = false;
    el.drawerEditTextarea.value = task.body || task.title;
    el.drawerEditTextarea.focus();
  };
  el.drawerEditCancel.onclick = () => {
    el.drawerEdit.hidden = true;
  };
  el.drawerEditSave.onclick = async () => {
    const value = el.drawerEditTextarea.value.trim();
    if (!value) return;
    try {
      await apiMutate("PATCH", `/api/tasks/${task.id}`, { body: value });
      el.drawerEdit.hidden = true;
      await refresh();
      refreshDrawerIfOpen();
    } catch (err) {
      handleApiError(err);
    }
  };
  if (!canEdit) {
    el.drawerEdit.hidden = true;
  } else if (editing) {
    el.drawerEdit.hidden = false;
    el.drawerEditTextarea.value = task.body || task.title;
  }

  const digestEntry = getResultDigest(task);
  if (digestEntry) {
    el.drawerResultWrap.hidden = false;
    if (el.drawerResultDigest.firstChild !== digestEntry.node) {
      el.drawerResultDigest.textContent = "";
      el.drawerResultDigest.appendChild(digestEntry.node);
    }
    el.drawerResultText.textContent = task.result;
    el.drawerShowOriginal.onclick = () => {
      const revealing = el.drawerResultText.hidden;
      el.drawerResultText.hidden = !revealing;
      el.drawerShowOriginal.textContent = revealing ? "Hide original" : "Show original";
    };
  } else {
    el.drawerResultWrap.hidden = true;
    if (el.drawerResultDigest.firstChild) el.drawerResultDigest.textContent = "";
    el.drawerResultText.textContent = "";
    el.drawerResultText.hidden = true;
    el.drawerShowOriginal.textContent = "Show original";
  }

  const kids = childrenOf(task.id);
  el.drawerChildrenWrap.hidden = kids.length === 0;
  renderChildren(el.drawerChildren, kids);
  renderSessionLine(el.drawerSessionLine, task);
}

// ---------- lane banners ----------

function renderLaneBanners() {
  const container = el.laneBanners;
  container.textContent = "";
  const paused = (state.lanes || []).filter((l) => l.paused && (!projectFilter || l.cwd === projectFilter));
  for (const lane of paused) {
    const div = document.createElement("div");
    div.className = "lane-banner";
    const text = document.createElement("span");
    text.textContent = `${projectLabel(lane.cwd)} paused${lane.reason ? ": " + lane.reason : ""}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Resume";
    btn.onclick = () => resumeLane(lane.cwd);
    div.append(text, btn);
    container.appendChild(div);
  }
}

// ---------- board rendering ----------

function renderAll() {
  renderProjectFilterOptions();
  renderQuickAddOptions();
  updateModeUI();

  const visible = topLevelTasks().filter(matchesFilter);

  const inboxTasks = visible
    .filter((t) => t.status === "queued" && t.lane == null)
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0));

  const upNextTasks = visible
    .filter((t) => t.status === "queued" && t.lane === "serial")
    .sort((a, b) => {
      const pa = projectLabel(a.cwd);
      const pb = projectLabel(b.cwd);
      if (pa !== pb) return pa < pb ? -1 : 1;
      return (a.position ?? 0) - (b.position ?? 0);
    });

  const runningAll = visible.filter((t) => t.status === "running");
  const runnerTasks = runningAll.filter((t) => t.run_mode === "serial");
  const parallelTasks = runningAll.filter((t) => t.run_mode !== "serial");

  const attentionTasks = visible
    .filter((t) => t.status === "interrupted" || t.status === "failed")
    .sort((a, b) => {
      const at = a.finished_at || a.created_at;
      const bt = b.finished_at || b.created_at;
      return at < bt ? 1 : at > bt ? -1 : 0;
    });

  const doneAll = visible
    .filter((t) => t.status === "done" || t.status === "cancelled")
    .sort((a, b) => {
      const aw = isWaiting(a);
      const bw = isWaiting(b);
      if (aw !== bw) return aw ? -1 : 1;
      const at = a.finished_at || a.created_at;
      const bt = b.finished_at || b.created_at;
      return at < bt ? 1 : at > bt ? -1 : 0;
    });

  inboxOrderIds = inboxTasks.map((t) => t.id);
  upnextOrderIds = upNextTasks.map((t) => t.id);

  multiProject = projectActivity().active.length > 1;

  el.inboxCount.textContent = String(inboxTasks.length);
  el.upnextCount.textContent = String(upNextTasks.length);
  el.runningCount.textContent = String(runningAll.length);
  el.attentionCount.textContent = String(attentionTasks.length);
  el.doneCount.textContent = String(doneAll.length);

  updateTabCounts(inboxTasks.length, upNextTasks.length, runningAll.length, attentionTasks.length, doneAll.length);

  // Empty columns give their width to the ones with work in them.
  document.getElementById("col-inbox").classList.toggle("is-empty", inboxTasks.length === 0);
  document.getElementById("col-upnext").classList.toggle("is-empty", upNextTasks.length === 0);
  document.getElementById("col-running").classList.toggle("is-empty", runningAll.length === 0);
  document.getElementById("col-attention").classList.toggle("is-empty", attentionTasks.length === 0);
  document.getElementById("col-attention").classList.toggle("has-attn", attentionTasks.length > 0);
  document.getElementById("col-done").classList.toggle("is-empty", doneAll.length === 0);

  el.inboxEmpty.hidden = inboxTasks.length !== 0;
  reconcileList(el.inboxList, inboxTasks, (t) => t.id, createCardFor("inbox"), updateCardFor("inbox"));
  applyTints(el.inboxList);

  el.upnextEmpty.hidden = upNextTasks.length !== 0;
  reconcileList(el.upnextList, upNextTasks, (t) => t.id, createCardFor("upnext"), updateCardFor("upnext"));
  applyTints(el.upnextList);

  // The queue section only shows while there is a queue to speak of: a card
  // running from it, or cards waiting in Up next. Otherwise Running is one
  // plain list and needs no section labels at all.
  const showRunner = runnerTasks.length > 0 || upNextTasks.length > 0;
  el.runnerLabel.hidden = !showRunner;
  el.runnerEmpty.hidden = !showRunner || runnerTasks.length !== 0;
  el.parallelLabel.hidden = !showRunner;
  reconcileList(el.runnerList, runnerTasks, (t) => t.id, createCardFor("runner"), updateCardFor("runner"));
  applyTints(el.runnerList);

  el.parallelEmpty.hidden = parallelTasks.length !== 0 || showRunner;
  reconcileList(el.parallelList, parallelTasks, (t) => t.id, createCardFor("parallel"), updateCardFor("parallel"));
  applyTints(el.parallelList);

  el.attentionEmpty.hidden = attentionTasks.length !== 0;
  reconcileList(el.attentionList, attentionTasks, (t) => t.id, createCardFor("attention"), updateCardFor("attention"));
  applyTints(el.attentionList);

  const doneVisible = doneAll.slice(0, doneShown);
  el.doneEmpty.hidden = doneAll.length !== 0;
  reconcileList(el.doneList, doneVisible, (t) => t.id, createCardFor("done"), updateCardFor("done"));
  applyTints(el.doneList);
  const older = doneAll.length - doneShown;
  el.doneShowMore.hidden = older <= 0;
  el.doneShowMore.textContent = older > 0 ? `Show ${older} older` : "Show older";
  el.doneShowMore.onclick = () => {
    doneShown += DONE_PAGE_SIZE;
    renderAll();
  };

  renderLaneBanners();

  const noTasksAtAll = state.tasks.length > 0 && visible.length === 0 && projectFilter;
  el.filterEmpty.hidden = !noTasksAtAll;
  if (noTasksAtAll) {
    el.filterEmptyProject.textContent = projectLabel(projectFilter);
  }
}

function updateTabCounts(inbox, upnext, running, attention, done) {
  const counts = { inbox, upnext, running, attention, done };
  for (const key of TAB_ORDER) {
    const countEl = document.getElementById(`tab-count-${key}`);
    if (countEl) countEl.textContent = String(counts[key]);
  }
  document.getElementById("tab-attention").classList.toggle("has-attn", attention > 0);
}

/**
 * Rebuilds a <select> from [{label, entries:[[value, text], ...]}] groups
 * (label null = ungrouped) only when the rendered options differ, so a
 * poll never resets an open dropdown or the user's selection.
 */
function syncGroupedOptions(select, groups) {
  const flat = [];
  for (const g of groups) for (const [v, t] of g.entries) flat.push([v, t, g.label || ""]);
  const opts = select.options;
  let same = opts.length === flat.length;
  for (let i = 0; same && i < flat.length; i++) {
    const parentLabel = opts[i].parentElement.tagName === "OPTGROUP" ? opts[i].parentElement.label : "";
    same = opts[i].value === flat[i][0] && opts[i].textContent === flat[i][1] && parentLabel === flat[i][2];
  }
  if (same) return false;
  select.textContent = "";
  for (const g of groups) {
    if (g.entries.length === 0) continue;
    const parent = g.label ? document.createElement("optgroup") : select;
    if (g.label) parent.label = g.label;
    for (const [v, t] of g.entries) {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = t;
      parent.appendChild(opt);
    }
    if (g.label) select.appendChild(parent);
  }
  return true;
}

function renderProjectFilterOptions() {
  if (document.activeElement === el.projectFilter) return;
  const { active, other } = projectActivity();
  const known = new Set([...active, ...other].map((e) => e.cwd));
  syncGroupedOptions(el.projectFilter, [
    { label: null, entries: [["", "All projects"]] },
    { label: "Active", entries: active.map((e) => [e.cwd, e.label]) },
    { label: "Other", entries: other.map((e) => [e.cwd, e.label]) },
  ]);
  if (!known.has(el.projectFilter.value)) el.projectFilter.value = "";
  projectFilter = el.projectFilter.value;
  const filtering = projectFilter !== "";
  el.filterChip.hidden = !filtering;
  el.filterChipLabel.textContent = filtering ? projectLabel(projectFilter) : "";
}

/**
 * One "send to" picker instead of project + session: live sessions first
 * (choosing one ties the task to it), then each active project as a plain
 * target for a fresh worker, then everything else under "Other".
 */
let pickerFilter = null;

function renderQuickAddOptions() {
  if (document.activeElement === el.quickAddTarget) return;
  // A new project filter re-aims the picker; otherwise the user's pick stands.
  if (pickerFilter !== projectFilter) {
    pickerFilter = projectFilter;
    el.quickAddTarget.value = "";
  }
  const { active, other } = projectActivity();
  const sessionEntries = [];
  for (const e of active) {
    for (const sess of e.sessions) sessionEntries.push([`s:${sess.id}`, `${sess.title || sess.id.slice(0, 8)} · ${e.label}`]);
  }
  syncGroupedOptions(el.quickAddTarget, [
    { label: "Active sessions", entries: sessionEntries },
    { label: "Projects", entries: active.map((e) => [`p:${e.cwd}`, e.label]) },
    { label: "Other", entries: other.map((e) => [`p:${e.cwd}`, e.label]) },
  ]);
  const values = Array.from(el.quickAddTarget.options, (o) => o.value);
  const current = el.quickAddTarget.value;
  if (values.includes(current) && current !== "") return;
  let pick = "";
  if (projectFilter) {
    const inFilter = active.find((e) => e.cwd === projectFilter);
    if (inFilter && inFilter.sessions.length) pick = `s:${inFilter.sessions[0].id}`;
    else if (values.includes(`p:${projectFilter}`)) pick = `p:${projectFilter}`;
  }
  if (!pick) {
    const recent = mostRecentActiveSession();
    if (recent) pick = `s:${recent.id}`;
  }
  if (!pick && values.length) pick = values[0];
  el.quickAddTarget.value = pick;
}

/** Resolves the picker's value into what the create-task call needs. */
function quickAddTarget() {
  const value = el.quickAddTarget.value || "";
  if (value.startsWith("s:")) {
    const sess = sessionsById.get(value.slice(2));
    return sess ? { cwd: sess.cwd, sessionId: sess.id } : { cwd: "", sessionId: "" };
  }
  if (value.startsWith("p:")) return { cwd: value.slice(2), sessionId: "" };
  return { cwd: "", sessionId: "" };
}

// ---------- permission mode (top bar) ----------

function loadPermissionMode() {
  try {
    const stored = window.sessionStorage.getItem(MODE_STORAGE_KEY);
    if (stored) permissionMode = stored;
  } catch {
    // sessionStorage unavailable; default stands
  }
}

function savePermissionMode(value) {
  permissionMode = value;
  try {
    window.sessionStorage.setItem(MODE_STORAGE_KEY, value);
  } catch {
    // best effort only
  }
}

function currentPermissionMode() {
  if (permissionMode === "bypassPermissions" && !state.config.allow_bypass) return "acceptEdits";
  return permissionMode;
}

function updateModeUI() {
  const bypassOption = el.modeSelect.querySelector('option[value="bypassPermissions"]');
  bypassOption.disabled = !state.config.allow_bypass;
  bypassOption.textContent = state.config.allow_bypass
    ? "bypassPermissions"
    : "bypassPermissions (set TASKY_ALLOW_BYPASS=1)";
  el.modeSelect.value = currentPermissionMode();
  const isBypass = el.modeSelect.value === "bypassPermissions";
  el.modeSelect.dataset.bypass = isBypass ? "1" : "0";
  el.bypassChip.hidden = !isBypass;
  el.modeHint.textContent = PERMISSION_HINTS[el.modeSelect.value] || "";
}

el.modeSelect.addEventListener("change", () => {
  savePermissionMode(el.modeSelect.value);
  updateModeUI();
});

// ---------- mutations ----------

async function updateTaskStatus(id, status) {
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { status });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function runAgain(id) {
  return updateTaskStatus(id, "queued");
}

async function backToInbox(id) {
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { status: "queued", lane: null });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function moveToInbox(id, beforeId) {
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { lane: null, before_id: beforeId });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function reorderUpNext(id, beforeId) {
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { before_id: beforeId });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function deleteTask(id) {
  try {
    await apiMutate("DELETE", `/api/tasks/${id}`);
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function runTask(id, mode) {
  try {
    await apiMutate("POST", `/api/tasks/${id}/run`, { permission_mode: currentPermissionMode(), mode });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function enqueueTask(id, beforeId) {
  try {
    await apiMutate("POST", `/api/tasks/${id}/enqueue`, { permission_mode: currentPermissionMode(), before_id: beforeId });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function resumeLane(cwd) {
  try {
    await apiMutate("PATCH", "/api/lanes", { cwd, paused: false });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function toggleAutoPull(sessionId, value) {
  try {
    await apiMutate("PATCH", `/api/sessions/${sessionId}`, { auto_pull: value });
    await refresh();
    refreshDrawerIfOpen();
  } catch (err) {
    handleApiError(err);
  }
}

async function importHistory() {
  try {
    const report = await apiMutate("POST", "/api/import", {});
    announce(`Imported ${report.tasks} tasks from ${report.files} files`);
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function copyResumeCommand(command, button, label) {
  try {
    await copyText(command);
    button.dataset.copied = "1";
    button.textContent = "Copied";
    announce("Resume command copied");
    window.setTimeout(() => {
      button.dataset.copied = "";
      if (button.isConnected) button.textContent = label;
    }, 2000);
  } catch {
    showError("Could not copy the resume command");
  }
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // fall through to fallback
    }
  }
  const input = el.clipboardFallback;
  input.value = text;
  input.hidden = false;
  input.select();
  document.execCommand("copy");
  input.hidden = true;
}

// ---------- drag and drop (pointer events; keyboard alternative below) ----------
//
// `drag` is the single source of truth for an in-progress move, pointer- or
// keyboard-driven. dragActive() gates the refresh loop and reconcileList so
// a poll landing mid-drag never fights the user for the card's position;
// the latest state is parked in `pendingState` and applied on drop/cancel.

let drag = null;

function dragActive() {
  return drag !== null;
}

function insertCardAtIndex(listEl, node, index) {
  const cards = Array.from(listEl.children).filter((c) => c !== node);
  const ref = cards[index] || null;
  listEl.insertBefore(node, ref);
}

function computeDropForPoint(x, y) {
  const hit = document.elementFromPoint(x, y);
  const columnEl = hit && hit.closest("[data-lane]");
  if (!columnEl) return null;
  const kind = columnEl.dataset.lane;
  if (kind !== "inbox" && kind !== "upnext") return null;
  const listEl = kind === "inbox" ? el.inboxList : el.upnextList;
  let candidates = Array.from(listEl.children).filter(
    (c) => c.classList.contains("card") && c !== drag.node,
  );
  if (kind === "upnext") {
    candidates = candidates.filter((c) => {
      const t = tasksById.get(Number(c.dataset.taskId));
      return t && t.cwd === drag.cwd;
    });
  }
  let index = candidates.length;
  let bestDist = Infinity;
  let bestI = -1;
  for (let i = 0; i < candidates.length; i++) {
    const r = candidates[i].getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const d = Math.hypot(x - cx, y - cy);
    if (d < bestDist) {
      bestDist = d;
      bestI = i;
    }
  }
  if (bestI !== -1) {
    const r = candidates[bestI].getBoundingClientRect();
    index = y < r.top + r.height / 2 ? bestI : bestI + 1;
  }
  return { kind, listEl, index, candidates };
}

function clearDropIndicator() {
  if (!drag) return;
  if (drag.dropLine && drag.dropLine.parentElement) drag.dropLine.remove();
  if (drag.dropColumnBody) drag.dropColumnBody.classList.remove("drop-target");
  drag.dropLine = null;
  drag.dropColumnBody = null;
}

function showDropIndicator(target) {
  clearDropIndicator();
  if (!target || !drag) return;
  drag.dropLine = document.createElement("div");
  drag.dropLine.className = "drop-line";
  const ref = target.candidates[target.index] || null;
  target.listEl.insertBefore(drag.dropLine, ref);
  const columnBody = target.listEl.closest(".column-body");
  if (columnBody) columnBody.classList.add("drop-target");
  drag.dropColumnBody = columnBody;
}

function beginLift() {
  drag.moved = true;
  const originSlot = document.createElement("div");
  originSlot.className = "card drag-origin drag-origin-placeholder";
  originSlot.style.minHeight = `${drag.height}px`;
  originSlot.setAttribute("aria-hidden", "true");
  drag.originSlot = originSlot;
  drag.sourceList.insertBefore(originSlot, drag.node);
  document.body.appendChild(drag.node);
  drag.node.classList.add("dragging");
  drag.node.style.width = `${drag.width}px`;
  const task = tasksById.get(drag.taskId);
  announce(`${task ? task.title : "Task"} picked up`);
}

function onPointerMove(evt) {
  if (!drag || evt.pointerId !== drag.pointerId) return;
  const dx = evt.clientX - drag.startX;
  const dy = evt.clientY - drag.startY;
  if (!drag.moved) {
    if (Math.hypot(dx, dy) < 4) return;
    beginLift();
  }
  drag.node.style.left = `${evt.clientX - drag.offsetX}px`;
  drag.node.style.top = `${evt.clientY - drag.offsetY}px`;
  const target = computeDropForPoint(evt.clientX, evt.clientY);
  drag.target = target;
  showDropIndicator(target);
}

function detachPointerListeners() {
  document.removeEventListener("pointermove", onPointerMove);
  document.removeEventListener("pointerup", onPointerUp);
  document.removeEventListener("pointercancel", onPointerUp);
}

function onPointerUp(evt) {
  if (!drag || evt.pointerId !== drag.pointerId) return;
  detachPointerListeners();
  const d = drag;
  if (!d.moved) {
    drag = null;
    return;
  }
  clearDropIndicator();
  finishPointerDrag(d, d.target);
}

async function commitDrop(taskId, sourceKind, targetKind, beforeId) {
  const permissionModeValue = currentPermissionMode();
  const task = tasksById.get(taskId);
  if (sourceKind === "inbox" && targetKind === "inbox") {
    await apiMutate("PATCH", `/api/tasks/${taskId}`, { before_id: beforeId });
  } else if (sourceKind === "inbox" && targetKind === "upnext") {
    await apiMutate("POST", `/api/tasks/${taskId}/enqueue`, { permission_mode: permissionModeValue, before_id: beforeId });
  } else if (sourceKind === "upnext" && targetKind === "upnext") {
    await apiMutate("PATCH", `/api/tasks/${taskId}`, { before_id: beforeId });
  } else if (sourceKind === "upnext" && targetKind === "inbox") {
    await apiMutate("PATCH", `/api/tasks/${taskId}`, { lane: null, before_id: beforeId });
  }
  return task;
}

function finishPointerDrag(d, target) {
  d.node.classList.remove("dragging");
  d.node.style.position = "";
  d.node.style.left = "";
  d.node.style.top = "";
  d.node.style.width = "";
  const task = tasksById.get(d.taskId);

  if (!target) {
    d.sourceList.insertBefore(d.node, d.originSlot);
    d.originSlot.remove();
    drag = null;
    announce("Move cancelled");
    resolvePendingState();
    return;
  }

  d.originSlot.remove();
  insertCardAtIndex(target.listEl, d.node, target.index);
  const beforeId = target.candidates[target.index] ? Number(target.candidates[target.index].dataset.taskId) : null;
  drag = null;

  const listName = target.kind === "inbox" ? "Inbox" : "Up next";
  const idx = Array.from(target.listEl.children).indexOf(d.node);
  const total = target.listEl.querySelectorAll(":scope > .card").length;
  announce(`${task ? task.title : "Task"} moved to position ${idx + 1} of ${total} in ${listName}`);

  commitDrop(d.taskId, d.kind, target.kind, beforeId)
    .then(() => refresh())
    .catch((err) => {
      handleApiError(err);
      return refresh();
    });

  resolvePendingState();
}

function resolvePendingState() {
  if (pendingState) {
    const next = pendingState;
    pendingState = null;
    applyState(next);
  }
}

document.addEventListener("pointerdown", (evt) => {
  const handle = evt.target.closest(".handle");
  if (!handle || handle.hidden || drag) return;
  if (evt.pointerType === "mouse" && evt.button !== 0) return;
  const card = handle.closest(".card");
  if (!card) return;
  const listEl = card.parentElement;
  const taskId = Number(handle.dataset.taskId);
  const task = tasksById.get(taskId);
  const rect = card.getBoundingClientRect();
  drag = {
    pointerId: evt.pointerId,
    taskId,
    kind: handle.dataset.lane,
    cwd: task ? task.cwd : null,
    node: card,
    sourceList: listEl,
    startX: evt.clientX,
    startY: evt.clientY,
    offsetX: evt.clientX - rect.left,
    offsetY: evt.clientY - rect.top,
    width: rect.width,
    height: rect.height,
    moved: false,
  };
  try {
    handle.setPointerCapture(evt.pointerId);
  } catch {
    // capture is best-effort; move/up still fire on document
  }
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);
});

// ---------- keyboard reorder ----------

function applyWorkingOrder(kind, ids) {
  const listEl = kind === "inbox" ? el.inboxList : el.upnextList;
  for (const id of ids) {
    const node = listEl.querySelector(`[data-key="${id}"]`);
    if (node) listEl.appendChild(node);
  }
}

function kbPickUp(taskId, kind, handle) {
  const task = tasksById.get(taskId);
  if (!task) return;
  const ids = kind === "inbox" ? [...inboxOrderIds] : [...idsForCwdInUpNext(task.cwd)];
  drag = { keyboard: true, taskId, kind, cwd: task.cwd, handle, ids };
  handle.classList.add("picked");
  announce(`${task.title} picked up`);
}

function kbMove(delta) {
  const ids = drag.ids;
  const i = ids.indexOf(drag.taskId);
  const j = i + delta;
  if (i === -1 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  applyWorkingOrder(drag.kind, ids);
  const task = tasksById.get(drag.taskId);
  announce(`${task.title} moved to position ${j + 1} of ${ids.length}`);
}

function kbSwitchLane(newKind) {
  if (!drag || drag.kind === newKind) return;
  const task = tasksById.get(drag.taskId);
  drag.kind = newKind;
  drag.ids =
    newKind === "inbox"
      ? inboxOrderIds.filter((id) => id !== drag.taskId).concat(drag.taskId)
      : idsForCwdInUpNext(task.cwd)
          .filter((id) => id !== drag.taskId)
          .concat(drag.taskId);
  applyWorkingOrder(newKind, drag.ids);
  const listName = newKind === "inbox" ? "Inbox" : "Up next";
  announce(`${task.title} moved to ${listName}, position ${drag.ids.length} of ${drag.ids.length}`);
}

async function kbCommit() {
  const d = drag;
  drag = null;
  d.handle.classList.remove("picked");
  const task = tasksById.get(d.taskId);
  const idx = d.ids.indexOf(d.taskId);
  const beforeId = idx >= 0 && idx < d.ids.length - 1 ? d.ids[idx + 1] : null;
  const wasQueuedElsewhere = task && task.lane === "serial" && d.kind === "inbox";
  try {
    if (d.kind === "inbox") {
      await apiMutate("PATCH", `/api/tasks/${d.taskId}`, {
        before_id: beforeId,
        ...(wasQueuedElsewhere ? { lane: null } : {}),
      });
    } else if (task && task.lane === "serial") {
      await apiMutate("PATCH", `/api/tasks/${d.taskId}`, { before_id: beforeId });
    } else {
      await apiMutate("POST", `/api/tasks/${d.taskId}/enqueue`, {
        permission_mode: currentPermissionMode(),
        before_id: beforeId,
      });
    }
    announce(`${task ? task.title : "Task"} dropped`);
    await refresh();
  } catch (err) {
    handleApiError(err);
    await refresh();
  }
  resolvePendingState();
}

function kbCancel() {
  if (!drag) return;
  drag.handle.classList.remove("picked");
  drag = null;
  renderAll();
  announce("Move cancelled");
  resolvePendingState();
}

document.addEventListener("keydown", (evt) => {
  const handle = evt.target.closest && evt.target.closest(".handle");
  if (handle && drag && drag.keyboard && drag.taskId === Number(handle.dataset.taskId)) {
    if (evt.key === "ArrowUp" || evt.key === "ArrowDown") {
      evt.preventDefault();
      kbMove(evt.key === "ArrowUp" ? -1 : 1);
    } else if (evt.key === "ArrowLeft" || evt.key === "ArrowRight") {
      evt.preventDefault();
      kbSwitchLane(evt.key === "ArrowLeft" ? "inbox" : "upnext");
    } else if (evt.key === " " || evt.key === "Enter") {
      evt.preventDefault();
      kbCommit();
    } else if (evt.key === "Escape") {
      evt.preventDefault();
      kbCancel();
    }
    return;
  }
  if (handle && !drag && (evt.key === " " || evt.key === "Enter")) {
    evt.preventDefault();
    kbPickUp(Number(handle.dataset.taskId), handle.dataset.lane, handle);
    return;
  }

  if (evt.key === "Escape" && isDrawerOpen()) {
    evt.preventDefault();
    closeDrawer();
    return;
  }

  // Global shortcuts, only outside text fields and outside an active drag.
  if (drag) return;
  const active = document.activeElement;
  const inField = active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT" || active.isContentEditable);
  if (inField) return;
  if (evt.key === "n") {
    evt.preventDefault();
    el.quickAddInput.focus();
  } else if (evt.key === "/") {
    evt.preventDefault();
    el.searchInput.focus();
    el.searchInput.select();
  }
});

// ---------- search ----------
//
// Searches the whole ledger on the server, not just the tasks the board holds,
// so an old answer can be read again here instead of asked for again.

let searchHitsById = new Map();
let searchTimer = null;
let searchSeq = 0;

function searchQuery() {
  return el.searchInput.value.trim();
}

function searchWords(query) {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

function setSearchMode(on) {
  el.searchResults.hidden = !on;
  el.board.hidden = on;
  el.tablist.hidden = on;
}

function scheduleSearch() {
  clearTimeout(searchTimer);
  const query = searchQuery();
  if (query.length < SEARCH_MIN_CHARS) {
    searchSeq += 1;
    searchHitsById = new Map();
    el.searchList.textContent = "";
    setSearchMode(false);
    return;
  }
  searchTimer = setTimeout(() => runSearch(query), SEARCH_DEBOUNCE_MS);
}

async function runSearch(query) {
  const seq = ++searchSeq;
  const params = new URLSearchParams({ q: query });
  if (projectFilter) params.set("cwd", projectFilter);
  let data;
  try {
    data = await apiGet(`/api/search?${params}`);
  } catch (err) {
    handleApiError(err);
    return;
  }
  // A slower reply to an older query must not overwrite a newer one.
  if (seq !== searchSeq) return;
  searchHitsById = new Map(data.tasks.map((t) => [t.id, t]));
  renderSearchResults(query, data.tasks, data.more);
}

function clearSearch() {
  el.searchInput.value = "";
  scheduleSearch();
}

function renderSearchResults(query, tasks, more) {
  setSearchMode(true);
  const where = projectFilter ? ` in ${projectLabel(projectFilter)}` : "";
  const count = tasks.length === 0 ? "No tasks" : `${tasks.length}${more ? "+" : ""} ${tasks.length === 1 ? "task" : "tasks"}`;
  el.searchHeading.textContent = `${count} matching “${query}”${where}`;
  announce(el.searchHeading.textContent);
  const words = searchWords(query);
  el.searchList.textContent = "";
  for (const task of tasks) el.searchList.appendChild(createSearchHit(task, words));
}

function createSearchHit(task, words) {
  const li = document.createElement("li");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "search-hit";
  btn.dataset.status = task.status;
  btn.addEventListener("click", () => openDrawer(task.id));

  const head = document.createElement("span");
  head.className = "search-hit-head";
  const glyph = document.createElement("span");
  glyph.className = "search-hit-glyph";
  glyph.setAttribute("aria-hidden", "true");
  glyph.textContent = STATUS_GLYPH[task.status] || "";
  const title = document.createElement("span");
  title.className = "search-hit-title";
  appendHighlighted(title, task.title, words);
  head.append(glyph, title);

  const metaParts = [STATUS_WORD[task.status] || task.status];
  if (task.cwd) metaParts.push(projectLabel(task.cwd));
  const time = taskTimeLabel(task);
  if (time) metaParts.push(time);
  const meta = document.createElement("span");
  meta.className = "search-hit-meta";
  meta.textContent = metaParts.join(" · ");

  btn.append(head, meta);
  const snippet = matchSnippet(task, words);
  if (snippet) {
    const line = document.createElement("span");
    line.className = "search-hit-snippet";
    const label = document.createElement("span");
    label.className = "search-hit-source";
    label.textContent = snippet.source;
    line.appendChild(label);
    appendHighlighted(line, snippet.text, words);
    btn.appendChild(line);
  }
  li.appendChild(btn);
  return li;
}

/** The passage around the first word found, from the prompt, then the reply. */
function matchSnippet(task, words) {
  const sources = [
    ["Prompt", task.body && task.body !== task.title ? task.body : ""],
    ["Reply", task.result || ""],
  ];
  for (const [source, raw] of sources) {
    const text = raw.replace(/\s+/g, " ").trim();
    const lower = text.toLowerCase();
    const at = words.map((w) => lower.indexOf(w)).filter((i) => i >= 0);
    if (at.length === 0) continue;
    const first = Math.min(...at);
    const start = Math.max(0, first - SNIPPET_BEFORE);
    const end = Math.min(text.length, first + SNIPPET_AFTER);
    const cut = `${start > 0 ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
    return { source, text: cut };
  }
  return null;
}

/** Appends text with every search word wrapped in <mark>, built as nodes, never as HTML. */
function appendHighlighted(parent, text, words) {
  const lower = text.toLowerCase();
  let pos = 0;
  while (pos < text.length) {
    let next = -1;
    let len = 0;
    for (const w of words) {
      const i = lower.indexOf(w, pos);
      if (i >= 0 && (next < 0 || i < next || (i === next && w.length > len))) {
        next = i;
        len = w.length;
      }
    }
    if (next < 0) break;
    if (next > pos) parent.appendChild(document.createTextNode(text.slice(pos, next)));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(next, next + len);
    parent.appendChild(mark);
    pos = next + len;
  }
  if (pos < text.length) parent.appendChild(document.createTextNode(text.slice(pos)));
}

el.searchForm.addEventListener("submit", (evt) => {
  evt.preventDefault();
  clearTimeout(searchTimer);
  const query = searchQuery();
  if (query.length >= SEARCH_MIN_CHARS) runSearch(query);
});
el.searchInput.addEventListener("input", scheduleSearch);
el.searchInput.addEventListener("keydown", (evt) => {
  if (evt.key === "Escape") {
    evt.preventDefault();
    evt.stopPropagation();
    if (el.searchInput.value) clearSearch();
    else el.searchInput.blur();
  }
});
el.searchClear.addEventListener("click", () => {
  clearSearch();
  el.searchInput.focus();
});

// ---------- counters, tablist, mobile relocation ----------

function setActiveTab(tab, focusHeading) {
  activeTab = tab;
  try {
    window.sessionStorage.setItem(TAB_STORAGE_KEY, tab);
  } catch {
    // best effort only
  }
  for (const key of TAB_ORDER) {
    const btn = document.getElementById(`tab-${key}`);
    const col = document.getElementById(`col-${key}`);
    const selected = key === tab;
    btn.setAttribute("aria-selected", String(selected));
    btn.tabIndex = selected ? 0 : -1;
    col.classList.toggle("active-tab", selected);
  }
  if (focusHeading) {
    const heading = document.getElementById(`${tab}-heading`);
    if (heading) heading.focus();
  }
}

function loadActiveTab() {
  try {
    const stored = window.sessionStorage.getItem(TAB_STORAGE_KEY);
    if (stored && TAB_ORDER.includes(stored)) activeTab = stored;
  } catch {
    // default stands
  }
}

el.tablist.addEventListener("click", (evt) => {
  const btn = evt.target.closest("[data-column]");
  if (!btn) return;
  setActiveTab(btn.dataset.column, true);
});

// ---------- settings popover (project filter + permission mode) ----------

function setSettingsOpen(open) {
  el.topbarExtra.hidden = !open;
  el.settingsToggle.setAttribute("aria-expanded", String(open));
  if (open) el.projectFilter.focus();
}

el.settingsToggle.addEventListener("click", (evt) => {
  evt.stopPropagation();
  setSettingsOpen(el.topbarExtra.hidden);
});

el.topbarExtra.addEventListener("click", (evt) => evt.stopPropagation());

document.addEventListener("click", () => {
  if (!el.topbarExtra.hidden) setSettingsOpen(false);
});

document.addEventListener("keydown", (evt) => {
  if (evt.key === "Escape" && !el.topbarExtra.hidden) {
    setSettingsOpen(false);
    el.settingsToggle.focus();
  }
});

// The sticky tablist and the drawer sit below the top bar, whose height
// depends on how the pill wraps; measure it instead of guessing.
if (typeof ResizeObserver === "function") {
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--topbar-h", `${el.topbar.offsetHeight}px`);
  }).observe(el.topbar);
}

// ---------- event wiring ----------

el.projectFilter.addEventListener("change", () => {
  projectFilter = el.projectFilter.value;
  doneShown = DONE_PAGE_SIZE;
  renderAll();
  scheduleSearch();
});

el.filterChip.addEventListener("click", () => {
  el.projectFilter.value = "";
  projectFilter = "";
  doneShown = DONE_PAGE_SIZE;
  renderAll();
  scheduleSearch();
});

el.quickAddInput.addEventListener("keydown", (evt) => {
  if (evt.key === "Enter" && !evt.shiftKey) {
    evt.preventDefault();
    el.quickAddForm.requestSubmit();
  } else if (evt.key === "Escape") {
    el.quickAddInput.blur();
  }
});

el.quickAddForm.addEventListener("submit", async (evt) => {
  evt.preventDefault();
  const body = el.quickAddInput.value.trim();
  if (!body) return;
  const { cwd, sessionId } = quickAddTarget();
  if (!cwd) {
    showError("Pick a project or session to send this task to");
    return;
  }
  try {
    await apiMutate("POST", "/api/tasks", {
      body,
      cwd,
      session_id: sessionId || undefined,
    });
    el.quickAddInput.value = "";
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
});

el.importHistory.addEventListener("click", importHistory);
el.filterReset.addEventListener("click", () => {
  el.projectFilter.value = "";
  projectFilter = "";
  renderAll();
});

el.accessCopy.addEventListener("click", () => {
  copyResumeCommand("tasky ui", el.accessCopy, "Copy command");
});

el.drawerClose.addEventListener("click", closeDrawer);
el.drawerBackdrop.addEventListener("click", closeDrawer);

// ---------- boot ----------

loadPermissionMode();
loadActiveTab();
setActiveTab(activeTab, false);
initToken();
if (token) {
  startPolling();
} else {
  showAccessState();
}
