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

const PERMISSION_LABELS = {
  default: "Default",
  acceptEdits: "Accept edits",
  plan: "Plan",
  bypassPermissions: "Bypass",
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
  toast: document.getElementById("toast"),
  toastText: document.getElementById("toast-text"),
  toastAction: document.getElementById("toast-action"),
  projectFilter: document.getElementById("project-filter"),
  topbarExtra: document.getElementById("topbar-extra"),
  modeSelect: document.getElementById("quick-add-mode"),
  bypassChip: document.getElementById("bypass-chip"),
  quickAddForm: document.getElementById("quick-add"),
  quickAddInput: document.getElementById("quick-add-input"),
  quickAddChips: document.getElementById("quick-add-chips"),
  quickAddTarget: document.getElementById("quick-add-target"),
  filterChip: document.getElementById("filter-chip"),
  filterChipLabel: document.getElementById("filter-chip-label"),
  settingsToggle: document.getElementById("settings-toggle"),
  tablist: document.getElementById("tablist"),
  hooksBanner: document.getElementById("hooks-banner"),
  hooksBannerText: document.getElementById("hooks-banner-text"),
  hooksBannerMore: document.getElementById("hooks-banner-more"),
  hooksBannerList: document.getElementById("hooks-banner-list"),
  hooksBannerDismiss: document.getElementById("hooks-banner-dismiss"),
  recaps: document.getElementById("recaps"),
  recapsToggle: document.getElementById("recaps-toggle"),
  recapsTitle: document.getElementById("recaps-title"),
  recapsList: document.getElementById("recaps-list"),
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
  drawerAreas: document.querySelector("#drawer .drawer-areas"),
  drawerEditOpen: document.querySelector("#drawer .drawer-edit-open"),
  drawerEdit: document.querySelector("#drawer .drawer-edit"),
  drawerEditTextarea: document.querySelector("#drawer .drawer-edit-textarea"),
  drawerEditSave: document.querySelector("#drawer .drawer-edit-save"),
  drawerEditCancel: document.querySelector("#drawer .drawer-edit-cancel"),
  drawerBody: document.querySelector("#drawer .drawer-body"),
  drawerMode: document.querySelector("#drawer .drawer-mode"),
  drawerModeSelect: document.querySelector("#drawer .drawer-mode-select"),
  drawerModeHint: document.querySelector("#drawer .drawer-mode-hint"),
  drawerTaskSection: document.querySelector("#drawer .drawer-task-section"),
  drawerResultWrap: document.querySelector("#drawer .drawer-result-wrap"),
  drawerResultDigest: document.querySelector("#drawer .drawer-result-digest"),
  drawerShowOriginal: document.querySelector("#drawer .drawer-show-original"),
  drawerResultText: document.querySelector("#drawer .drawer-result-text"),
  drawerChildren: document.querySelector("#drawer .drawer-children"),
  drawerChildrenWrap: document.querySelector("#drawer .drawer-children-wrap"),
  drawerSessionLine: document.querySelector("#drawer .drawer-session-line"),
  drawerRecapsWrap: document.querySelector("#drawer .drawer-recaps-wrap"),
  drawerRecaps: document.querySelector("#drawer .drawer-recaps"),
  drawerRecapsAfterWrap: document.querySelector("#drawer .drawer-recaps-after-wrap"),
  drawerRecapsAfter: document.querySelector("#drawer .drawer-recaps-after"),
  drawerFollowupsWrap: document.querySelector("#drawer .drawer-followups-wrap"),
  drawerFollowups: document.querySelector("#drawer .drawer-followups"),
  drawerFilesWrap: document.querySelector("#drawer .drawer-files-wrap"),
  drawerFiles: document.querySelector("#drawer .drawer-files"),
  drawerFilesCount: document.querySelector("#drawer .drawer-files-count"),
  drawerTokensWrap: document.querySelector("#drawer .drawer-tokens-wrap"),
  drawerTokens: document.querySelector("#drawer .drawer-tokens"),
  drawerTools: document.querySelector("#drawer .drawer-tools"),
  searchForm: document.getElementById("search"),
  searchInput: document.getElementById("search-input"),
  searchResults: document.getElementById("search-results"),
  searchHeading: document.getElementById("search-heading"),
  searchList: document.getElementById("search-list"),
  searchClear: document.getElementById("search-clear"),
  smartSearch: document.getElementById("smart-search"),
  smartBtn: document.getElementById("smart-search-btn"),
  smartLabel: document.getElementById("smart-search-label"),
  smartCancel: document.getElementById("smart-search-cancel"),
  smartStatus: document.getElementById("smart-search-status"),
  smartResults: document.getElementById("smart-results"),
  smartCost: document.getElementById("smart-cost"),
  smartEmpty: document.getElementById("smart-empty"),
  smartList: document.getElementById("smart-list"),
  tablist: document.getElementById("tablist"),
  historyToggle: document.getElementById("history-toggle"),
  history: document.getElementById("history"),
  historyHeading: document.getElementById("history-heading"),
  historyCompact: document.getElementById("history-compact"),
  historyCompactList: document.getElementById("history-compact-list"),
  historyScope: document.getElementById("history-scope"),
  historyFilter: document.getElementById("history-filter"),
  historyClose: document.getElementById("history-close"),
  historySyncRow: document.getElementById("history-sync-row"),
  historyModel: document.getElementById("history-model"),
  historySync: document.getElementById("history-sync"),
  historySyncStatus: document.getElementById("history-sync-status"),
  historyEmpty: document.getElementById("history-empty"),
  historyBody: document.getElementById("history-body"),
  historyTabs: document.getElementById("history-tabs"),
  historyTimeline: document.getElementById("history-timeline"),
  historyProblems: document.getElementById("history-problems"),
  historyDead: document.getElementById("history-dead"),
  historyMap: document.getElementById("history-map"),
  archToggle: document.getElementById("arch-toggle"),
  arch: document.getElementById("arch"),
  archHeading: document.getElementById("arch-heading"),
  archRepo: document.getElementById("arch-repo"),
  archFilter: document.getElementById("arch-filter"),
  archClose: document.getElementById("arch-close"),
  archActions: document.getElementById("arch-actions"),
  archScan: document.getElementById("arch-scan"),
  archScanLabel: document.getElementById("arch-scan-label"),
  archModel: document.getElementById("arch-model"),
  archMap: document.getElementById("arch-map"),
  archStatus: document.getElementById("arch-status"),
  archMeta: document.getElementById("arch-meta"),
  archEmpty: document.getElementById("arch-empty"),
  archEmptyTitle: document.getElementById("arch-empty-title"),
  archEmptyText: document.getElementById("arch-empty-text"),
  archBody: document.getElementById("arch-body"),
  archCount: document.getElementById("arch-count"),
  archAdd: document.getElementById("arch-add"),
  archNew: document.getElementById("arch-new"),
  archNoMatch: document.getElementById("arch-no-match"),
  archAreas: document.getElementById("arch-areas"),
  archUnplaced: document.getElementById("arch-unplaced"),
  archUnplacedNote: document.getElementById("arch-unplaced-note"),
  archUnplacedLists: document.getElementById("arch-unplaced-lists"),
  archSpecs: document.getElementById("arch-specs"),
  archSpecsCount: document.getElementById("arch-specs-count"),
  archSpecsList: document.getElementById("arch-specs-list"),
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
    if (err.name === "AbortError") throw err; // the caller cancelled; the server is fine
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

async function apiMutate(method, path, body, signal) {
  if (!(await ensureVerified())) throw new NotVerifiedError();
  const res = await doFetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Tasky-Token": token || "" },
    body: JSON.stringify(body ?? {}),
    signal,
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

// ---------- toast ----------
//
// One small, non-blocking note at the bottom with at most one action
// (Undo). A new toast replaces the old one; each goes away on its own.

const TOAST_MS = 6000;
let toastTimer = null;

function hideToast() {
  clearTimeout(toastTimer);
  toastTimer = null;
  el.toast.hidden = true;
  el.toastAction.onclick = null;
}

function showToast(message, actionLabel, onAction) {
  clearTimeout(toastTimer);
  el.toastText.textContent = message;
  el.toastAction.hidden = !actionLabel;
  el.toastAction.textContent = actionLabel || "";
  el.toastAction.onclick = () => {
    hideToast();
    if (onAction) onAction();
  };
  el.toast.hidden = false;
  toastTimer = setTimeout(hideToast, TOAST_MS);
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
  renderAll();
  if (historyOpen) loadHistory();
  if (archOpen) loadArch();
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

/** "14:32" in the viewer's locale; "" for a missing or broken stamp. */
function clockTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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

/**
 * A reply that ends with a question still wants an answer only while it is
 * the newest turn of its session: once the user typed the next prompt, the
 * question was answered, whatever the old reply says.
 */
function wantsAnswer(task) {
  return !!task.latest_in_session && isWaiting(task);
}

/** A finished turn whose question is still open in a live session: it needs the user. */
function asksUser(task) {
  if (task.status !== "done" || !wantsAnswer(task)) return false;
  const session = task.session_id ? sessionsById.get(task.session_id) : null;
  return !!session && session.state === "active";
}

/** Why a card sits in Needs attention, in the words its label shows. */
function attentionReason(task) {
  if (task.status === "failed") return "Failed";
  if (task.status === "interrupted") return "Stopped mid-work";
  if (asksUser(task)) return "Asked you";
  return "";
}

// ---------- number and time formats ----------

/** 950 / 1.2k / 18k / 1.2M: short enough for a card's meta row. */
function formatCount(n) {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1000) return String(Math.round(n));
  const [div, unit] = n < 1e6 ? [1e3, "k"] : [1e6, "M"];
  const v = n / div;
  const text = v < 10 ? v.toFixed(1).replace(/\.0$/, "") : String(Math.round(v));
  return `${text}${unit}`;
}

/** Wall time between two ISO stamps: "45s", "4m", "1h 12m"; "" when either is missing. */
function formatDuration(fromIso, toIso) {
  if (!fromIso || !toIso) return "";
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const secs = Math.round(ms / 1000);
  if (secs < 1) return ""; // an instant (slash command, import) says nothing
  if (secs < 60) return `${secs}s`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const rest = mins % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function tokenTotal(tokens) {
  if (!tokens) return 0;
  return (tokens.input || 0) + (tokens.output || 0) + (tokens.cache_read || 0) + (tokens.cache_write || 0);
}

/** The reply's first sentence, as plain text (the digest summary is already markdown-free). */
function firstSentence(text) {
  if (!text) return "";
  const match = text.match(/^.*?[.!?](?=\s|$)/);
  return (match ? match[0] : text).trim();
}

/** The compact facts row under a card title; zero and unknown values are left out. */
function cardMetaParts(task) {
  const parts = [];
  if (isTerminal(task.status)) {
    const duration = formatDuration(task.started_at, task.finished_at);
    if (duration) parts.push(duration);
  }
  const stats = task.stats;
  if (stats && stats.files > 0) parts.push(plural(stats.files, "file"));
  const out = stats && stats.tokens ? stats.tokens.output : 0;
  if (out > 0) parts.push(`${formatCount(out)} out`);
  const followups = (task.followups || []).length;
  if (followups > 0) parts.push(`+${followups} ${followups === 1 ? "msg" : "msgs"}`);
  return parts;
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
// A task is one short block: drag handle or running dot, status dot, a title
// of up to two lines with the reply's first sentence and a facts row
// (duration, files, tokens, follow-ups) under it, an optional "Needs
// answer" badge, mode and project tags, elapsed time, then the
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
  if (kind === "attention" && asksUser(task)) spoken.push("asked you a question");
  if (kind === "runner") spoken.push("queue runner");
  if (kind === "parallel" && task.run_mode === "fork") spoken.push(`fork of ${forkSourceLabel(task)}`);
  const areaNames = taskAreaNames(task);
  if (areaNames.length) spoken.push(`${areaNames.length === 1 ? "area" : "areas"} ${areaNames.join(", ")}`);
  titleBtn.setAttribute("aria-label", `${spoken.join(", ")}. Open details`);

  time.textContent = taskTimeLabel(task);

  // The project tag only earns its place when the board mixes projects.
  const showProject = multiProject && !projectFilter && !!task.cwd;
  projectTag.hidden = !showProject;
  projectTag.textContent = showProject ? projectLabel(task.cwd) : "";
  projectTag.title = showProject ? task.cwd : "";

  // A waiting task shows how it will run, unless that is the plain default.
  const modeTag = node.querySelector(".tag-mode");
  const mode = task.status === "queued" ? task.permission_mode : null;
  const showMode = !!mode && mode !== "default";
  modeTag.hidden = !showMode;
  modeTag.textContent = showMode ? PERMISSION_LABELS[mode] || mode : "";
  modeTag.title = showMode ? `Runs in ${mode}: ${PERMISSION_HINTS[mode] || ""}` : "";
  modeTag.dataset.mode = showMode ? mode : "";

  const digestEntry = getResultDigest(task);
  // In Needs attention the reason label already says "Asked you".
  const waiting = kind !== "attention" && wantsAnswer(task);
  answerBadge.hidden = !waiting;
  answerBadge.title = waiting ? "The result ends with a question for you" : "";

  const summaryEl = node.querySelector(".card-summary");
  const summary = digestEntry ? firstSentence(digestEntry.summary) : "";
  summaryEl.hidden = !summary;
  summaryEl.textContent = summary;

  renderCardMeta(node.querySelector(".card-meta"), task, kind);
  renderAreaChips(node.querySelector(".card-areas"), task.areas, 2);
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

/**
 * The muted row under a card title: in Needs attention it opens with the
 * reason the card is there, then duration, files, output tokens and
 * follow-ups, each only when known and non-zero.
 */
function renderCardMeta(metaEl, task, kind) {
  const reason = kind === "attention" ? attentionReason(task) : "";
  const parts = cardMetaParts(task);
  const key = `${reason}|${parts.join("|")}`;
  metaEl.hidden = !reason && parts.length === 0;
  if (metaEl.dataset.key === key) return;
  metaEl.dataset.key = key;
  metaEl.textContent = "";
  if (reason) {
    const label = document.createElement("span");
    label.className = "reason";
    label.dataset.reason = task.status === "done" ? "asked" : task.status;
    label.textContent = reason;
    metaEl.appendChild(label);
  }
  for (const part of parts) {
    const item = document.createElement("span");
    item.className = "meta-item";
    item.textContent = part;
    metaEl.appendChild(item);
  }
}

function taskAreaNames(task) {
  return (Array.isArray(task.areas) ? task.areas : []).map((a) => a && a.name).filter(Boolean);
}

/**
 * The areas a task edited, as small muted chips on one line: the first
 * `max`, then "+N" for the rest (all of them in the tooltip).
 */
function renderAreaChips(container, areas, max) {
  const names = (Array.isArray(areas) ? areas : []).map((a) => a && a.name).filter(Boolean);
  const key = `${max}|${names.join("|")}`;
  container.hidden = names.length === 0;
  if (container.dataset.key === key) return;
  container.dataset.key = key;
  container.textContent = "";
  container.title = names.length > max ? names.join(", ") : "";
  for (const name of names.slice(0, max)) {
    const chip = document.createElement("span");
    chip.className = "area-chip";
    chip.textContent = name;
    container.appendChild(chip);
  }
  if (names.length > max) {
    const more = document.createElement("span");
    more.className = "area-chip area-chip-more";
    more.textContent = `+${names.length - max}`;
    container.appendChild(more);
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
  const oldHooks = container.querySelector(".session-old-hooks");
  oldHooks.hidden = !hasOldHooks(session);
  oldHooks.title = oldHooks.hidden ? "" : oldHooksText(session);

  const toggle = container.querySelector(".auto-pull-toggle");
  toggle.checked = !!session.auto_pull;
  toggle.onchange = () => toggleAutoPull(session.id, toggle.checked);

  const compactBtn = container.querySelector(".copy-compact");
  const canCompact = session.state === "active" && !!session.compact_prompt;
  compactBtn.hidden = !canCompact;
  if (canCompact) {
    if (compactBtn.dataset.copied !== "1") compactBtn.textContent = "Copy /compact text";
    compactBtn.onclick = () => copyCompact(compactInstructions(session), compactBtn, "Copy /compact text");
  }

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
  } else if (kind === "attention" && task.status !== "done") {
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
  } else if (kind === "attention" && task.status !== "done") {
    // An "Asked you" card is a finished turn: the answer goes in its session,
    // so it gets Details and Delete only.
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
  // Delete only hides the task (the history sync still reads it), and the
  // toast that follows offers Undo, so there is nothing to confirm.
  deleteBtn.onclick = () => {
    closeOverflowMenu();
    deleteTask(task.id);
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
  return tasksById.get(taskId) || searchHitsById.get(taskId) || smartHitsById.get(taskId);
}

async function openTaskById(taskId) {
  if (!findTask(taskId)) {
    try {
      const task = await apiGet(`/api/tasks/${taskId}`);
      searchHitsById.set(task.id, task);
    } catch (err) {
      showError(`Task #${taskId} is no longer in the ledger`);
      return;
    }
  }
  openDrawer(taskId);
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
  renderAreaChips(el.drawerAreas, task.areas, 6);

  if (task.body && task.body !== task.title) {
    el.drawerBody.textContent = task.body;
    el.drawerBody.hidden = false;
  } else {
    el.drawerBody.textContent = "";
    el.drawerBody.hidden = true;
  }

  const canEdit = task.status === "queued" && task.lane == null;
  // Any task that has not started yet can still change how it will run.
  const canMode = task.status === "queued";
  el.drawerMode.hidden = !canMode;
  if (canMode) {
    syncBypassOption(el.drawerModeSelect);
    el.drawerModeSelect.value = task.permission_mode || "default";
    el.drawerModeHint.textContent = PERMISSION_HINTS[el.drawerModeSelect.value] || "";
    el.drawerModeSelect.onchange = () => setTaskPermissionMode(task.id, el.drawerModeSelect.value);
  }
  // The Task section only exists when it adds to the header: a body longer
  // than the title, or an edit or mode control.
  el.drawerTaskSection.hidden = el.drawerBody.hidden && !canEdit && !canMode;
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
  renderDrawerRecaps(task);
  renderDrawerFollowups(task);
  renderDrawerTokens(task.stats);
  renderDrawerFiles(task);
  renderSessionLine(el.drawerSessionLine, task);
}

function renderDrawerFollowups(task) {
  const followups = task.followups || [];
  el.drawerFollowupsWrap.hidden = followups.length === 0;
  el.drawerFollowups.textContent = "";
  for (const f of followups) {
    const li = document.createElement("li");
    li.className = "followup";
    const at = document.createElement("time");
    at.className = "followup-at";
    at.dateTime = f.at || "";
    at.textContent = clockTime(f.at);
    const text = document.createElement("p");
    text.className = "followup-text";
    text.textContent = f.text;
    li.append(at, text);
    el.drawerFollowups.appendChild(li);
  }
}

const TOKEN_ROWS = [
  ["output", "Output"],
  ["input", "Input"],
  ["cache_read", "Cache read"],
  ["cache_write", "Cache write"],
];

function renderDrawerTokens(stats) {
  const tokens = stats && stats.tokens;
  const tools = stats ? stats.tools || 0 : 0;
  el.drawerTokensWrap.hidden = !tokens && tools === 0;
  el.drawerTools.textContent = tools > 0 ? plural(tools, "tool call") : "";
  el.drawerTokens.textContent = "";
  el.drawerTokens.hidden = !tokens;
  if (!tokens) return;
  const rows = [...TOKEN_ROWS.map(([key, label]) => [label, tokens[key] || 0]), ["Total", tokenTotal(tokens)]];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "token-row";
    if (label === "Total") row.classList.add("is-total");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = formatCount(value);
    dd.title = value.toLocaleString();
    row.append(dt, dd);
    el.drawerTokens.appendChild(row);
  }
}

/**
 * Edited files are not in /api/state (they would bloat every poll), so the
 * drawer asks for them once it is open and fills the list if it is still
 * showing the same task when the answer lands.
 */
async function renderDrawerFiles(task) {
  const known = task.stats ? task.stats.files : 0;
  el.drawerFilesWrap.hidden = !(known > 0);
  el.drawerFiles.textContent = "";
  el.drawerFilesCount.textContent = known > 0 ? String(known) : "";
  if (!(known > 0)) return;
  let detail;
  try {
    detail = await apiGet(`/api/tasks/${task.id}`);
  } catch {
    return; // the count stays; the list is a nicety
  }
  if (drawerTaskId !== task.id || !Array.isArray(detail.files)) return;
  el.drawerFilesCount.textContent = String(detail.files.length);
  el.drawerFilesWrap.hidden = detail.files.length === 0;
  const prefix = task.cwd ? `${task.cwd.replace(/\/$/, "")}/` : "";
  for (const path of detail.files) {
    const li = document.createElement("li");
    li.className = "drawer-file";
    li.textContent = prefix && path.startsWith(prefix) ? path.slice(prefix.length) : path;
    li.title = path;
    el.drawerFiles.appendChild(li);
  }
}

// ---------- recaps ----------
//
// A recap is the summary Claude Code writes when the user comes back to a
// session after being away. They are the fastest way back into a thread, so
// they get a panel of their own above the board, a row in Done between the
// turns they sit between, and a place in the drawer of the turn they follow.

const DAY_MS = 24 * 60 * 60 * 1000;

/** When a task happened, for placing it on a timeline next to recaps. */
function taskMoment(task) {
  return task.finished_at || task.started_at || task.created_at;
}

/**
 * One recap as a low row: "Recap · 14:32" and the text. Collapsible rows
 * clamp the text to two lines until clicked; the drawer shows it whole.
 */
function createRecapRow(recap, label, collapsible, tag = "li") {
  const li = document.createElement(tag);
  li.className = "recap-row";
  li.dataset.key = `recap-${recap.id}`;
  const box = document.createElement(collapsible ? "button" : "div");
  box.className = "recap-box";
  if (collapsible) {
    box.type = "button";
    box.setAttribute("aria-expanded", "false");
    box.addEventListener("click", () => {
      box.setAttribute("aria-expanded", String(box.getAttribute("aria-expanded") !== "true"));
    });
  }
  const head = document.createElement("span");
  head.className = "recap-head";
  const mark = document.createElement("span");
  mark.className = "recap-mark";
  mark.textContent = label;
  const at = document.createElement("time");
  at.dateTime = recap.ts;
  at.title = new Date(recap.ts).toLocaleString();
  at.textContent = clockTime(recap.ts);
  head.append(mark, " · ", at);
  const text = document.createElement("span");
  text.className = "recap-text";
  text.textContent = recap.text;
  box.append(head, text);
  li.appendChild(box);
  return li;
}

/**
 * The recaps that frame one turn: the one Claude Code filed after it
 * (same prompt_id), and any written in its session between the previous
 * turn and this one's start (the user came back, read, then typed this).
 */
function recapsAroundTask(task) {
  if (!task.session_id) return [];
  const start = task.started_at || task.created_at;
  let prev = "";
  for (const t of state.tasks) {
    if (t.session_id !== task.session_id || t.id === task.id || t.parent_id != null) continue;
    const when = taskMoment(t);
    if (when < start && when > prev) prev = when;
  }
  const floor = prev || new Date(new Date(start).getTime() - DAY_MS).toISOString();
  return (state.recaps || [])
    .filter(
      (r) =>
        r.session_id === task.session_id &&
        ((task.prompt_id && r.prompt_id === task.prompt_id) || (r.ts <= start && r.ts > floor)),
    )
    .sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
}

/** Recaps written before the turn go above its result, the ones after it below. */
function renderDrawerRecaps(task) {
  const start = task.started_at || task.created_at;
  const recaps = recapsAroundTask(task);
  const before = recaps.filter((r) => r.ts <= start);
  const after = recaps.filter((r) => r.ts > start);
  el.drawerRecapsWrap.hidden = before.length === 0;
  el.drawerRecaps.textContent = "";
  for (const r of before) el.drawerRecaps.appendChild(createRecapRow(r, "Recap before this task", false));
  el.drawerRecapsAfterWrap.hidden = after.length === 0;
  el.drawerRecapsAfter.textContent = "";
  for (const r of after) el.drawerRecapsAfter.appendChild(createRecapRow(r, "Recap after this turn", false));
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

// ---------- Done, grouped by day and session ----------
//
// Done reads as a diary: a header per day, then one collapsible group per
// session that worked that day, newest first. Each session keeps one rail
// colour across every day it appears in, and its group header shows the same
// colour, so the headers double as the legend. Today's groups start open,
// older ones folded; whatever the user toggles is remembered per group.

const DONE_GROUPS_KEY = "tasky.doneGroups";
const DONE_GROUPS_KEPT = 300;
let doneGroupOpen = loadDoneGroupState();
let doneGroupSeq = 0;

function loadDoneGroupState() {
  try {
    const parsed = JSON.parse(readLocal(DONE_GROUPS_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveDoneGroupState() {
  // Keys start with the day, so the newest sort last; keep only those.
  const keys = Object.keys(doneGroupOpen).sort();
  for (const key of keys.slice(0, Math.max(0, keys.length - DONE_GROUPS_KEPT))) delete doneGroupOpen[key];
  writeLocal(DONE_GROUPS_KEY, JSON.stringify(doneGroupOpen));
}

/** "2026-09-24" in the viewer's own timezone, so "Today" means their today. */
function localDayKey(iso) {
  const d = iso ? new Date(iso) : new Date();
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function dayLabel(dayKey) {
  const today = localDayKey();
  if (dayKey === today) return "Today";
  if (dayKey === localDayKey(new Date(Date.now() - DAY_MS).toISOString())) return "Yesterday";
  const [y, m, d] = dayKey.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  const opts = { weekday: "short", month: "short", day: "numeric" };
  if (y !== new Date().getFullYear()) opts.year = "numeric";
  return date.toLocaleDateString([], opts);
}

/** What a session is called in a group header: its title, else its project. */
function sessionLabel(session, cwd) {
  if (session && session.title) return session.title;
  return projectLabel((session && session.cwd) || cwd);
}

/** Days (newest first), each with its session groups in order of their newest task. */
function buildDoneGroups(tasks) {
  const days = [];
  const byDay = new Map();
  for (const t of tasks) {
    const dayKey = localDayKey(taskMoment(t));
    let day = byDay.get(dayKey);
    if (!day) {
      day = { key: dayKey, groups: [], byGroup: new Map() };
      byDay.set(dayKey, day);
      days.push(day);
    }
    const sessionKey = t.session_id || `cwd:${t.cwd || ""}`;
    let group = day.byGroup.get(sessionKey);
    if (!group) {
      group = { key: `${dayKey}|${sessionKey}`, dayKey, sessionKey, sessionId: t.session_id, cwd: t.cwd, tasks: [] };
      day.byGroup.set(sessionKey, group);
      day.groups.push(group);
    }
    group.tasks.push(t);
  }
  return days;
}

/**
 * A group's rows: its task cards plus the recaps its session got that day,
 * newest first by when each happened. `floor` keeps recaps from reaching
 * past the oldest task on the page while older ones are still paged out.
 */
function doneGroupItems(group, floor) {
  const items = group.tasks.map((t) => ({ task: t, at: taskMoment(t) }));
  if (group.sessionId) {
    for (const r of state.recaps || []) {
      if (r.session_id !== group.sessionId || localDayKey(r.ts) !== group.dayKey) continue;
      if (floor && r.ts < floor) continue;
      items.push({ recap: r, at: r.ts });
    }
  }
  // Stable sort: tasks with equal stamps keep Done's own order.
  return items.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}

function renderDoneGroups(tasks, hasOlder) {
  const days = buildDoneGroups(tasks);
  const tints = new Map();
  for (const day of days) {
    for (const g of day.groups) if (!tints.has(g.sessionKey)) tints.set(g.sessionKey, tints.size % TINT_COUNT);
  }
  const floor = hasOlder && tasks.length ? taskMoment(tasks[tasks.length - 1]) : "";
  const today = localDayKey();
  const update = (node, day) => updateDoneDay(node, day, { tints, floor, today });
  reconcileList(el.doneList, days, (d) => `day-${d.key}`, (day) => {
    const node = createDoneDay();
    update(node, day);
    return node;
  }, update);
}

function createDoneDay() {
  const section = document.createElement("section");
  section.className = "done-day";
  const head = document.createElement("h3");
  head.className = "done-day-head";
  const groups = document.createElement("div");
  groups.className = "done-day-groups";
  section.append(head, groups);
  return section;
}

function updateDoneDay(node, day, ctx) {
  node.querySelector(".done-day-head").textContent = dayLabel(day.key);
  const update = (groupNode, g) => updateDoneGroup(groupNode, g, ctx);
  reconcileList(node.querySelector(".done-day-groups"), day.groups, (g) => `grp-${g.key}`, (g) => {
    const groupNode = createDoneGroup();
    update(groupNode, g);
    return groupNode;
  }, update);
}

function createDoneGroup() {
  const node = document.createElement("div");
  node.className = "done-group";
  const listId = `done-group-${++doneGroupSeq}`;
  const heading = document.createElement("h4");
  heading.className = "done-group-heading";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "done-group-toggle";
  btn.setAttribute("aria-controls", listId);
  btn.innerHTML =
    '<svg class="chevron" aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>' +
    '<span class="swatch" aria-hidden="true"></span>' +
    '<span class="done-group-title"></span>' +
    '<span class="old-hooks-mark" hidden>older tasky</span>' +
    '<span class="done-group-facts"></span>';
  heading.appendChild(btn);
  const list = document.createElement("div");
  list.className = "card-list done-group-list";
  list.id = listId;
  btn.addEventListener("click", () => {
    const open = btn.getAttribute("aria-expanded") !== "true";
    doneGroupOpen[node.dataset.groupKey] = open;
    saveDoneGroupState();
    btn.setAttribute("aria-expanded", String(open));
    list.hidden = !open;
  });
  node.append(heading, list);
  return node;
}

function updateDoneGroup(node, group, ctx) {
  node.dataset.groupKey = group.key;
  const tint = TINT_CLASSES[ctx.tints.get(group.sessionKey) || 0];
  node.classList.remove(...TINT_CLASSES);
  node.classList.add(tint);

  const session = group.sessionId ? sessionsById.get(group.sessionId) : null;
  const btn = node.querySelector(".done-group-toggle");
  const list = node.querySelector(".done-group-list");
  const stored = doneGroupOpen[group.key];
  const open = typeof stored === "boolean" ? stored : group.dayKey === ctx.today;
  btn.setAttribute("aria-expanded", String(open));
  list.hidden = !open;

  const title = sessionLabel(session, group.cwd);
  node.querySelector(".done-group-title").textContent = title;
  btn.title = session && session.cwd ? `${title} · ${session.cwd}` : group.cwd || title;
  const facts = [plural(group.tasks.length, "task")];
  const out = session && session.tokens ? session.tokens.output : 0;
  if (out > 0) facts.push(`${formatCount(out)} out`);
  node.querySelector(".done-group-facts").textContent = facts.join(" · ");
  const oldMark = node.querySelector(".old-hooks-mark");
  const old = hasOldHooks(session);
  oldMark.hidden = !old;
  oldMark.title = old ? oldHooksText(session) : "";

  reconcileList(
    list,
    doneGroupItems(group, ctx.floor),
    (item) => (item.task ? item.task.id : `recap-${item.recap.id}`),
    (item) => (item.task ? createCard(item.task, "done") : createRecapRow(item.recap, "Recap", true, "div")),
    (itemNode, item) => {
      if (item.task) updateCard(itemNode, item.task, "done");
    },
  );
  for (const card of list.querySelectorAll(":scope > .card")) {
    card.classList.remove(...TINT_CLASSES);
    card.classList.add("tinted", tint);
  }
}

// ---------- old hooks ----------

/** A live session whose hooks predate this server: it still records the old, wrong way. */
function hasOldHooks(session) {
  return !!session && session.state === "active" && session.hook_version !== state.version;
}

function oldHooksText(session) {
  const ran = session.hook_version ? `v${session.hook_version}` : `before ${state.version}`;
  return `Running an older tasky (${ran}). Restart this Claude Code session to pick up the fixes.`;
}

let hooksBannerDismissed = false;
let hooksBannerKey = "";

/** One slim warning above the board while any live session still runs old hooks. */
function renderHooksBanner() {
  const stale = state.version ? state.sessions.filter(hasOldHooks) : [];
  el.hooksBanner.hidden = hooksBannerDismissed || stale.length === 0;
  if (el.hooksBanner.hidden) return;
  const names = stale.map((sess) => sessionLabel(sess, sess.cwd));
  const key = stale.map((sess) => `${sess.id}:${sess.hook_version}:${sess.title}`).join("|");
  if (key === hooksBannerKey) return;
  hooksBannerKey = key;
  const versions = new Set(stale.map((sess) => (sess.hook_version ? `v${sess.hook_version}` : `before ${state.version}`)));
  const who = stale.length === 1 ? "1 session is" : `${stale.length} sessions are`;
  const them = stale.length === 1 ? "it" : "them";
  el.hooksBannerText.textContent =
    `${who} running an older tasky (${[...versions].join(" / ")}). Restart ${them} to pick up the fixes.`;
  el.hooksBanner.title = names.join("\n");
  el.hooksBannerList.textContent = "";
  for (const sess of stale) {
    const li = document.createElement("li");
    // An untitled session is named by its project already; its id tells it apart.
    li.textContent = `${sessionLabel(sess, sess.cwd)} · ${sess.title ? projectLabel(sess.cwd) : sess.id.slice(0, 8)}`;
    li.title = sess.cwd || "";
    el.hooksBannerList.appendChild(li);
  }
}

el.hooksBannerMore.addEventListener("click", () => {
  const open = el.hooksBannerList.hidden;
  el.hooksBannerList.hidden = !open;
  el.hooksBannerMore.setAttribute("aria-expanded", String(open));
  el.hooksBannerMore.textContent = open ? "Hide" : "Show which";
});

el.hooksBannerDismiss.addEventListener("click", () => {
  hooksBannerDismissed = true;
  el.hooksBanner.hidden = true;
});

// ---------- latest recap panel ----------

const RECAPS_OPEN_KEY = "tasky.recapsOpen";
const RECAPS_SHOWN = 3;
let recapsPanelKey = "";

/** "just now", "12 min ago", "3 h ago", "2 days ago". */
function relativeAgo(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/**
 * The newest recap of each live session (up to three, newest first); when
 * no live session has one, the single newest recap there is. Honours the
 * project filter like the columns do.
 */
function latestRecaps() {
  const recaps = (state.recaps || []).filter((r) => !projectFilter || r.cwd === projectFilter);
  const seen = new Set();
  const picked = [];
  for (const r of recaps) {
    const session = sessionsById.get(r.session_id);
    if (!session || session.state !== "active" || seen.has(r.session_id)) continue;
    seen.add(r.session_id);
    picked.push(r);
    if (picked.length === RECAPS_SHOWN) break;
  }
  if (picked.length === 0 && recaps.length > 0) picked.push(recaps[0]);
  return picked;
}

/** The task a recap leads back to: its session's newest turn on the board. */
function latestTaskOfSession(sessionId) {
  let best = null;
  for (const t of state.tasks) {
    if (t.session_id !== sessionId || t.parent_id != null) continue;
    if (t.latest_in_session) return t;
    if (!best || taskMoment(t) > taskMoment(best)) best = t;
  }
  return best;
}

function recapsOpen() {
  return readLocal(RECAPS_OPEN_KEY) !== "0";
}

function renderRecapPanel() {
  const recaps = latestRecaps();
  el.recaps.hidden = recaps.length === 0;
  if (recaps.length === 0) return;
  const open = recapsOpen();
  el.recapsToggle.setAttribute("aria-expanded", String(open));
  el.recapsList.hidden = !open;
  el.recapsTitle.textContent = recaps.length === 1 ? "Latest recap" : "Latest recaps";
  const key = recaps.map((r) => r.id).join(",") + (multiProject ? "|m" : "");
  if (key !== recapsPanelKey) {
    recapsPanelKey = key;
    el.recapsList.textContent = "";
    for (const r of recaps) el.recapsList.appendChild(createRecapCard(r));
  }
  // Relative times move on without the recaps changing.
  for (const at of el.recapsList.querySelectorAll("time")) at.textContent = relativeAgo(at.dateTime);
}

function createRecapCard(recap) {
  const session = sessionsById.get(recap.session_id);
  const li = document.createElement("li");
  li.className = "recap-card";
  const target = latestTaskOfSession(recap.session_id);
  const box = document.createElement(target ? "button" : "div");
  box.className = "recap-card-box";
  if (target) {
    box.type = "button";
    box.title = "Open this session's latest task";
    box.addEventListener("click", () => openDrawer(target.id));
  }
  const head = document.createElement("span");
  head.className = "recap-card-head";
  const title = document.createElement("span");
  title.className = "recap-card-session";
  title.textContent = sessionLabel(session, recap.cwd);
  head.appendChild(title);
  if (session && session.title) {
    const project = document.createElement("span");
    project.className = "recap-card-project";
    project.textContent = projectLabel(session.cwd || recap.cwd);
    head.appendChild(project);
  }
  if (hasOldHooks(session)) {
    const mark = document.createElement("span");
    mark.className = "old-hooks-mark";
    mark.textContent = "older tasky";
    mark.title = oldHooksText(session);
    head.appendChild(mark);
  }
  const at = document.createElement("time");
  at.className = "recap-card-time";
  at.dateTime = recap.ts;
  at.title = new Date(recap.ts).toLocaleString();
  at.textContent = relativeAgo(recap.ts);
  head.appendChild(at);
  const text = document.createElement("span");
  text.className = "recap-card-text";
  text.textContent = recap.text;
  box.append(head, text);
  li.appendChild(box);
  return li;
}

el.recapsToggle.addEventListener("click", () => {
  const open = !recapsOpen();
  writeLocal(RECAPS_OPEN_KEY, open ? "1" : "0");
  el.recapsToggle.setAttribute("aria-expanded", String(open));
  el.recapsList.hidden = !open;
});

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
    .filter((t) => t.status === "interrupted" || t.status === "failed" || asksUser(t))
    .sort((a, b) => {
      const at = a.finished_at || a.created_at;
      const bt = b.finished_at || b.created_at;
      return at < bt ? 1 : at > bt ? -1 : 0;
    });

  const doneAll = visible
    .filter((t) => (t.status === "done" || t.status === "cancelled") && !asksUser(t))
    .sort((a, b) => {
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
  for (const key of ["inbox", "upnext", "running", "attention"]) {
    const col = document.getElementById(`col-${key}`);
    col.classList.toggle("is-collapsed", col.classList.contains("is-empty"));
  }

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
  const older = doneAll.length - doneShown;
  renderDoneGroups(doneVisible, older > 0);
  el.doneShowMore.hidden = older <= 0;
  el.doneShowMore.textContent = older > 0 ? `Show ${older} older` : "Show older";
  el.doneShowMore.onclick = () => {
    doneShown += DONE_PAGE_SIZE;
    renderAll();
  };

  renderLaneBanners();
  renderHooksBanner();
  renderRecapPanel();

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

// ---------- permission mode (composer) ----------
//
// Picked when a task is written, stored on the task, and used by every run
// of it: the run buttons never ask again. The last pick is remembered for
// the next task.

function loadPermissionMode() {
  const stored = readLocal(MODE_STORAGE_KEY);
  if (stored && PERMISSION_LABELS[stored]) permissionMode = stored;
}

function savePermissionMode(value) {
  permissionMode = value;
  writeLocal(MODE_STORAGE_KEY, value);
}

function currentPermissionMode() {
  if (permissionMode === "bypassPermissions" && !state.config.allow_bypass) return "acceptEdits";
  return permissionMode;
}

/** Bypass is offered only when the server allows it (TASKY_ALLOW_BYPASS=1). */
function syncBypassOption(select) {
  const bypassOption = select.querySelector('option[value="bypassPermissions"]');
  bypassOption.hidden = !state.config.allow_bypass;
  bypassOption.disabled = !state.config.allow_bypass;
}

function updateModeUI() {
  syncBypassOption(el.modeSelect);
  el.modeSelect.value = currentPermissionMode();
  const isBypass = el.modeSelect.value === "bypassPermissions";
  el.modeSelect.dataset.bypass = isBypass ? "1" : "0";
  el.modeSelect.title = `Permission mode for new tasks: ${PERMISSION_HINTS[el.modeSelect.value] || ""}`;
  el.bypassChip.hidden = !isBypass;
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

async function setTaskPermissionMode(id, mode) {
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { permission_mode: mode });
    announce(`Permission mode set to ${PERMISSION_LABELS[mode] || mode}`);
    await refresh();
    refreshDrawerIfOpen();
  } catch (err) {
    handleApiError(err);
    refreshDrawerIfOpen();
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
    if (drawerTaskId === id) closeDrawer();
    showToast("Task deleted", "Undo", () => restoreTask(id));
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function restoreTask(id) {
  try {
    await apiMutate("POST", `/api/tasks/${id}/restore`, {});
    announce("Task restored");
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function runTask(id, mode) {
  try {
    await apiMutate("POST", `/api/tasks/${id}/run`, { mode });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function enqueueTask(id, beforeId) {
  try {
    await apiMutate("POST", `/api/tasks/${id}/enqueue`, { before_id: beforeId });
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

/** While a card is up, folded Inbox / Up next strips open to take it. */
function setDraggingClass(on) {
  document.body.classList.toggle("is-dragging", on);
}

function beginLift() {
  drag.moved = true;
  setDraggingClass(true);
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
  const task = tasksById.get(taskId);
  if (sourceKind === "inbox" && targetKind === "inbox") {
    await apiMutate("PATCH", `/api/tasks/${taskId}`, { before_id: beforeId });
  } else if (sourceKind === "inbox" && targetKind === "upnext") {
    await apiMutate("POST", `/api/tasks/${taskId}/enqueue`, { before_id: beforeId });
  } else if (sourceKind === "upnext" && targetKind === "upnext") {
    await apiMutate("PATCH", `/api/tasks/${taskId}`, { before_id: beforeId });
  } else if (sourceKind === "upnext" && targetKind === "inbox") {
    await apiMutate("PATCH", `/api/tasks/${taskId}`, { lane: null, before_id: beforeId });
  }
  return task;
}

function finishPointerDrag(d, target) {
  setDraggingClass(false);
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
  setDraggingClass(true);
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
  setDraggingClass(false);
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
      await apiMutate("POST", `/api/tasks/${d.taskId}/enqueue`, { before_id: beforeId });
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
  setDraggingClass(false);
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

// ---------- project history ----------
//
// What a model distilled from a repo's tasks: milestones, and problems with
// the chain of attempts that led to (or away from) a fix. Reading it is free;
// only Sync spends tokens, with the model picked next to the button.

const HISTORY_VIEWS = ["timeline", "problems", "deadends", "map"];
const HISTORY_TAB_KEY = "tasky.historyTab";
const HISTORY_MODEL_KEY = "tasky.historyModel";
const HISTORY_DEFAULT_MODELS = ["sonnet", "opus"];
const HISTORY_MODEL_HINTS = { sonnet: "recommended", opus: "most thorough" };
const HISTORY_STATE_LABEL = { open: "Open", recurring: "Recurring", solved: "Solved" };
const HISTORY_OUTCOME = {
  worked: { glyph: "✓", word: "Worked" },
  failed: { glyph: "✗", word: "Failed" },
  partial: { glyph: "◐", word: "Partial" },
  pending: { glyph: "…", word: "Pending" },
};
const HISTORY_FLASH_MS = 1500;
const HISTORY_START_GRACE_MS = 4000;

let historyOpen = false;
let historyScope = ""; // a repo key, "all", or "" until the repo list is known
let historyScopeChosen = false;
let historyRepos = [];
let historyData = null;
let historySeq = 0;
let historyStarting = ""; // repo key a sync was just asked for, until the server reports it running
let historyStartTimer = null;
let historyView = "timeline";
let historyFilterText = "";
let historyRenderedKey = "";
let historyScopeOptionsKey = "";
let historyModelOptionsKey = "";

function readLocal(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // best effort only
  }
}

{
  const stored = readLocal(HISTORY_TAB_KEY);
  if (HISTORY_VIEWS.includes(stored)) historyView = stored;
}

function modelLabel(model) {
  if (!model) return "";
  return model.charAt(0).toUpperCase() + model.slice(1);
}

function plural(n, word) {
  return `${n} ${n === 1 ? word : `${word}s`}`;
}

/** A repo's display name, with its key when another recorded repo shares the name. */
function historyRepoLabel(key, fallbackName) {
  const repo = historyRepos.find((r) => r.repo === key);
  const name = (repo && repo.name) || fallbackName || key;
  const twins = historyRepos.filter((r) => r.name === name).length;
  return twins > 1 ? `${name} (${key})` : name;
}

function currentHistoryRepo() {
  if (!historyScope || historyScope === "all") return null;
  return historyRepos.find((r) => r.repo === historyScope) || null;
}

/** The repo the board is filtered to, else the most recently active one. */
function defaultHistoryScope(repos) {
  if (!repos.length) return "all";
  if (projectFilter) {
    const hit = repos.find((r) => Array.isArray(r.cwds) && r.cwds.includes(projectFilter));
    if (hit) return hit.repo;
  }
  return repos[0].repo;
}

/** An "all" reply narrowed to one repo, so the first load needs no second round trip. */
function scopeHistoryData(data, scope) {
  if (scope === "all" || data.scope === scope) return data;
  return {
    ...data,
    scope,
    milestones: (data.milestones || []).filter((m) => m.repo === scope),
    problems: (data.problems || []).filter((p) => p.repo === scope),
  };
}

function openHistory() {
  if (el.searchInput.value) clearSearch();
  if (archOpen) closeArch(false);
  historyOpen = true;
  el.historyToggle.setAttribute("aria-pressed", "true");
  el.history.hidden = false;
  el.board.hidden = true;
  el.tablist.hidden = true;
  if (!historyScopeChosen) historyScope = historyRepos.length ? defaultHistoryScope(historyRepos) : "";
  historyData = null;
  historyRenderedKey = "";
  renderHistory();
  loadHistory();
  el.historyHeading.focus();
}

function closeHistory(focusToggle = true) {
  historyOpen = false;
  el.historyToggle.setAttribute("aria-pressed", "false");
  el.history.hidden = true;
  el.board.hidden = false;
  el.tablist.hidden = false;
  if (focusToggle) el.historyToggle.focus();
}

async function loadHistory() {
  const seq = ++historySeq;
  const scope = historyScope;
  let data;
  try {
    data = await apiGet(`/api/history?${new URLSearchParams({ repo: scope || "all" })}`);
  } catch (err) {
    if (seq !== historySeq) return;
    if (scope && scope !== "all" && /\(404\)/.test(err.message)) {
      // The repo left the ledger; fall back to the default choice.
      historyScope = "";
      historyScopeChosen = false;
      loadHistory();
      return;
    }
    handleApiError(err);
    return;
  }
  // A reply for a scope switched away from, or older than a newer load, is dropped.
  if (seq !== historySeq || scope !== historyScope) return;
  historyRepos = Array.isArray(data.repos) ? data.repos : [];
  if (!historyScope || (historyScope !== "all" && !historyRepos.some((r) => r.repo === historyScope))) {
    historyScope = defaultHistoryScope(historyRepos);
  }
  if (data.scope !== historyScope) {
    if (data.scope !== "all") {
      loadHistory();
      return;
    }
    data = scopeHistoryData(data, historyScope);
  }
  data.milestones = Array.isArray(data.milestones) ? data.milestones : [];
  data.problems = Array.isArray(data.problems) ? data.problems : [];
  historyData = data;
  const repo = currentHistoryRepo();
  if (historyStarting && repo && repo.repo === historyStarting && repo.sync && repo.sync.state === "running") {
    clearHistoryStarting();
  }
  renderHistory();
}

function clearHistoryStarting() {
  historyStarting = "";
  clearTimeout(historyStartTimer);
  historyStartTimer = null;
}

function historyIsRunning(repo) {
  return Boolean(repo) && (historyStarting === repo.repo || Boolean(repo.sync && repo.sync.state === "running"));
}

async function startHistorySync() {
  const repo = currentHistoryRepo();
  if (!repo || historyIsRunning(repo)) return;
  const model = el.historyModel.value;
  historyStarting = repo.repo;
  renderHistorySync();
  try {
    await apiMutate("POST", "/api/history/sync", { repo: repo.repo, model });
    announce(`History sync started with ${modelLabel(model)}`);
  } catch (err) {
    clearHistoryStarting();
    renderHistorySync();
    handleApiError(err);
    loadHistory();
    return;
  }
  // The sync process claims the repo a moment after it starts; until the
  // server says so, keep showing it as starting rather than idle.
  clearTimeout(historyStartTimer);
  historyStartTimer = setTimeout(() => {
    historyStarting = "";
    historyStartTimer = null;
    loadHistory();
  }, HISTORY_START_GRACE_MS);
}

// ----- header, sync row, tabs -----

function renderHistoryScopeOptions() {
  const key = JSON.stringify(historyRepos.map((r) => [r.repo, r.name]));
  if (key !== historyScopeOptionsKey) {
    historyScopeOptionsKey = key;
    el.historyScope.textContent = "";
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "All repos";
    el.historyScope.appendChild(all);
    for (const r of historyRepos) {
      const opt = document.createElement("option");
      opt.value = r.repo;
      opt.textContent = historyRepoLabel(r.repo, r.name);
      el.historyScope.appendChild(opt);
    }
  }
  el.historyScope.value = historyScope || "all";
  el.historyScope.disabled = historyRepos.length === 0;
}

function historyModels() {
  const models = historyData && historyData.models;
  return Array.isArray(models) && models.length ? models : HISTORY_DEFAULT_MODELS;
}

function renderHistoryModelOptions() {
  const models = historyModels();
  const key = models.join(",");
  if (key !== historyModelOptionsKey) {
    historyModelOptionsKey = key;
    el.historyModel.textContent = "";
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m;
      const hint = HISTORY_MODEL_HINTS[m];
      opt.textContent = hint ? `${modelLabel(m)} · ${hint}` : modelLabel(m);
      el.historyModel.appendChild(opt);
    }
  }
  const stored = readLocal(HISTORY_MODEL_KEY);
  const fallback = historyData && historyData.default_model;
  el.historyModel.value = models.includes(stored) ? stored : models.includes(fallback) ? fallback : models[0];
}

/**
 * The instructions only, without "/compact": pasted text that long is collapsed
 * into "[Pasted text]" by Claude Code, so a leading slash command inside it is
 * never run. Type /compact, then paste this.
 */
function compactInstructions(session) {
  return session.compact_prompt.replace(/\s+/g, " ").trim();
}

/** Open sessions of the shown repo with a /compact the last sync suggested, newest first. */
function renderHistoryCompact(repo) {
  const cwds = new Set((repo && repo.cwds) || []);
  const sessions = (state.sessions || [])
    .filter((s) => s.state === "active" && s.compact_prompt && cwds.has(s.cwd))
    .sort((a, b) => ((a.compact_at || "") < (b.compact_at || "") ? 1 : -1));
  el.historyCompact.hidden = sessions.length === 0;
  const key = sessions.map((s) => `${s.id}:${s.compact_at}`).join("|");
  if (el.historyCompactList.dataset.key === key) return;
  el.historyCompactList.dataset.key = key;
  el.historyCompactList.textContent = "";
  for (const session of sessions) {
    const li = document.createElement("li");
    li.className = "history-compact-item";
    const head = document.createElement("div");
    head.className = "history-compact-head";
    const name = document.createElement("span");
    name.className = "history-compact-session";
    name.textContent = session.title || session.id.slice(0, 8);
    const when = document.createElement("span");
    when.className = "history-compact-when";
    when.textContent = relativeTime(session.compact_at);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm";
    btn.textContent = "Copy";
    btn.setAttribute("aria-label", `Copy the /compact text for ${name.textContent}`);
    btn.addEventListener("click", () => copyCompact(compactInstructions(session), btn, "Copy"));
    head.append(name, when, btn);
    const text = document.createElement("pre");
    text.className = "history-compact-text";
    text.textContent = compactInstructions(session);
    li.append(head, text);
    el.historyCompactList.appendChild(li);
  }
}

async function copyCompact(command, button, label) {
  try {
    await copyText(command);
    button.dataset.copied = "1";
    button.textContent = "Copied";
    announce("Copied: in that session type /compact, a space, then paste");
    window.setTimeout(() => {
      button.dataset.copied = "";
      if (button.isConnected) button.textContent = label;
    }, 2000);
  } catch {
    showError("Could not copy the /compact command");
  }
}

function renderHistorySync() {
  const repo = currentHistoryRepo();
  el.historySyncRow.hidden = !repo;
  renderHistoryCompact(repo);
  if (!repo) return;
  renderHistoryModelOptions();
  const running = historyIsRunning(repo);
  const pending = repo.pending || 0;
  el.historySync.disabled = running || pending === 0;
  el.historySync.textContent = running ? "Syncing…" : "Sync";
  el.historyModel.disabled = running;

  const status = el.historySyncStatus;
  const sync = repo.sync;
  const toRead = `${plural(pending, "task")} to read`;
  status.textContent = "";
  status.title = sync && sync.total_cost_usd ? `Spent on this repo so far: $${Number(sync.total_cost_usd).toFixed(4)}` : "";
  if (running) {
    const model = (sync && sync.state === "running" && sync.model) || el.historyModel.value;
    status.textContent = `Syncing… ${modelLabel(model)} is reading this repo's tasks.`;
    return;
  }
  if (!sync) {
    status.textContent = `Never synced · ${toRead} · spends tokens`;
    return;
  }
  const when = relativeTime(sync.finished_at || sync.started_at);
  const parts = [when && when !== "just now" ? `Synced ${when}` : "Synced just now"];
  if (sync.last_cost_usd) parts.push(`$${Number(sync.last_cost_usd).toFixed(4)}`);
  if (sync.model) parts.push(sync.model);
  parts.push(pending ? toRead : "up to date");
  status.appendChild(document.createTextNode(parts.join(" · ")));
  if (sync.error) {
    status.appendChild(document.createTextNode(" · "));
    const err = document.createElement("span");
    err.className = "history-sync-error";
    err.textContent = `stopped: ${sync.error}`;
    status.appendChild(err);
  }
}

function setHistoryView(view, focusTab) {
  if (!HISTORY_VIEWS.includes(view)) return;
  if (view !== historyView) writeLocal(HISTORY_TAB_KEY, view);
  historyView = view;
  for (const v of HISTORY_VIEWS) {
    const tab = document.getElementById(`history-tab-${v}`);
    const panel = document.getElementById(`history-panel-${v}`);
    const selected = v === view;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    panel.hidden = !selected;
  }
  if (focusTab) document.getElementById(`history-tab-${view}`).focus();
}

function renderHistory() {
  renderHistoryScopeOptions();
  renderHistorySync();
  const data = historyData;
  if (!data) {
    el.historyEmpty.hidden = true;
    el.historyBody.hidden = true;
    historyRenderedKey = "";
    return;
  }
  let emptyText = "";
  if (!historyRepos.length) {
    emptyText = "No repos recorded yet.";
  } else if (data.milestones.length + data.problems.length === 0) {
    const repo = currentHistoryRepo();
    if (!repo) emptyText = "No history for any repo yet. Pick a repo to sync it.";
    else if (repo.pending) emptyText = `No history for this repo yet. Sync reads its ${plural(repo.pending, "recorded task")} with the chosen model.`;
    else emptyText = "No history for this repo yet, and no recorded tasks to read.";
  }
  el.historyEmpty.textContent = emptyText;
  el.historyEmpty.hidden = !emptyText;
  el.historyBody.hidden = Boolean(emptyText);
  if (emptyText) {
    historyRenderedKey = "";
    return;
  }
  // Polls re-deliver the same history often; rebuilding it would drop focus
  // and scroll position for nothing.
  const key = JSON.stringify([data.scope, data.milestones, data.problems, historyFilterText]);
  if (key !== historyRenderedKey) {
    historyRenderedKey = key;
    renderHistoryViews();
  }
  setHistoryView(historyView, false);
}

// ----- filtering and ordering -----

function historyHaystack(...values) {
  return values.filter(Boolean).join("\n").toLowerCase();
}

function milestoneMatches(m) {
  return !historyFilterText || historyHaystack(m.title, m.detail, m.topic, m.name).includes(historyFilterText);
}

function attemptMatches(a) {
  return !historyFilterText || historyHaystack(a.description, a.why, a.evidence).includes(historyFilterText);
}

function problemMatches(p) {
  if (!historyFilterText) return true;
  if (historyHaystack(p.title, p.symptom, p.cause, p.topic, p.name).includes(historyFilterText)) return true;
  return (p.attempts || []).some(attemptMatches);
}

/** Newest first, undated last. Dates are ISO strings, so they compare as text. */
function compareDateDesc(a, b) {
  if (a === b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return a < b ? 1 : -1;
}

function sortProblems(problems) {
  const rank = (p) => (p.state === "solved" ? 1 : 0);
  return problems.slice().sort((a, b) => rank(a) - rank(b) || compareDateDesc(a.last_seen, b.last_seen) || b.id - a.id);
}

function collectDeadEnds(problems) {
  const out = [];
  for (const problem of problems) {
    for (const attempt of problem.attempts || []) {
      if (attempt.outcome === "failed") out.push({ attempt, problem });
    }
  }
  const when = (d) => d.attempt.invalidated_on || d.attempt.believed_from;
  return out.sort((a, b) => compareDateDesc(when(a), when(b)) || b.attempt.id - a.attempt.id);
}

function renderHistoryViews() {
  const data = historyData;
  const showRepo = data.scope === "all";
  const milestones = data.milestones.filter(milestoneMatches);
  const problems = sortProblems(data.problems.filter(problemMatches));
  const deadEnds = collectDeadEnds(problems);
  const filtered = Boolean(historyFilterText);

  renderTimeline(milestones, showRepo, filtered);
  renderProblems(problems, showRepo, filtered);
  renderDeadEnds(deadEnds, showRepo, filtered);
  renderMap(milestones, problems, showRepo, filtered);

  setHistoryCount("timeline", milestones.length);
  setHistoryCount("problems", problems.length);
  setHistoryCount("deadends", deadEnds.length);
  setHistoryCount("map", milestones.length + problems.length);
}

function setHistoryCount(view, n) {
  document.getElementById(`history-count-${view}`).textContent = String(n);
}

function setTabEmpty(view, text) {
  const node = document.getElementById(`history-empty-${view}`);
  node.textContent = text;
  node.hidden = !text;
}

function noMatchText(what) {
  return `No ${what} match “${el.historyFilter.value.trim()}”.`;
}

// ----- shared bits -----

function historyChip(text, className) {
  const chip = document.createElement("span");
  chip.className = `badge history-chip ${className || ""}`.trim();
  chip.textContent = text;
  return chip;
}

function historyLine(label, text, className) {
  const p = document.createElement("p");
  p.className = `history-line ${className || ""}`.trim();
  if (label) {
    const strong = document.createElement("span");
    strong.className = "history-line-label";
    strong.textContent = `${label} `;
    p.appendChild(strong);
  }
  p.appendChild(document.createTextNode(text));
  return p;
}

function historyDates(from, to) {
  if (!from && !to) return null;
  const p = document.createElement("p");
  p.className = "history-meta";
  const parts = [];
  if (from) parts.push(`believed from ${from}`);
  if (to) parts.push(`invalidated on ${to}`);
  p.textContent = parts.join(" → ");
  return p;
}

function historyEvidence(text) {
  const quote = document.createElement("blockquote");
  quote.className = "history-evidence";
  quote.textContent = text;
  return quote;
}

function shortCommit(commit) {
  return /^[0-9a-f]{8,40}$/i.test(commit) ? commit.slice(0, 7) : commit;
}

function historyRefs(taskIds, commits) {
  const ids = Array.isArray(taskIds) ? taskIds : [];
  const shas = Array.isArray(commits) ? commits : [];
  if (!ids.length && !shas.length) return null;
  const refs = document.createElement("div");
  refs.className = "history-refs";
  for (const id of ids) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-ghost btn-sm history-ref";
    btn.textContent = `#${id}`;
    btn.setAttribute("aria-label", `Open task ${id}`);
    btn.addEventListener("click", () => openTaskById(id));
    refs.appendChild(btn);
  }
  for (const sha of shas) {
    const code = document.createElement("code");
    code.className = "badge history-commit";
    code.textContent = shortCommit(sha);
    code.title = `Commit ${sha}`;
    refs.appendChild(code);
  }
  return refs;
}

function historyHead(title, level) {
  const head = document.createElement("div");
  head.className = "history-item-head";
  const h = document.createElement(level || "h3");
  h.className = "history-title";
  h.textContent = title;
  head.appendChild(h);
  return head;
}

function appendMaybe(parent, child) {
  if (child) parent.appendChild(child);
}

/** Switch tab, bring the item into view and flash it so the eye lands on it. */
function goToHistoryItem(view, id) {
  setHistoryView(view, false);
  const target = document.getElementById(id);
  if (!target) return;
  target.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
  target.focus({ preventScroll: true });
  target.classList.remove("is-flash");
  void target.offsetWidth; // restart the animation when flashed twice in a row
  target.classList.add("is-flash");
  setTimeout(() => target.classList.remove("is-flash"), HISTORY_FLASH_MS);
}

// ----- timeline -----

function renderTimeline(milestones, showRepo, filtered) {
  const list = el.historyTimeline;
  list.textContent = "";
  setTabEmpty("timeline", milestones.length ? "" : filtered ? noMatchText("milestones") : "No milestones recorded yet.");
  for (const m of milestones) list.appendChild(createMilestoneItem(m, showRepo));
}

function createMilestoneItem(m, showRepo) {
  const li = document.createElement("li");
  li.className = "timeline-item";
  li.id = `milestone-${m.id}`;
  li.tabIndex = -1;

  const when = document.createElement(m.happened_on ? "time" : "span");
  when.className = "history-date timeline-date";
  if (m.happened_on) when.dateTime = m.happened_on;
  when.textContent = m.happened_on || "undated";
  li.appendChild(when);

  const body = document.createElement("div");
  body.className = "timeline-body";
  const head = historyHead(m.title);
  if (m.topic) head.appendChild(historyChip(m.topic, "history-topic"));
  if (showRepo) head.appendChild(historyChip(historyRepoLabel(m.repo, m.name), "history-repo"));
  body.appendChild(head);
  if (m.detail) body.appendChild(historyLine("", m.detail));
  appendMaybe(body, historyRefs(m.task_ids, m.commits));
  li.appendChild(body);
  return li;
}

// ----- problems -----

function renderProblems(problems, showRepo, filtered) {
  const list = el.historyProblems;
  list.textContent = "";
  setTabEmpty("problems", problems.length ? "" : filtered ? noMatchText("problems") : "No problems recorded yet.");
  for (const p of problems) list.appendChild(createProblemCard(p, showRepo));
}

function createProblemCard(p, showRepo) {
  const li = document.createElement("li");
  li.className = "history-item problem-card";
  li.id = `problem-${p.id}`;
  li.dataset.state = p.state || "open";
  li.tabIndex = -1;

  const head = historyHead(p.title);
  head.appendChild(historyChip(HISTORY_STATE_LABEL[p.state] || "Open", `history-state history-state-${p.state || "open"}`));
  if (p.topic) head.appendChild(historyChip(p.topic, "history-topic"));
  if (showRepo) head.appendChild(historyChip(historyRepoLabel(p.repo, p.name), "history-repo"));
  li.appendChild(head);

  if (p.first_seen || p.last_seen) {
    const seen = document.createElement("p");
    seen.className = "history-meta";
    const parts = [];
    if (p.first_seen) parts.push(`first seen ${p.first_seen}`);
    if (p.last_seen && p.last_seen !== p.first_seen) parts.push(`last seen ${p.last_seen}`);
    seen.textContent = parts.join(" · ");
    li.appendChild(seen);
  }
  if (p.symptom) li.appendChild(historyLine("Symptom:", p.symptom));
  if (p.cause) li.appendChild(historyLine("Cause:", p.cause));

  const attempts = p.attempts || [];
  if (attempts.length) {
    const chain = document.createElement("ol");
    chain.className = "attempt-chain";
    chain.setAttribute("aria-label", `Attempts at “${p.title}”`);
    for (const a of attempts) chain.appendChild(createAttemptItem(a));
    li.appendChild(chain);
  }
  appendMaybe(li, historyRefs(p.task_ids, []));
  return li;
}

function createAttemptItem(a) {
  const outcome = HISTORY_OUTCOME[a.outcome] ? a.outcome : "pending";
  const li = document.createElement("li");
  li.className = "attempt";
  li.id = `attempt-${a.id}`;
  li.dataset.outcome = outcome;
  li.tabIndex = -1;

  const marker = document.createElement("span");
  marker.className = "attempt-marker";
  marker.setAttribute("aria-hidden", "true");
  marker.textContent = HISTORY_OUTCOME[outcome].glyph;
  li.appendChild(marker);

  const body = document.createElement("div");
  body.className = "attempt-body";
  const desc = document.createElement("p");
  desc.className = "attempt-desc";
  const word = document.createElement("span");
  word.className = "visually-hidden";
  word.textContent = `${HISTORY_OUTCOME[outcome].word}: `;
  desc.appendChild(word);
  desc.appendChild(document.createTextNode(a.description));
  if (a.source === "agent") {
    desc.appendChild(document.createTextNode(" "));
    const tag = historyChip("agent", "attempt-agent");
    tag.title = "Recorded by an agent while it worked, not by a sync";
    desc.appendChild(tag);
  }
  body.appendChild(desc);
  if (a.why) {
    const label = outcome === "failed" || outcome === "partial" ? "Why it failed:" : "Why:";
    body.appendChild(historyLine(label, a.why));
  }
  if (a.evidence) body.appendChild(historyEvidence(a.evidence));
  appendMaybe(body, historyDates(a.believed_from, a.invalidated_on));
  appendMaybe(body, historyRefs(a.task_ids, a.commits));
  li.appendChild(body);
  return li;
}

// ----- dead ends -----

function renderDeadEnds(deadEnds, showRepo, filtered) {
  const list = el.historyDead;
  list.textContent = "";
  setTabEmpty("deadends", deadEnds.length ? "" : filtered ? noMatchText("dead ends") : "No dead ends recorded yet.");
  for (const d of deadEnds) list.appendChild(createDeadEndItem(d.attempt, d.problem, showRepo));
}

function createDeadEndItem(a, problem, showRepo) {
  const li = document.createElement("li");
  li.className = "history-item deadend";

  const head = historyHead(a.description);
  if (a.source === "agent") head.appendChild(historyChip("agent", "attempt-agent"));
  if (showRepo) head.appendChild(historyChip(historyRepoLabel(problem.repo, problem.name), "history-repo"));
  li.appendChild(head);
  if (a.why) li.appendChild(historyLine("Why it failed:", a.why));
  if (a.evidence) li.appendChild(historyEvidence(a.evidence));
  appendMaybe(li, historyDates(a.believed_from, a.invalidated_on));

  const where = document.createElement("p");
  where.className = "history-line";
  const label = document.createElement("span");
  label.className = "history-line-label";
  label.textContent = "Problem: ";
  where.appendChild(label);
  const link = document.createElement("button");
  link.type = "button";
  link.className = "history-link";
  link.textContent = problem.title;
  link.addEventListener("click", () => goToHistoryItem("problems", `problem-${problem.id}`));
  where.appendChild(link);
  li.appendChild(where);

  const worked = (problem.attempts || []).find((x) => x.outcome === "worked");
  li.appendChild(historyLine("What worked instead:", worked ? worked.description : "nothing recorded yet", worked ? "" : "is-muted"));
  appendMaybe(li, historyRefs(a.task_ids, a.commits));
  return li;
}

// ----- mind map -----
//
// A left-to-right tree: root → (repos) → topics → problems and milestones →
// attempts. Every leaf gets its own row, parents sit centred on their
// children, and each depth has a fixed column. Drawn with createElementNS.

const SVG_NS = "http://www.w3.org/2000/svg";
const MAP_ROW_H = 30;
const MAP_COL_W = 250;
const MAP_NODE_W = 220;
const MAP_NODE_H = 22;
const MAP_PAD = 12;
const MAP_LABEL_MAX = 34;

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}

function truncateLabel(text, max) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

function mapNode(label, kind, extra) {
  return { label: label || "", kind, children: [], ...extra };
}

function buildHistoryTree(milestones, problems, showRepo) {
  const repo = currentHistoryRepo();
  const root = mapNode(showRepo ? "All repos" : (repo && repo.name) || historyScope, "root");
  const repoNodes = new Map();
  const topicNodes = new Map();

  const topicFor = (item) => {
    let parent = root;
    if (showRepo) {
      parent = repoNodes.get(item.repo);
      if (!parent) {
        parent = mapNode(historyRepoLabel(item.repo, item.name), "repo", { full: item.repo });
        repoNodes.set(item.repo, parent);
        root.children.push(parent);
      }
    }
    const topic = item.topic || "General";
    const key = `${showRepo ? item.repo : ""}\n${topic}`;
    let node = topicNodes.get(key);
    if (!node) {
      node = mapNode(topic, "topic");
      topicNodes.set(key, node);
      parent.children.push(node);
    }
    return node;
  };

  for (const p of problems) {
    const state = HISTORY_STATE_LABEL[p.state] ? p.state : "open";
    const problemNode = mapNode(p.title, "problem", {
      status: state,
      view: "problems",
      target: `problem-${p.id}`,
      aria: `Problem, ${HISTORY_STATE_LABEL[state].toLowerCase()}: ${p.title}`,
    });
    for (const a of p.attempts || []) {
      const outcome = HISTORY_OUTCOME[a.outcome] ? a.outcome : "pending";
      problemNode.children.push(
        mapNode(a.description, "attempt", {
          status: outcome,
          glyph: HISTORY_OUTCOME[outcome].glyph,
          view: "problems",
          target: `attempt-${a.id}`,
          aria: `Attempt, ${HISTORY_OUTCOME[outcome].word.toLowerCase()}: ${a.description}`,
        }),
      );
    }
    topicFor(p).children.push(problemNode);
  }
  for (const m of milestones) {
    topicFor(m).children.push(
      mapNode(m.title, "milestone", {
        view: "timeline",
        target: `milestone-${m.id}`,
        full: m.happened_on ? `${m.happened_on} · ${m.title}` : m.title,
        aria: `Milestone${m.happened_on ? ` ${m.happened_on}` : ""}: ${m.title}`,
      }),
    );
  }
  return root;
}

/** Leaves take consecutive rows; a parent sits halfway between its first and last child. */
function layoutHistoryTree(root) {
  let rows = 0;
  let maxDepth = 0;
  const visit = (node, depth) => {
    node.depth = depth;
    if (depth > maxDepth) maxDepth = depth;
    if (!node.children.length) {
      node.y = rows * MAP_ROW_H;
      rows += 1;
      return;
    }
    for (const child of node.children) visit(child, depth + 1);
    node.y = (node.children[0].y + node.children[node.children.length - 1].y) / 2;
  };
  visit(root, 0);
  return { rows, maxDepth };
}

function renderMap(milestones, problems, showRepo, filtered) {
  el.historyMap.textContent = "";
  const count = milestones.length + problems.length;
  setTabEmpty("map", count ? "" : filtered ? noMatchText("items") : "Nothing to map yet.");
  el.historyMap.hidden = count === 0;
  if (!count) return;

  const root = buildHistoryTree(milestones, problems, showRepo);
  const { rows, maxDepth } = layoutHistoryTree(root);
  const width = MAP_PAD * 2 + maxDepth * MAP_COL_W + MAP_NODE_W;
  const height = MAP_PAD * 2 + (rows - 1) * MAP_ROW_H + MAP_NODE_H;
  const svg = svgEl("svg", {
    class: "map-svg",
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "group",
    "aria-label": `Mind map of ${root.label}`,
  });
  const edges = svgEl("g", { class: "map-edges", "aria-hidden": "true" });
  const nodes = svgEl("g", { class: "map-nodes" });
  svg.appendChild(edges);
  svg.appendChild(nodes);

  const anchor = (node) => ({ x: MAP_PAD + node.depth * MAP_COL_W, y: MAP_PAD + node.y + MAP_NODE_H / 2 });
  const draw = (node) => {
    const from = anchor(node);
    for (const child of node.children) {
      const to = anchor(child);
      const x1 = from.x + MAP_NODE_W;
      const mid = (x1 + to.x) / 2;
      edges.appendChild(svgEl("path", { class: "map-edge", d: `M${x1} ${from.y} C${mid} ${from.y} ${mid} ${to.y} ${to.x} ${to.y}` }));
    }
    nodes.appendChild(createMapNode(node, from));
    for (const child of node.children) draw(child);
  };
  draw(root);
  el.historyMap.appendChild(svg);
}

function createMapNode(node, at) {
  const cls = `map-node map-${node.kind}${node.status ? ` is-${node.status}` : ""}`;
  const g = svgEl("g", { class: cls, transform: `translate(${at.x} ${at.y - MAP_NODE_H / 2})` });
  const title = svgEl("title");
  title.textContent = node.full || node.label;
  g.appendChild(title);
  g.appendChild(svgEl("rect", { width: MAP_NODE_W, height: MAP_NODE_H, rx: 6, ry: 6 }));
  const text = svgEl("text", { x: 8, y: MAP_NODE_H / 2, "dominant-baseline": "central" });
  text.textContent = `${node.glyph ? `${node.glyph} ` : ""}${truncateLabel(node.label, MAP_LABEL_MAX)}`;
  g.appendChild(text);
  if (node.target) {
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.setAttribute("aria-label", node.aria);
    const go = () => goToHistoryItem(node.view, node.target);
    g.addEventListener("click", go);
    g.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter" || evt.key === " ") {
        evt.preventDefault();
        go();
      }
    });
  }
  return g;
}

// ----- wiring -----

function setHistoryFilter(value) {
  historyFilterText = value.trim().toLowerCase();
  if (historyData) renderHistory();
}

el.historyToggle.addEventListener("click", () => {
  if (historyOpen) closeHistory();
  else openHistory();
});
el.historyClose.addEventListener("click", () => closeHistory());
el.historyScope.addEventListener("change", () => {
  historyScope = el.historyScope.value;
  historyScopeChosen = true;
  historyData = null;
  historyRenderedKey = "";
  renderHistory();
  loadHistory();
});
el.historyFilter.addEventListener("input", () => setHistoryFilter(el.historyFilter.value));
el.historyModel.addEventListener("change", () => {
  writeLocal(HISTORY_MODEL_KEY, el.historyModel.value);
  renderHistorySync();
});
el.historySync.addEventListener("click", startHistorySync);
el.historyTabs.addEventListener("click", (evt) => {
  const tab = evt.target.closest("[role='tab']");
  if (tab) setHistoryView(tab.dataset.view, false);
});
el.historyTabs.addEventListener("keydown", (evt) => {
  const i = HISTORY_VIEWS.indexOf(historyView);
  let next = null;
  if (evt.key === "ArrowRight") next = HISTORY_VIEWS[(i + 1) % HISTORY_VIEWS.length];
  else if (evt.key === "ArrowLeft") next = HISTORY_VIEWS[(i - 1 + HISTORY_VIEWS.length) % HISTORY_VIEWS.length];
  else if (evt.key === "Home") next = HISTORY_VIEWS[0];
  else if (evt.key === "End") next = HISTORY_VIEWS[HISTORY_VIEWS.length - 1];
  if (!next) return;
  evt.preventDefault();
  setHistoryView(next, true);
});
el.history.addEventListener("keydown", (evt) => {
  if (evt.key !== "Escape" || isDrawerOpen()) return;
  evt.preventDefault();
  // Escape in a filled filter clears the filter first; the next one closes.
  if (evt.target === el.historyFilter && el.historyFilter.value) {
    el.historyFilter.value = "";
    setHistoryFilter("");
    return;
  }
  closeHistory();
});

// ---------- architecture ----------
//
// A repo's areas: the vocabulary its work is described in ("checkout",
// "auth", "ci"), each with the specs, problems and milestones linked to it
// and how much editing happened there. Reading and Scan are free; only Map
// areas spends tokens, one model call with the model picked next to it.

const ARCH_MODEL_KEY = "tasky.archModel";
const ARCH_POLL_MS = 3000;
const ARCH_START_GRACE_MS = 4000;
const ARCH_KINDS = ["business", "technical"];
const ARCH_PATH_CHIPS = 4;
const ARCH_ALIAS_CHIPS = 6;
const ARCH_UNPLACED_MAX = 8;
const ARCH_SPEC_ITEMS = 3;
const ARCH_SPEC_KINDS = ["openspec", "openspec-change", "spec-kit", "kiro", "adr"];
const ARCH_SPEC_LABEL = {
  openspec: "OpenSpec",
  "openspec-change": "OpenSpec change",
  "spec-kit": "spec-kit",
  kiro: "Kiro",
  adr: "ADR",
};
const ARCH_SOURCE_HINT = {
  user: { text: "edited", title: "Edited by you: a new mapping keeps its name and description" },
  archify: { text: "Archify", title: "Taken from Archify output" },
  graphify: { text: "Graphify", title: "Taken from Graphify output" },
};

let archOpen = false;
let archRepo = ""; // the shown repo key; "" until the server names one
let archRepoChosen = false;
let archRepos = [];
let archData = null;
let archSeq = 0;
let archStarting = ""; // repo key a mapping was just asked for, until the server reports it running
let archStartTimer = null;
let archPollTimer = null;
let archWasRunning = false;
let archScanning = false;
let archNotice = null; // {text, error} from the last Scan or Map click
let archFilterText = "";
let archRenderedKey = "";
let archRepoOptionsKey = "";
let archModelOptionsKey = "";
let archStaleWhileEditing = false;
const archExpanded = new Set();
let archEditing = null; // an area id, "new", or null
let archConfirming = null; // the area id whose Delete waits for "Yes, delete"
let archNewPrefill = null;

function archView() {
  return archData && archData.view ? archData.view : null;
}

function archScanRow() {
  const view = archView();
  return view && view.scan ? view.scan : null;
}

function archIsRunning() {
  if (!archRepo) return false;
  if (archStarting === archRepo) return true;
  const scan = archScanRow();
  return Boolean(scan && scan.state === "running");
}

/** "just now" / "3h ago" within a day, else the date. */
function archWhen(iso) {
  if (!iso) return "";
  const rel = relativeTime(iso);
  if (rel === "just now") return rel;
  if (/^\d+[mh]$/.test(rel)) return `${rel} ago`;
  return String(iso).slice(0, 10);
}

function archRepoLabel(repo) {
  const twins = archRepos.filter((r) => r.name === repo.name).length;
  const name = twins > 1 ? `${repo.name} (${repo.repo})` : repo.name || repo.repo;
  return repo.areas ? `${name} · ${plural(repo.areas, "area")}` : name;
}

/** Network and auth failures go to the usual banner; anything else is returned for inline display. */
function archErrorText(err) {
  if (err instanceof UnauthorizedError || err instanceof NetworkError || err instanceof NotVerifiedError) {
    handleApiError(err);
    return "";
  }
  return err && err.message ? err.message : "Something went wrong";
}

function openArch() {
  if (el.searchInput.value) clearSearch();
  if (historyOpen) closeHistory(false);
  archOpen = true;
  el.archToggle.setAttribute("aria-pressed", "true");
  el.arch.hidden = false;
  el.board.hidden = true;
  el.tablist.hidden = true;
  archData = null;
  archRenderedKey = "";
  archEditing = null;
  archConfirming = null;
  archNotice = null;
  renderArch();
  loadArch();
  el.archHeading.focus();
}

function closeArch(focusToggle = true) {
  archOpen = false;
  clearTimeout(archPollTimer);
  archPollTimer = null;
  el.archToggle.setAttribute("aria-pressed", "false");
  el.arch.hidden = true;
  el.board.hidden = false;
  el.tablist.hidden = false;
  if (focusToggle) el.archToggle.focus();
}

async function loadArch() {
  const seq = ++archSeq;
  const wanted = archRepo;
  let data;
  try {
    data = await apiGet(`/api/architecture${wanted ? `?${new URLSearchParams({ repo: wanted })}` : ""}`);
  } catch (err) {
    if (seq !== archSeq) return;
    handleApiError(err);
    scheduleArchPoll();
    return;
  }
  // A reply for a repo switched away from, or older than a newer load, is dropped.
  if (seq !== archSeq || wanted !== archRepo || !archOpen) return;
  archRepos = Array.isArray(data.repos) ? data.repos : [];
  if (!archRepoChosen && !wanted && projectFilter) {
    // First open: prefer the repo the board is filtered to.
    const hit = archRepos.find((r) => Array.isArray(r.cwds) && r.cwds.includes(projectFilter));
    if (hit && hit.repo !== data.repo) {
      archRepo = hit.repo;
      loadArch();
      return;
    }
  }
  archRepo = data.repo || "";
  archData = data;
  const scan = archScanRow();
  const serverRunning = Boolean(scan && scan.state === "running");
  if (archStarting && archStarting === archRepo && serverRunning) clearArchStarting();
  if (archWasRunning && !archIsRunning()) {
    if (scan && scan.error) announce(`Mapping stopped: ${scan.error}`);
    else announce(`Mapped ${plural(data.view ? data.view.areas.length : 0, "area")}`);
  }
  archWasRunning = archIsRunning();
  renderArch();
  scheduleArchPoll();
}

function scheduleArchPoll() {
  clearTimeout(archPollTimer);
  archPollTimer = null;
  if (!archOpen || !archIsRunning()) return;
  archPollTimer = setTimeout(() => {
    archPollTimer = null;
    if (document.hidden) {
      scheduleArchPoll();
      return;
    }
    loadArch();
  }, ARCH_POLL_MS);
}

function clearArchStarting() {
  archStarting = "";
  clearTimeout(archStartTimer);
  archStartTimer = null;
}

async function startArchScan() {
  const repo = archRepo;
  if (!repo || archScanning || archIsRunning()) return;
  archScanning = true;
  archNotice = null;
  renderArchActions();
  try {
    const res = await apiMutate("POST", "/api/architecture/scan", { repo });
    const parts = [`Scanned ${plural(res.files || 0, "file")}`, plural(res.specs || 0, "spec")];
    if (res.candidates) parts.push(plural(res.candidates, "candidate area"));
    if (res.adopted) parts.push(`${res.adopted} adopted`);
    archNotice = { text: parts.join(" · ") };
    announce(parts.join(", "));
  } catch (err) {
    const text = archErrorText(err);
    archNotice = text ? { text, error: true } : null;
  } finally {
    archScanning = false;
  }
  if (repo !== archRepo) return;
  archRenderedKey = "";
  await loadArch();
}

async function startArchMap() {
  const repo = archRepo;
  if (!repo || archScanning || archIsRunning()) return;
  const model = el.archModel.value;
  archStarting = repo;
  archNotice = null;
  archWasRunning = true;
  renderArchActions();
  try {
    await apiMutate("POST", "/api/architecture/map", { repo, model });
    announce(`Mapping areas with ${modelLabel(model)}`);
  } catch (err) {
    clearArchStarting();
    archWasRunning = false;
    const text = archErrorText(err);
    archNotice = text ? { text, error: true } : null;
    renderArchActions();
    loadArch();
    return;
  }
  // The worker claims the repo a moment after it starts; until the server
  // says so, keep showing it as starting rather than idle.
  clearTimeout(archStartTimer);
  archStartTimer = setTimeout(() => {
    archStarting = "";
    archStartTimer = null;
    loadArch();
  }, ARCH_START_GRACE_MS);
  scheduleArchPoll();
}

// ----- head, actions, meta -----

function renderArchRepoOptions() {
  const key = JSON.stringify(archRepos.map((r) => [r.repo, r.name, r.areas]));
  if (key !== archRepoOptionsKey) {
    archRepoOptionsKey = key;
    el.archRepo.textContent = "";
    for (const r of archRepos) {
      const opt = document.createElement("option");
      opt.value = r.repo;
      opt.textContent = archRepoLabel(r);
      el.archRepo.appendChild(opt);
    }
  }
  if (archRepo) el.archRepo.value = archRepo;
  el.archRepo.disabled = archRepos.length === 0;
}

function renderArchModelOptions() {
  const models = archData && Array.isArray(archData.models) && archData.models.length ? archData.models : HISTORY_DEFAULT_MODELS;
  const key = models.join(",");
  if (key !== archModelOptionsKey) {
    archModelOptionsKey = key;
    el.archModel.textContent = "";
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = modelLabel(m);
      el.archModel.appendChild(opt);
    }
    const stored = readLocal(ARCH_MODEL_KEY);
    const fallback = archData && archData.default_model;
    el.archModel.value = models.includes(stored) ? stored : models.includes(fallback) ? fallback : models[0];
  }
}

function formatUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

function renderArchActions() {
  const view = archView();
  el.archActions.hidden = !view;
  if (!view) return;
  renderArchModelOptions();
  const running = archIsRunning();
  const scan = view.scan;
  el.archScan.disabled = running || archScanning;
  el.archScanLabel.textContent = archScanning ? "Scanning…" : "Scan";
  el.archMap.disabled = running || archScanning;
  el.archMap.textContent = running ? "Mapping…" : "Map areas";
  el.archModel.disabled = running;

  const status = el.archStatus;
  status.textContent = "";
  status.title = scan && scan.total_cost_usd ? `Spent on this repo's areas so far: ${formatUsd(scan.total_cost_usd)}` : "";
  if (archScanning) {
    status.textContent = "Scanning the repo…";
    return;
  }
  if (running) {
    const model = (scan && scan.state === "running" && scan.model) || el.archModel.value;
    status.textContent = `Mapping… ${modelLabel(model)} is defining this repo's areas. This takes about a minute.`;
    return;
  }
  if (archNotice) {
    const span = document.createElement("span");
    if (archNotice.error) span.className = "arch-error";
    span.textContent = archNotice.text;
    status.appendChild(span);
    return;
  }
  // When and by whom it was mapped, and the cost, sit in the meta line;
  // the status line only speaks up for a failed run.
  if (scan && scan.error) {
    const err = document.createElement("span");
    err.className = "arch-error";
    err.textContent = `Last mapping stopped: ${scan.error}`;
    status.appendChild(err);
  }
}

function renderArchMeta() {
  const view = archView();
  const scan = view && view.scan;
  el.archMeta.hidden = !(scan && scan.scanned_at);
  if (el.archMeta.hidden) {
    el.archMeta.textContent = "";
    return;
  }
  const sources = scan.sources || {};
  const specKinds = ARCH_SPEC_KINDS.filter((k) => sources[k]).map((k) => `${ARCH_SPEC_LABEL[k]} ${sources[k]}`);
  const others = Object.keys(sources)
    .filter((k) => !ARCH_SPEC_KINDS.includes(k) && sources[k])
    .map((k) => `${k === "folder" ? "folders" : k} ${sources[k]}`);
  const specCount = Array.isArray(view.specs) ? view.specs.length : 0;
  const parts = [`Scanned ${archWhen(scan.scanned_at)}`];
  if (scan.files != null) parts.push(plural(scan.files, "file"));
  parts.push(`${plural(specCount, "spec")}${specKinds.length ? ` (${specKinds.join(", ")})` : ""}`);
  if (others.length) parts.push(`candidates from ${others.join(", ")}`);
  if (scan.mapped_at) {
    const cost = formatUsd(scan.last_cost_usd);
    parts.push(`mapped ${archWhen(scan.mapped_at)}${scan.model ? ` by ${scan.model}` : ""}${cost ? ` (${cost})` : ""}`);
  }
  el.archMeta.textContent = parts.join(" · ");
  el.archMeta.title = scan.root || "";
}

function renderArchEmpty() {
  const view = archView();
  let title = "";
  let text = "";
  if (archData && !archRepos.length) {
    title = "No repos recorded yet.";
    text = "Areas are kept per repository. Run a task in one and it shows up here.";
  } else if (view && !view.areas.length) {
    if (!view.scan) {
      title = "This repo has not been scanned yet.";
      text =
        "Scan reads its specs, ADRs, Archify and Graphify output for free. Map areas then asks the model to define the vocabulary: the business and technical parts the repo is made of. You can also add areas yourself.";
    } else {
      title = "No areas yet.";
      text = `Scan found ${plural(view.specs.length, "spec")}. Map areas asks the model to define the vocabulary from them, or add areas yourself.`;
    }
  }
  el.archEmpty.hidden = !title;
  el.archEmptyTitle.textContent = title;
  el.archEmptyText.textContent = text;
}

function renderArch() {
  renderArchRepoOptions();
  renderArchActions();
  renderArchMeta();
  renderArchEmpty();
  const view = archView();
  el.archBody.hidden = !view;
  if (!view) {
    archRenderedKey = "";
    return;
  }
  // Polls re-deliver the same view often; rebuilding would drop focus,
  // open details and scroll position for nothing. An open form is never
  // rebuilt under the user's hands; it catches up once closed.
  const key = JSON.stringify([
    archRepo,
    view.areas,
    view.specs,
    view.unplaced_dirs,
    view.unplaced_topics,
    view.unlinked_specs,
    view.edited_without_area,
    archFilterText,
  ]);
  if (key === archRenderedKey) return;
  if (archEditing !== null || archConfirming !== null) {
    archStaleWhileEditing = true;
    return;
  }
  archRenderedKey = key;
  archStaleWhileEditing = false;
  renderArchBody();
}

/** Re-renders after a form or confirm closes, if data arrived meanwhile. */
function archCatchUp() {
  if (archStaleWhileEditing) {
    archRenderedKey = "";
    renderArch();
  }
}

// ----- areas -----

function archAreaMatches(area) {
  if (!archFilterText) return true;
  const hay = [area.name, area.kind, area.description, ...(area.aliases || []), ...(area.paths || [])]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
  return hay.includes(archFilterText);
}

function archAreaActivity(area) {
  return (area.turns || 0) + (area.files || 0) + (area.tasks || 0);
}

/** Areas as the server sorted them (by activity), with idle ones moved last. */
function archSortedAreas(view) {
  const active = view.areas.filter((a) => archAreaActivity(a) > 0);
  const idle = view.areas.filter((a) => archAreaActivity(a) === 0);
  return [...active, ...idle];
}

function archContext(view) {
  const specsByPath = new Map((view.specs || []).map((s) => [s.path, s]));
  const maxTurns = Math.max(1, ...view.areas.map((a) => a.turns || 0));
  return { specsByPath, maxTurns };
}

function renderArchBody() {
  const view = archView();
  const focusKey = archFocusKey();
  const areas = archSortedAreas(view);
  const shown = areas.filter(archAreaMatches);
  const ctx = archContext(view);
  el.archCount.textContent = archFilterText ? `${shown.length} of ${areas.length}` : String(areas.length);
  el.archAreas.textContent = "";
  for (const area of shown) el.archAreas.appendChild(buildArchArea(area, ctx));
  el.archNoMatch.hidden = !(archFilterText && areas.length && !shown.length);
  el.archNoMatch.textContent = el.archNoMatch.hidden ? "" : `No areas match “${el.archFilter.value.trim()}”.`;
  renderArchUnplaced(view);
  renderArchSpecs(view);
  if (focusKey) archRestoreFocus(focusKey);
}

function archFocusKey() {
  const active = document.activeElement;
  if (!active || !el.arch.contains(active)) return "";
  return active.dataset.focus || "";
}

function archRestoreFocus(key) {
  const node = el.arch.querySelector(`[data-focus="${CSS.escape(key)}"]`);
  if (node) node.focus();
}

function archButton(text, className, focusKey) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = className;
  btn.textContent = text;
  if (focusKey) btn.dataset.focus = focusKey;
  return btn;
}

function archChipRow(values, max, className, label, render) {
  if (!values.length) return null;
  const row = document.createElement("div");
  row.className = `arch-chips ${className}`;
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", label);
  for (const v of values.slice(0, max)) row.appendChild(render(v));
  if (values.length > max) {
    const more = document.createElement("span");
    more.className = "arch-chip arch-chip-more";
    more.textContent = `+${values.length - max}`;
    more.title = values.slice(max).join("\n");
    row.appendChild(more);
  }
  return row;
}

function archPathChip(path) {
  const chip = document.createElement("code");
  chip.className = "arch-chip arch-path";
  chip.textContent = path;
  chip.title = path;
  return chip;
}

function archAliasChip(alias) {
  const chip = document.createElement("span");
  chip.className = "arch-chip arch-alias";
  chip.textContent = alias;
  return chip;
}

function archKindBadge(kind) {
  const badge = document.createElement("span");
  badge.className = "badge arch-kind";
  badge.dataset.kind = ARCH_KINDS.includes(kind) ? kind : "technical";
  badge.textContent = kind || "technical";
  return badge;
}

function archAreaSpecs(area) {
  const linked = Array.isArray(area.spec_items) && area.spec_items.length ? area.spec_items : area.specs || [];
  return [...new Set(linked)];
}

function buildArchArea(area, ctx) {
  const li = document.createElement("li");
  li.className = "arch-area";
  li.id = `arch-area-${area.id}`;
  li.dataset.areaId = String(area.id);
  if (archAreaActivity(area) === 0) li.classList.add("is-idle");
  if (area.kind === "business") li.classList.add("is-business");
  if (archEditing === area.id) {
    li.classList.add("is-editing");
    li.appendChild(buildArchForm(area));
    return li;
  }

  const head = document.createElement("div");
  head.className = "arch-area-head";
  const name = document.createElement("h4");
  name.className = "arch-area-name";
  name.textContent = area.name;
  head.append(name, archKindBadge(area.kind));
  const hint = ARCH_SOURCE_HINT[area.source];
  if (hint) {
    const src = document.createElement("span");
    src.className = "badge arch-source";
    src.textContent = hint.text;
    src.title = hint.title;
    head.appendChild(src);
  }
  head.appendChild(buildArchTools(area));
  li.appendChild(head);

  if (area.description) {
    const desc = document.createElement("p");
    desc.className = "arch-area-desc";
    desc.textContent = area.description;
    li.appendChild(desc);
  }

  li.appendChild(buildArchActivity(area, ctx.maxTurns));
  appendMaybe(li, archChipRow(area.paths || [], ARCH_PATH_CHIPS, "arch-paths", "Paths", archPathChip));
  appendMaybe(li, archChipRow(area.aliases || [], ARCH_ALIAS_CHIPS, "arch-aliases", "Aliases", archAliasChip));

  const error = document.createElement("p");
  error.className = "arch-form-error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  li.appendChild(error);

  const specs = archAreaSpecs(area);
  const problems = area.problems || [];
  const milestones = area.milestones || [];
  const tasks = area.recent_tasks || [];
  const counts = document.createElement("span");
  counts.className = "arch-counts";
  const addCount = (text, attention) => {
    const span = document.createElement("span");
    span.textContent = text;
    if (attention) span.className = "is-attn";
    counts.appendChild(span);
  };
  if (specs.length) addCount(plural(specs.length, "spec"));
  const open = problems.filter((p) => p.state !== "solved").length;
  if (open) addCount(plural(open, "open problem"), true);
  const solved = problems.length - open;
  if (solved) addCount(`${solved} solved`);
  if (milestones.length) addCount(plural(milestones.length, "milestone"));
  if (area.tasks) addCount(plural(area.tasks, "task"));

  if (!counts.childNodes.length) {
    const none = document.createElement("p");
    none.className = "arch-counts arch-counts-none";
    none.textContent = "No specs, problems or tasks linked yet";
    li.appendChild(none);
    return li;
  }
  const detailId = `arch-detail-${area.id}`;
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "arch-disclosure";
  toggle.dataset.focus = `toggle-${area.id}`;
  toggle.setAttribute("aria-controls", detailId);
  const expanded = archExpanded.has(area.id);
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.innerHTML =
    '<svg class="chevron" aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>';
  toggle.appendChild(counts);
  const sr = document.createElement("span");
  sr.className = "visually-hidden";
  sr.textContent = ` for ${area.name}`;
  toggle.appendChild(sr);
  const detail = document.createElement("div");
  detail.className = "arch-detail";
  detail.id = detailId;
  detail.hidden = !expanded;
  if (expanded) fillArchDetail(detail, area, ctx, specs);
  toggle.addEventListener("click", () => {
    const on = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(on));
    if (on) {
      archExpanded.add(area.id);
      if (!detail.childNodes.length) fillArchDetail(detail, area, ctx, specs);
    } else {
      archExpanded.delete(area.id);
    }
    detail.hidden = !on;
  });
  li.append(toggle, detail);
  return li;
}

function buildArchActivity(area, maxTurns) {
  const row = document.createElement("div");
  row.className = "arch-activity";
  const bar = document.createElement("span");
  bar.className = "arch-bar";
  bar.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  fill.className = "arch-bar-fill";
  const turns = area.turns || 0;
  // CSSOM writes are not inline styles as far as CSP is concerned.
  fill.style.width = `${turns ? Math.max(3, Math.round((turns / maxTurns) * 100)) : 0}%`;
  bar.appendChild(fill);
  const text = document.createElement("span");
  text.className = "arch-activity-text";
  const parts = [];
  if (turns) parts.push(plural(turns, "turn"));
  if (area.files) parts.push(plural(area.files, "file"));
  if (area.last_edit) parts.push(`last ${area.last_edit}`);
  if (!parts.length) parts.push("No edits recorded");
  parts.forEach((part, i) => {
    const span = document.createElement("span");
    span.textContent = i ? ` · ${part}` : part;
    text.appendChild(span);
  });
  row.append(bar, text);
  return row;
}

function buildArchTools(area) {
  const tools = document.createElement("span");
  tools.className = "arch-area-tools";
  if (archConfirming === area.id) {
    const ask = document.createElement("span");
    ask.className = "arch-confirm-ask";
    ask.textContent = "Delete?";
    const yes = archButton("Yes, delete", "btn btn-sm arch-danger", `yes-${area.id}`);
    yes.addEventListener("click", () => deleteArchArea(area, yes));
    const no = archButton("Cancel", "btn btn-ghost btn-sm", `no-${area.id}`);
    no.addEventListener("click", () => {
      archConfirming = null;
      rerenderArchArea(area.id, `delete-${area.id}`);
      archCatchUp();
    });
    tools.append(ask, yes, no);
    return tools;
  }
  const edit = archButton("Edit", "btn btn-ghost btn-sm", `edit-${area.id}`);
  edit.setAttribute("aria-label", `Edit ${area.name}`);
  edit.addEventListener("click", () => {
    closeArchNew();
    archConfirming = null;
    archEditing = area.id;
    rerenderArchArea(area.id);
    const input = el.archAreas.querySelector(`#arch-area-${area.id} input[name="name"]`);
    if (input) input.focus();
  });
  const del = archButton("Delete", "btn btn-ghost btn-sm", `delete-${area.id}`);
  del.setAttribute("aria-label", `Delete ${area.name}`);
  del.addEventListener("click", () => {
    archConfirming = area.id;
    rerenderArchArea(area.id, `yes-${area.id}`);
  });
  tools.append(edit, del);
  return tools;
}

function findArchArea(id) {
  const view = archView();
  return view ? view.areas.find((a) => a.id === id) || null : null;
}

/** Swaps one card for a fresh copy (edit, confirm, cancel), leaving the rest alone. */
function rerenderArchArea(id, focusKey) {
  const node = document.getElementById(`arch-area-${id}`);
  const area = findArchArea(id);
  const view = archView();
  if (!node || !area || !view) return;
  node.replaceWith(buildArchArea(area, archContext(view)));
  if (focusKey) archRestoreFocus(focusKey);
}

async function deleteArchArea(area, button) {
  button.disabled = true;
  try {
    await apiMutate("DELETE", `/api/areas/${area.id}`);
  } catch (err) {
    button.disabled = false;
    const text = archErrorText(err);
    const node = document.getElementById(`arch-area-${area.id}`);
    const box = node && node.querySelector(".arch-form-error");
    if (box && text) {
      box.textContent = text;
      box.hidden = false;
    }
    return;
  }
  archConfirming = null;
  archExpanded.delete(area.id);
  announce(`Deleted area ${area.name}`);
  archRenderedKey = "";
  await loadArch();
  el.archAdd.focus();
}

// ----- expanded detail -----

function archDetailSection(title, count) {
  const section = document.createElement("section");
  section.className = "arch-detail-section";
  const h = document.createElement("h5");
  h.className = "arch-detail-heading";
  h.textContent = title;
  if (count != null) {
    const n = document.createElement("span");
    n.className = "col-count";
    n.textContent = String(count);
    h.appendChild(n);
  }
  const list = document.createElement("ul");
  list.className = "arch-detail-list";
  section.append(h, list);
  return { section, list };
}

function fillArchDetail(detail, area, ctx, specs) {
  detail.textContent = "";
  if (specs.length) {
    const { section, list } = archDetailSection("Specs", specs.length);
    for (const path of specs) list.appendChild(buildArchSpecItem(ctx.specsByPath.get(path), path));
    detail.appendChild(section);
  }
  const problems = area.problems || [];
  if (problems.length) {
    const { section, list } = archDetailSection("Problems", problems.length);
    for (const p of problems) {
      const li = document.createElement("li");
      li.className = "arch-detail-item arch-problem";
      const title = document.createElement("span");
      title.className = "arch-detail-title";
      title.textContent = p.title;
      const state = document.createElement("span");
      state.className = `badge history-chip history-state-${p.state}`;
      state.textContent = HISTORY_STATE_LABEL[p.state] || p.state;
      li.append(state, title);
      const meta = [];
      if (p.failed) meta.push(`${plural(p.failed, "failed fix")}`);
      if (p.last_seen) meta.push(`last seen ${p.last_seen}`);
      if (meta.length) {
        const m = document.createElement("span");
        m.className = "arch-detail-meta";
        m.textContent = meta.join(" · ");
        li.appendChild(m);
      }
      list.appendChild(li);
    }
    detail.appendChild(section);
  }
  const milestones = area.milestones || [];
  if (milestones.length) {
    const { section, list } = archDetailSection("Milestones", milestones.length);
    for (const m of milestones) {
      const li = document.createElement("li");
      li.className = "arch-detail-item arch-milestone";
      const date = document.createElement("span");
      date.className = "arch-detail-date";
      date.textContent = m.happened_on || "undated";
      const title = document.createElement("span");
      title.className = "arch-detail-title";
      title.textContent = m.title;
      li.append(date, title);
      list.appendChild(li);
    }
    detail.appendChild(section);
  }
  const tasks = area.recent_tasks || [];
  if (tasks.length) {
    const label = area.tasks > tasks.length ? `Recent tasks (${tasks.length} of ${area.tasks})` : "Recent tasks";
    const { section, list } = archDetailSection(label, area.tasks > tasks.length ? null : tasks.length);
    for (const t of tasks) {
      const li = document.createElement("li");
      li.className = "arch-detail-item arch-task";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-link arch-task-link";
      btn.dataset.focus = `task-${area.id}-${t.id}`;
      const id = document.createElement("span");
      id.className = "arch-task-id";
      id.textContent = `#${t.id}`;
      btn.append(id, document.createTextNode(` ${t.title || "Untitled task"}`));
      btn.addEventListener("click", () => openTaskById(t.id));
      li.appendChild(btn);
      const meta = [t.date, t.status && t.status !== "done" ? STATUS_WORD[t.status] || t.status : ""].filter(Boolean);
      if (meta.length) {
        const m = document.createElement("span");
        m.className = "arch-detail-meta";
        m.textContent = meta.join(" · ");
        li.appendChild(m);
      }
      list.appendChild(li);
    }
    detail.appendChild(section);
  }
}

function buildArchSpecItem(spec, path) {
  const li = document.createElement("li");
  li.className = "arch-detail-item arch-spec";
  const head = document.createElement("div");
  head.className = "arch-spec-head";
  const title = document.createElement("span");
  title.className = "arch-detail-title";
  title.textContent = (spec && spec.title) || path;
  head.appendChild(title);
  if (spec && spec.kind) {
    const kind = document.createElement("span");
    kind.className = "badge arch-spec-kind";
    kind.textContent = ARCH_SPEC_LABEL[spec.kind] || spec.kind;
    head.appendChild(kind);
  }
  if (spec && spec.status) {
    const status = document.createElement("span");
    status.className = "badge arch-spec-status";
    status.textContent = spec.status;
    head.appendChild(status);
  }
  li.appendChild(head);
  if (spec && spec.summary) {
    const summary = document.createElement("p");
    summary.className = "arch-spec-summary";
    summary.textContent = spec.summary;
    li.appendChild(summary);
  }
  const items = spec && Array.isArray(spec.items) ? spec.items : [];
  if (items.length) {
    const ul = document.createElement("ul");
    ul.className = "arch-spec-items";
    for (const item of items.slice(0, ARCH_SPEC_ITEMS)) {
      const it = document.createElement("li");
      it.textContent = typeof item === "string" ? item : item && (item.title || item.text || JSON.stringify(item));
      ul.appendChild(it);
    }
    if (items.length > ARCH_SPEC_ITEMS) {
      const more = document.createElement("li");
      more.className = "arch-spec-more";
      more.textContent = `and ${items.length - ARCH_SPEC_ITEMS} more`;
      ul.appendChild(more);
    }
    li.appendChild(ul);
  }
  const code = document.createElement("code");
  code.className = "arch-spec-path";
  code.textContent = path;
  li.appendChild(code);
  return li;
}

// ----- create and edit -----

function archField(label, control, hint) {
  const wrap = document.createElement("label");
  wrap.className = "field arch-field";
  const text = document.createElement("span");
  text.className = "field-label";
  text.textContent = label;
  wrap.append(text, control);
  if (hint) {
    const h = document.createElement("span");
    h.className = "field-hint";
    h.textContent = hint;
    wrap.appendChild(h);
  }
  return wrap;
}

function archSplit(text, separator) {
  return text
    .split(separator)
    .map((v) => v.trim())
    .filter(Boolean);
}

/** The same form for a new area (area null, maybe prefilled) and for editing one. */
function buildArchForm(area, prefill) {
  const values = area || prefill || {};
  const form = document.createElement("form");
  form.className = "arch-form";
  form.noValidate = true;
  form.setAttribute("aria-label", area ? `Edit area ${area.name}` : "New area");

  const name = document.createElement("input");
  name.type = "text";
  name.name = "name";
  name.className = "arch-input";
  name.required = true;
  name.maxLength = 60;
  name.autocomplete = "off";
  name.spellcheck = false;
  name.value = values.name || "";

  const kind = document.createElement("select");
  kind.name = "kind";
  kind.className = "select";
  for (const k of ARCH_KINDS) {
    const opt = document.createElement("option");
    opt.value = k;
    opt.textContent = k.charAt(0).toUpperCase() + k.slice(1);
    kind.appendChild(opt);
  }
  kind.value = ARCH_KINDS.includes(values.kind) ? values.kind : "technical";

  const desc = document.createElement("textarea");
  desc.name = "description";
  desc.className = "textarea arch-textarea";
  desc.rows = 2;
  desc.maxLength = 300;
  desc.value = values.description || "";

  const aliases = document.createElement("input");
  aliases.type = "text";
  aliases.name = "aliases";
  aliases.className = "arch-input";
  aliases.autocomplete = "off";
  aliases.spellcheck = false;
  aliases.value = (values.aliases || []).join(", ");

  const paths = document.createElement("textarea");
  paths.name = "paths";
  paths.className = "textarea arch-textarea arch-paths-input";
  paths.rows = Math.min(8, Math.max(3, (values.paths || []).length + 1));
  paths.spellcheck = false;
  paths.value = (values.paths || []).join("\n");

  const top = document.createElement("div");
  top.className = "arch-form-row";
  top.append(archField("Name", name, "lowercase words joined by dashes"), archField("Kind", kind));

  const error = document.createElement("p");
  error.className = "arch-form-error";
  error.setAttribute("role", "alert");
  error.hidden = true;

  const actions = document.createElement("div");
  actions.className = "arch-form-actions";
  const save = document.createElement("button");
  save.type = "submit";
  save.className = "btn btn-primary btn-sm";
  save.textContent = area ? "Save" : "Add area";
  const cancel = archButton("Cancel", "btn btn-sm");
  cancel.addEventListener("click", () => cancelArchForm(area));
  actions.append(save, cancel);

  form.append(
    top,
    archField("Description", desc),
    archField("Aliases", aliases, "comma-separated: other words people use for it"),
    archField("Paths", paths, "one per line, relative to the repo root"),
    error,
    actions,
  );

  form.addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const payload = {
      name: name.value.trim(),
      kind: kind.value,
      description: desc.value.trim(),
      aliases: archSplit(aliases.value, ","),
      paths: archSplit(paths.value, /\n/),
    };
    if (!payload.name) {
      error.textContent = "Give the area a name.";
      error.hidden = false;
      name.focus();
      return;
    }
    save.disabled = true;
    error.hidden = true;
    let saved;
    try {
      saved = area
        ? await apiMutate("PATCH", `/api/areas/${area.id}`, payload)
        : await apiMutate("POST", "/api/areas", { repo: archRepo, ...payload });
    } catch (err) {
      save.disabled = false;
      const text = archErrorText(err);
      if (text) {
        error.textContent = text;
        error.hidden = false;
      }
      return;
    }
    archEditing = null;
    if (!area) closeArchNew();
    announce(area ? `Saved area ${saved.name}` : `Added area ${saved.name}`);
    archRenderedKey = "";
    await loadArch();
    const node = document.getElementById(`arch-area-${saved.id}`);
    if (node) {
      node.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      archRestoreFocus(`edit-${saved.id}`);
    }
  });
  form.addEventListener("keydown", (evt) => {
    if (evt.key !== "Escape") return;
    evt.preventDefault();
    evt.stopPropagation();
    cancelArchForm(area);
  });
  return form;
}

function cancelArchForm(area) {
  if (area) {
    archEditing = null;
    rerenderArchArea(area.id, `edit-${area.id}`);
  } else {
    closeArchNew();
    el.archAdd.focus();
  }
  archCatchUp();
}

function openArchNew(prefill) {
  if (archEditing !== null && archEditing !== "new") {
    const id = archEditing;
    archEditing = null;
    rerenderArchArea(id);
  }
  archConfirming = null;
  archEditing = "new";
  archNewPrefill = prefill || null;
  el.archNew.textContent = "";
  el.archNew.appendChild(buildArchForm(null, archNewPrefill));
  el.archNew.hidden = false;
  el.archAdd.setAttribute("aria-expanded", "true");
  el.archNew.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  const input = el.archNew.querySelector('input[name="name"]');
  if (input) input.focus();
}

function closeArchNew() {
  if (archEditing === "new") archEditing = null;
  archNewPrefill = null;
  el.archNew.hidden = true;
  el.archNew.textContent = "";
  el.archAdd.setAttribute("aria-expanded", "false");
}

// ----- not placed, all specs -----

function archSlug(text) {
  return String(text || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

function archUnplacedGroup(title, values, render) {
  const group = document.createElement("div");
  group.className = "arch-unplaced-group";
  const h = document.createElement("h4");
  h.className = "arch-detail-heading";
  h.textContent = title;
  const n = document.createElement("span");
  n.className = "col-count";
  n.textContent = String(values.length);
  h.appendChild(n);
  const list = document.createElement("ul");
  list.className = "arch-unplaced-list";
  for (const v of values.slice(0, ARCH_UNPLACED_MAX)) {
    const li = document.createElement("li");
    li.appendChild(render(v));
    list.appendChild(li);
  }
  if (values.length > ARCH_UNPLACED_MAX) {
    const li = document.createElement("li");
    li.className = "arch-chip arch-chip-more";
    li.textContent = `+${values.length - ARCH_UNPLACED_MAX} more`;
    li.title = values.slice(ARCH_UNPLACED_MAX).join("\n");
    list.appendChild(li);
  }
  group.append(h, list);
  return group;
}

function archPlaceButton(text, label, prefill, mono) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `arch-chip arch-place${mono ? " arch-path" : ""}`;
  btn.title = label;
  btn.setAttribute("aria-label", `${text}: ${label}`);
  const t = document.createElement("span");
  t.className = "arch-place-text";
  t.textContent = text;
  btn.innerHTML =
    '<svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>';
  btn.appendChild(t);
  btn.addEventListener("click", () => openArchNew(prefill));
  return btn;
}

function renderArchUnplaced(view) {
  const dirs = view.unplaced_dirs || [];
  const topics = view.unplaced_topics || [];
  const specs = view.unlinked_specs || [];
  const without = view.edited_without_area || 0;
  el.archUnplaced.hidden = !(dirs.length || topics.length || specs.length || without);
  el.archUnplacedLists.textContent = "";
  el.archUnplacedNote.hidden = !without;
  el.archUnplacedNote.textContent = without
    ? `${without} of ${plural(view.tasks_total || without, "task")} edited only files outside every area.`
    : "";
  if (dirs.length) {
    el.archUnplacedLists.appendChild(
      archUnplacedGroup("Folders edited outside every area", dirs, (d) => {
        if (d === "." || d === "") return archPlaceButton("repo root", "New area with the files at the repo root", { paths: ["."] }, true);
        const base = d.split("/").filter(Boolean).pop() || d;
        return archPlaceButton(d, "New area with this folder", { name: archSlug(base), paths: [d] }, true);
      }),
    );
  }
  if (topics.length) {
    el.archUnplacedLists.appendChild(
      archUnplacedGroup("History topics with no area", topics, (t) =>
        archPlaceButton(t, "New area named after this topic", { name: archSlug(t), aliases: [t] }, false),
      ),
    );
  }
  if (specs.length) {
    el.archUnplacedLists.appendChild(
      archUnplacedGroup("Specs linked to no area", specs, (p) => {
        const code = document.createElement("code");
        code.className = "arch-chip arch-path";
        code.textContent = p;
        code.title = p;
        return code;
      }),
    );
  }
}

function renderArchSpecs(view) {
  const specs = view.specs || [];
  el.archSpecs.hidden = specs.length === 0;
  el.archSpecsCount.textContent = String(specs.length);
  el.archSpecsList.textContent = "";
  for (const spec of specs) {
    const li = document.createElement("li");
    li.className = "arch-specs-row";
    const head = document.createElement("div");
    head.className = "arch-spec-head";
    const kind = document.createElement("span");
    kind.className = "badge arch-spec-kind";
    kind.textContent = ARCH_SPEC_LABEL[spec.kind] || spec.kind || "spec";
    const title = document.createElement("span");
    title.className = "arch-detail-title";
    title.textContent = spec.title || spec.path;
    head.append(kind, title);
    if (spec.status) {
      const status = document.createElement("span");
      status.className = "badge arch-spec-status";
      status.textContent = spec.status;
      head.appendChild(status);
    }
    li.appendChild(head);
    const foot = document.createElement("div");
    foot.className = "arch-specs-foot";
    const areas = Array.isArray(spec.areas) ? spec.areas : [];
    const where = document.createElement("span");
    where.className = areas.length ? "arch-specs-areas" : "arch-specs-areas is-none";
    where.textContent = areas.length ? areas.join(", ") : "no area";
    const code = document.createElement("code");
    code.className = "arch-spec-path";
    code.textContent = spec.path;
    code.title = spec.path;
    foot.append(where, code);
    li.appendChild(foot);
    el.archSpecsList.appendChild(li);
  }
}

// ----- wiring -----

el.archToggle.addEventListener("click", () => {
  if (archOpen) closeArch();
  else openArch();
});
el.archClose.addEventListener("click", () => closeArch());
el.archRepo.addEventListener("change", () => {
  archRepo = el.archRepo.value;
  archRepoChosen = true;
  archData = null;
  archRenderedKey = "";
  archEditing = null;
  archConfirming = null;
  archNotice = null;
  archWasRunning = false;
  archExpanded.clear();
  closeArchNew();
  renderArch();
  loadArch();
});
el.archFilter.addEventListener("input", () => {
  archFilterText = el.archFilter.value.trim().toLowerCase();
  if (archData) renderArch();
});
el.archModel.addEventListener("change", () => writeLocal(ARCH_MODEL_KEY, el.archModel.value));
el.archScan.addEventListener("click", startArchScan);
el.archMap.addEventListener("click", startArchMap);
el.archAdd.setAttribute("aria-expanded", "false");
el.archAdd.setAttribute("aria-controls", "arch-new");
el.archAdd.setAttribute("aria-label", "Add an area");
el.archAdd.addEventListener("click", () => {
  if (archEditing === "new") {
    closeArchNew();
    archCatchUp();
  } else {
    openArchNew(null);
  }
});
el.arch.addEventListener("keydown", (evt) => {
  if (evt.key !== "Escape" || isDrawerOpen()) return;
  evt.preventDefault();
  if (archConfirming !== null) {
    const id = archConfirming;
    archConfirming = null;
    rerenderArchArea(id, `delete-${id}`);
    archCatchUp();
    return;
  }
  // Escape in a filled filter clears the filter first; the next one closes.
  if (evt.target === el.archFilter && el.archFilter.value) {
    el.archFilter.value = "";
    archFilterText = "";
    if (archData) renderArch();
    return;
  }
  closeArch();
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
  if (on && historyOpen) closeHistory(false);
  if (on && archOpen) closeArch(false);
  el.searchResults.hidden = !on;
  el.board.hidden = on;
  el.tablist.hidden = on;
}

function scheduleSearch() {
  clearTimeout(searchTimer);
  const query = searchQuery();
  if (query !== smartQuery) resetSmartSearch();
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
  el.smartSearch.hidden = false;
}

// ---------- smart search ----------
//
// Word search finds what was said; smart search asks Haiku which tasks are
// about what was meant. It costs money and takes seconds, so it only runs
// when asked, can be cancelled, and never reuses an old answer.

let smartHitsById = new Map();
let smartAbort = null;
let smartQuery = "";

function setSmartBusy(busy) {
  el.smartBtn.disabled = busy;
  el.smartBtn.classList.toggle("is-busy", busy);
  el.smartLabel.textContent = busy ? "Searching with Haiku…" : "Smart search";
  el.smartCancel.hidden = !busy;
}

/** Back to a fresh button: whatever was asking stops, whatever came back goes. */
function resetSmartSearch() {
  if (smartAbort) smartAbort.abort();
  smartAbort = null;
  smartQuery = "";
  smartHitsById = new Map();
  setSmartBusy(false);
  el.smartStatus.textContent = "";
  el.smartStatus.classList.remove("is-error");
  el.smartResults.hidden = true;
  el.smartList.textContent = "";
}

async function runSmartSearch() {
  const query = searchQuery();
  if (query.length < SEARCH_MIN_CHARS) return;
  resetSmartSearch();
  const ctrl = new AbortController();
  smartAbort = ctrl;
  smartQuery = query;
  setSmartBusy(true);
  el.smartStatus.textContent = "Haiku is reading your past tasks…";
  const body = { q: query };
  if (projectFilter) body.cwd = projectFilter;
  let data;
  try {
    data = await apiMutate("POST", "/api/search/smart", body, ctrl.signal);
  } catch (err) {
    if (smartAbort !== ctrl) return; // cancelled, or a newer ask took over
    smartAbort = null;
    setSmartBusy(false);
    if (err instanceof UnauthorizedError || err instanceof NotVerifiedError) {
      el.smartStatus.textContent = "";
      handleApiError(err);
      return;
    }
    const reason = err instanceof NetworkError ? "tasky is not reachable" : err.message.replace(/[\s:]+$/, "");
    el.smartStatus.textContent = `Smart search failed: ${reason}`;
    el.smartStatus.classList.add("is-error");
    return;
  }
  if (smartAbort !== ctrl) return;
  smartAbort = null;
  setSmartBusy(false);
  renderSmartResults(query, data);
}

function renderSmartResults(query, data) {
  const results = Array.isArray(data.results) ? data.results.filter((r) => r && r.task) : [];
  smartHitsById = new Map(results.map((r) => [r.task.id, r.task]));
  const cost = typeof data.cost_usd === "number" ? `$${data.cost_usd.toFixed(3)}` : "";
  el.smartCost.textContent = cost;
  el.smartCost.title = typeof data.scanned === "number" ? `${plural(data.scanned, "task")} read` : "";
  el.smartStatus.textContent = "";
  el.smartResults.hidden = false;
  el.smartEmpty.hidden = results.length !== 0;
  el.smartList.textContent = "";
  const words = searchWords(query);
  for (const r of results) {
    const li = createSearchHit(r.task, words);
    if (r.reason) {
      const why = document.createElement("span");
      why.className = "search-hit-reason";
      why.textContent = r.reason;
      li.firstElementChild.appendChild(why);
    }
    el.smartList.appendChild(li);
  }
  announce(results.length === 0 ? "Smart search: no matches" : `Smart search: ${results.length} ${results.length === 1 ? "match" : "matches"}`);
}

el.smartBtn.addEventListener("click", runSmartSearch);
el.smartCancel.addEventListener("click", () => {
  resetSmartSearch();
  el.smartStatus.textContent = "Cancelled.";
  el.smartBtn.focus();
});

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
      permission_mode: currentPermissionMode(),
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
