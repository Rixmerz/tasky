// Tasky dashboard client. No build step, no dependencies.

const POLL_MS = 2000;
const DONE_PAGE_SIZE = 100;
const TOKEN_STORAGE_KEY = "tasky.token";
const SESSION_ID_RE = /^[A-Za-z0-9-]{1,64}$/;
const UNSAFE_CWD_CHARS = /['\\\x00-\x1f\x7f]/;

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
  default: "Runs unattended, so tools that need approval are refused.",
  acceptEdits: "File edits are allowed; other tools that need approval are refused.",
  plan: "Read-only: Claude plans the work and changes nothing.",
  bypassPermissions: "Every tool runs without asking. Use only in a sandbox or throwaway checkout.",
};

const BYPASS_DISABLED_HINT = "Disabled. Start the dashboard with TASKY_ALLOW_BYPASS=1 to allow it.";

/** @typedef {{id:number,session_id:string|null,parent_id:number|null,kind:string,title:string,body:string,status:string,result:string|null,cwd:string|null,project:string,prompt_id:string|null,external_id:string|null,agent_id:string|null,source:string,position:number|null,created_at:string,started_at:string|null,finished_at:string|null}} Task */

class UnauthorizedError extends Error {}
class NetworkError extends Error {}
class NotVerifiedError extends Error {}

// ---------- app state ----------

let state = { rev: -1, sessions: [], tasks: [], config: { queue_prefix: "++", max_chain: 5, port: 7733, allow_bypass: false } };
let tasksById = new Map();
let sessionsById = new Map();
let projectFilter = "";
let doneShown = DONE_PAGE_SIZE;
let pollTimer = null;
let pollInFlight = false;
let refreshInFlight = false;
let accessDenied = false;
let verified = false;
let token = null;
let memoryToken = null;

// ---------- DOM refs ----------

const el = {
  accessState: document.getElementById("access-state"),
  reconnecting: document.getElementById("reconnecting-status"),
  topbar: document.getElementById("topbar"),
  main: document.getElementById("main"),
  errorBanner: document.getElementById("error-banner"),
  errorBannerText: document.getElementById("error-banner-text"),
  errorBannerDismiss: document.getElementById("error-banner-dismiss"),
  announcer: document.getElementById("announcer"),
  projectFilter: document.getElementById("project-filter"),
  quickAddForm: document.getElementById("quick-add"),
  quickAddInput: document.getElementById("quick-add-input"),
  quickAddProject: document.getElementById("quick-add-project"),
  quickAddSession: document.getElementById("quick-add-session"),
  quickAddMore: document.getElementById("quick-add-more"),
  quickAddPrefix: document.getElementById("quick-add-prefix"),
  countNow: document.getElementById("count-now"),
  countAttention: document.getElementById("count-attention"),
  countQueue: document.getElementById("count-queue"),
  countDone: document.getElementById("count-done"),
  nowList: document.getElementById("now-list"),
  nowEmpty: document.getElementById("now-empty"),
  attentionList: document.getElementById("attention-list"),
  attentionEmpty: document.getElementById("attention-empty"),
  queueList: document.getElementById("queue-list"),
  queueEmpty: document.getElementById("queue-empty"),
  queueEmptyPrefix: document.getElementById("queue-empty-prefix"),
  doneList: document.getElementById("done-list"),
  doneEmpty: document.getElementById("done-empty"),
  doneCount: document.getElementById("done-count"),
  doneShowMore: document.getElementById("done-show-more"),
  importHistory: document.getElementById("import-history"),
  filterEmpty: document.getElementById("filter-empty"),
  filterEmptyProject: document.getElementById("filter-empty-project"),
  filterReset: document.getElementById("filter-reset"),
  clipboardFallback: document.getElementById("clipboard-fallback"),
  tplCard: document.getElementById("tpl-card"),
  tplChild: document.getElementById("tpl-child"),
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
    // The server is gone or unreachable; show it immediately rather than
    // waiting for the next poll's verifyServer() call to notice.
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
  el.main.hidden = true;
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

// ---------- polling ----------

async function poll() {
  if (pollInFlight || accessDenied) return;
  pollInFlight = true;
  try {
    const version = await apiGet("/api/version");
    if (version.rev !== state.rev) {
      await refresh();
    } else {
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

function applyState(next) {
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
  el.quickAddPrefix.textContent = next.config.queue_prefix;
  el.queueEmptyPrefix.textContent = next.config.queue_prefix;
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

function distinctProjects() {
  const byCwd = new Map();
  for (const t of state.tasks) {
    if (t.cwd) byCwd.set(t.cwd, projectLabel(t.cwd));
  }
  for (const s of state.sessions) {
    if (s.cwd) byCwd.set(s.cwd, projectLabel(s.cwd));
  }
  return byCwd;
}

function mostRecentActiveSession() {
  const active = state.sessions.filter((s) => s.state === "active");
  active.sort((a, b) => (a.last_seen_at < b.last_seen_at ? 1 : -1));
  return active[0] || null;
}

function activeSessionsForCwd(cwd) {
  return state.sessions.filter((s) => s.state === "active" && s.cwd === cwd);
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
  if (task.status === "done" || task.status === "failed" || task.status === "interrupted" || task.status === "cancelled") {
    return relativeTime(task.finished_at || task.created_at);
  }
  return relativeTime(task.created_at);
}

function childCountLabel(taskId) {
  const kids = childrenOf(taskId);
  if (kids.length === 0) return "";
  const delegations = kids.filter((k) => k.kind === "delegation").length;
  return delegations ? `${delegations} delegation${delegations === 1 ? "" : "s"}` : "";
}

// ---------- rendering: keyed list reconciliation ----------

/**
 * Reconciles `container`'s children against `items`, reusing existing DOM
 * nodes (preserving open <details>, focus, and typed text) when the key
 * still matches.
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
    leftover.remove();
  }
}

// ---------- card rendering ----------

function createCard(task) {
  const node = el.tplCard.content.firstElementChild.cloneNode(true);
  updateCard(node, task);
  return node;
}

function updateCard(node, task) {
  node.dataset.status = task.status;
  const details = node.querySelector(":scope > .card-head > .disclosure");
  const summary = details.querySelector(":scope > summary");
  const glyph = summary.querySelector(".status-glyph");
  const word = summary.querySelector(".status-word");
  const title = summary.querySelector(".title");
  const meta = summary.querySelector(".meta");

  glyph.textContent = STATUS_GLYPH[task.status] || "";
  word.textContent = STATUS_WORD[task.status] || task.status;
  // Colour + glyph alone is enough for running/queued/done/cancelled; the
  // brief calls out interrupted/failed as needing the word visible too.
  const wordVisible = task.status === "interrupted" || task.status === "failed";
  word.classList.toggle("visually-hidden", !wordVisible);
  title.textContent = task.title;

  const metaParts = [];
  if (task.cwd) metaParts.push(projectLabel(task.cwd));
  if (task.kind !== "prompt") metaParts.push(task.kind);
  const time = taskTimeLabel(task);
  if (time) metaParts.push(time);
  const childLabel = childCountLabel(task.id);
  if (childLabel) metaParts.push(childLabel);
  meta.textContent = metaParts.join(" · ");

  const expanded = details.querySelector(".expanded");
  const bodyEl = expanded.querySelector(".body");
  if (task.body && task.body !== task.title) {
    bodyEl.textContent = task.body;
    bodyEl.hidden = false;
  } else {
    bodyEl.textContent = "";
    bodyEl.hidden = true;
  }

  const resultDetails = expanded.querySelector(".result-details");
  const resultText = expanded.querySelector(".result-text");
  if (task.result) {
    resultText.textContent = task.result;
    resultDetails.hidden = false;
  } else {
    resultText.textContent = "";
    resultDetails.hidden = true;
  }

  renderChildren(expanded.querySelector(".children"), childrenOf(task.id));
  renderSessionLine(expanded.querySelector(".session-line"), task);
  renderRunPanel(node, task);
  renderActions(node, task);
  renderQueueControls(node, task);
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

function renderRunPanel(node, task) {
  const panel = node.querySelector(".run-panel");
  if (task.status !== "queued") {
    panel.hidden = true;
    return;
  }
  const select = panel.querySelector(".permission-mode");
  const bypassOption = select.querySelector('option[value="bypassPermissions"]');
  const bypassDisabledHint = panel.querySelector(".bypass-disabled-hint");
  const hint = panel.querySelector(".permission-hint");
  const warning = panel.querySelector(".bypass-warning");
  const submit = panel.querySelector(".run-submit");

  const allowBypass = !!state.config.allow_bypass;
  bypassOption.disabled = !allowBypass;
  bypassDisabledHint.hidden = allowBypass;

  const updateHint = () => {
    const mode = select.value;
    hint.textContent = PERMISSION_HINTS[mode] || "";
    const bypass = mode === "bypassPermissions";
    warning.hidden = !bypass;
    submit.textContent = bypass ? "Run without asking" : "Run in background";
    submit.classList.toggle("bypass", bypass);
  };
  select.onchange = updateHint;
  updateHint();

  submit.onclick = () => runTask(task.id, select.value);
}

function renderActions(node, task) {
  const actions = node.querySelector(".actions");
  const doneBtn = actions.querySelector(".action-done");
  const cancelBtn = actions.querySelector(".action-cancel");
  const deleteBtn = actions.querySelector(".action-delete");

  doneBtn.hidden = task.status !== "running" && task.status !== "interrupted" && task.status !== "failed";
  cancelBtn.hidden = task.status !== "queued" && task.status !== "running";

  doneBtn.onclick = () => updateTaskStatus(task.id, "done");
  cancelBtn.onclick = () => updateTaskStatus(task.id, "cancelled");

  // Don't clobber an in-progress confirm (window still open) on a refresh.
  if (deleteBtn.dataset.confirming !== "1") {
    deleteBtn.textContent = "Delete";
  }
  deleteBtn.onclick = () => {
    if (deleteBtn.dataset.confirming === "1") {
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

function renderQueueControls(node, task) {
  const controls = node.querySelector(".queue-controls");
  const positionLabel = node.querySelector(".queue-position");
  if (task.status !== "queued") {
    controls.hidden = true;
    positionLabel.hidden = true;
    return;
  }
  controls.hidden = false;
  const queued = topLevelTasks()
    .filter((t) => t.status === "queued" && matchesFilter(t))
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const index = queued.findIndex((t) => t.id === task.id);

  positionLabel.hidden = index === -1;
  positionLabel.textContent = index === -1 ? "" : `#${index + 1}`;

  const upBtn = controls.querySelector(".move-up");
  const downBtn = controls.querySelector(".move-down");
  const runToggle = controls.querySelector(".run-toggle");

  upBtn.disabled = index <= 0;
  downBtn.disabled = index === -1 || index >= queued.length - 1;

  upBtn.onclick = () => {
    if (index <= 0) return;
    reorderTask(task.id, queued[index - 1].id, upBtn);
  };
  downBtn.onclick = () => {
    if (index === -1 || index >= queued.length - 1) return;
    const beforeId = index + 2 < queued.length ? queued[index + 2].id : null;
    reorderTask(task.id, beforeId, downBtn);
  };
  runToggle.onclick = () => {
    const details = node.querySelector(":scope > .card-head > .disclosure");
    const panel = node.querySelector(".run-panel");
    details.open = true;
    panel.hidden = !panel.hidden;
    if (!panel.hidden) panel.querySelector(".permission-mode").focus();
  };
}

// ---------- group rendering ----------

function renderAll() {
  renderProjectFilterOptions();
  renderQuickAddOptions();

  const visible = topLevelTasks().filter(matchesFilter);
  const running = visible.filter((t) => t.status === "running");
  const queued = visible.filter((t) => t.status === "queued").sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const attention = visible.filter((t) => t.status === "interrupted" || t.status === "failed");
  const doneAll = visible
    .filter((t) => t.status === "done" || t.status === "cancelled")
    .sort((a, b) => {
      const at = a.finished_at || a.created_at;
      const bt = b.finished_at || b.created_at;
      return at < bt ? 1 : at > bt ? -1 : 0;
    });

  el.countNow.textContent = String(running.length);
  el.countAttention.textContent = String(attention.length);
  el.countQueue.textContent = String(queued.length);
  el.countDone.textContent = String(doneAll.length);
  el.doneCount.textContent = String(doneAll.length);

  el.nowEmpty.hidden = running.length !== 0;
  reconcileList(el.nowList, running, (t) => t.id, createCard, updateCard);

  el.attentionEmpty.hidden = attention.length !== 0;
  reconcileList(el.attentionList, attention, (t) => t.id, createCard, updateCard);

  el.queueEmpty.hidden = queued.length !== 0;
  reconcileList(el.queueList, queued, (t) => t.id, createCard, updateCard);

  const doneVisible = doneAll.slice(0, doneShown);
  el.doneEmpty.hidden = doneAll.length !== 0;
  reconcileList(el.doneList, doneVisible, (t) => t.id, createCard, updateCard);
  el.doneShowMore.hidden = doneAll.length <= doneShown;
  el.doneShowMore.onclick = () => {
    doneShown += DONE_PAGE_SIZE;
    renderAll();
  };

  const noTasksAtAll = state.tasks.length > 0 && visible.length === 0 && projectFilter;
  el.filterEmpty.hidden = !noTasksAtAll;
  if (noTasksAtAll) {
    el.filterEmptyProject.textContent = projectLabel(projectFilter);
  }
}

// Compares a <select>'s current options against [value, label] entries
// without touching the DOM, so an unchanged list never forces a rebuild.
function optionsMatch(select, entries) {
  const opts = select.options;
  if (opts.length !== entries.length) return false;
  for (let i = 0; i < opts.length; i++) {
    if (opts[i].value !== entries[i][0] || opts[i].textContent !== entries[i][1]) return false;
  }
  return true;
}

function setOptions(select, entries) {
  select.textContent = "";
  for (const [value, label] of entries) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
}

function renderProjectFilterOptions() {
  // Rebuilding closes an open dropdown under the user; skip while focused,
  // and skip entirely when the option list did not actually change.
  if (document.activeElement === el.projectFilter) return;
  const projects = distinctProjects();
  const entries = [["", "All projects"], ...projects];
  if (!optionsMatch(el.projectFilter, entries)) {
    const current = el.projectFilter.value;
    setOptions(el.projectFilter, entries);
    el.projectFilter.value = projects.has(current) || current === "" ? current : "";
  }
  projectFilter = el.projectFilter.value;
}

function renderQuickAddOptions() {
  const projects = distinctProjects();
  const entries = Array.from(projects);
  if (document.activeElement !== el.quickAddProject && !optionsMatch(el.quickAddProject, entries)) {
    const current = el.quickAddProject.value;
    setOptions(el.quickAddProject, entries);
    let defaultCwd = current;
    if (!defaultCwd || !projects.has(defaultCwd)) {
      defaultCwd = projectFilter || (mostRecentActiveSession() || {}).cwd || "";
    }
    if (defaultCwd && projects.has(defaultCwd)) {
      el.quickAddProject.value = defaultCwd;
    } else if (projects.size > 0) {
      el.quickAddProject.value = projects.keys().next().value;
    }
  }
  renderQuickAddSessions();
}

function renderQuickAddSessions() {
  if (document.activeElement === el.quickAddSession) return;
  const cwd = el.quickAddProject.value;
  const sessions = activeSessionsForCwd(cwd);
  const entries = [["", "No session"], ...sessions.map((s) => [s.id, s.title || s.id])];
  if (!optionsMatch(el.quickAddSession, entries)) {
    const current = el.quickAddSession.value;
    setOptions(el.quickAddSession, entries);
    el.quickAddSession.value = sessions.some((s) => s.id === current) ? current : "";
  }
}

// ---------- mutations ----------

async function updateTaskStatus(id, status) {
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { status });
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

async function runTask(id, permissionMode) {
  try {
    await apiMutate("POST", `/api/tasks/${id}/run`, { permission_mode: permissionMode });
    await refresh();
  } catch (err) {
    handleApiError(err);
  }
}

async function reorderTask(id, beforeId, focusButton) {
  const focusSelector = focusButton.classList.contains("move-up") ? ".move-up" : ".move-down";
  try {
    await apiMutate("PATCH", `/api/tasks/${id}`, { before_id: beforeId });
    await refresh();
    const card = document.querySelector(`[data-key="${id}"]`);
    const btn = card && card.querySelector(focusSelector);
    if (btn && !btn.disabled) btn.focus();
  } catch (err) {
    handleApiError(err);
  }
}

async function toggleAutoPull(sessionId, value) {
  try {
    await apiMutate("PATCH", `/api/sessions/${sessionId}`, { auto_pull: value });
    await refresh();
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
  } catch (err) {
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

// ---------- event wiring ----------

el.projectFilter.addEventListener("change", () => {
  projectFilter = el.projectFilter.value;
  doneShown = DONE_PAGE_SIZE;
  renderAll();
});

el.quickAddProject.addEventListener("change", renderQuickAddSessions);

el.quickAddMore.addEventListener("click", () => {
  const expanded = el.quickAddForm.classList.toggle("expanded");
  el.quickAddMore.setAttribute("aria-expanded", String(expanded));
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
  const cwd = el.quickAddProject.value;
  const sessionId = el.quickAddSession.value;
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

document.addEventListener("keydown", (evt) => {
  const active = document.activeElement;
  const inField = active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT" || active.isContentEditable);
  if (inField) return;
  if (evt.key === "n" || evt.key === "/") {
    evt.preventDefault();
    el.quickAddInput.focus();
  }
});

// ---------- boot ----------

initToken();
if (token) {
  startPolling();
} else {
  showAccessState();
}
