/* Jarvis console window — vanilla JS, classic <script>, no build step.
   Talks to the same-origin API only (docs/CONSOLE_DESIGN.md §10):
     GET  /api/console                     full state
     GET  /api/console?lite=1&rev=<n>      10 Hz feed
     POST /api/console        {action, params}
     POST /api/console/mode   {mode}
     POST /api/console/patch  {action, variant?, csv?}
     POST /api/console/save   {name}   ·   POST /api/console/load {name}
     GET  /api/fixtures?q=…               fixture search
*/
"use strict";

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------------ api */

/* Control-API token.  Needed when the server is bound to a non-loopback
 * address (see config.requires_token): without it a second browser
 * cannot reach /api/console, which is intentional - that API can put
 * real DMX on a wire.  Stored locally so it survives a reload. */
const TOKEN_KEY = "jarvis.token";

function apiToken() {
  return window.sessionStorage.getItem(TOKEN_KEY) || "";
}

function setApiToken(t) {
  if (t) window.sessionStorage.setItem(TOKEN_KEY, t);
  else window.sessionStorage.removeItem(TOKEN_KEY);
}

async function api(path, body) {
  const headers = { "Content-Type": "application/json" };
  if (apiToken()) headers["X-Jarvis-Token"] = apiToken();
  const opts = body !== undefined
    ? { method: "POST", headers: headers, body: JSON.stringify(body) }
    : { headers: headers };
  const resp = await fetch(path, opts);
  if (resp.status === 401) {
    const t = window.prompt(
      "This desk needs an access token.\n"
      + "Set CONSOLE_TOKEN in jarvis/.env and enter it here:");
    if (t) { setApiToken(t.trim()); return api(path, body); }
    throw new Error("unauthorised - no token");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
  return data;
}

/* ---------------------------------------------------------------- state */

let S = null;                 // last full state (§10 GET /api/console)
let patchRev = -1;            // server patch revision we know; -1 = unknown
let patchRowSig = "";
// null, not "": an empty patch hashes to the empty string, and "" never
// differs from "", so on a cold start with nothing patched the visualiser
// was never built at all and the operator was left staring at an empty
// black box where the rig should be.
let vizSig = null;
let groupSig = null;
let palSig = null;
let preSig = null;
let pbStructSig = null;
let showsSig = null;
let selSig = null;
let fxSig = null;
let lastAnchor = null;        // shift-click anchor for head ranges
let viz = null;
let vizFixtures = [];
let feedFails = 0;
let feeding = false;
let lastGoodFeed = 0;           // ms timestamp of the last good poll
let lastSeenErrors = 0;          // output.errors, to spot a NEW failure
let lastErrorMsg = "";
let reloadTimer = 0;
let pbCards = {};
let padDown = false;          // a finger is on the pan/tilt pad
const looks = Object.create(null);   // head_no -> {hex, a} (live engine look)
const dragging = new Set();          // ranges the user is dragging right now

const SWATCHES = ["#ff2d55", "#ff8a2a", "#ffd23f", "#4ade80",
                  "#37d0f5", "#4f7cff", "#a855f7", "#ff4fd8"];
const HEX_RE = /^#?[0-9a-fA-F]{6}$/;

function clampInt(v, lo, hi, dflt) {
  const n = Math.round(Number(v));
  if (!Number.isFinite(n)) return dflt;
  return Math.max(lo, Math.min(hi, n));
}

function setRange(el, v) {
  if (!el || dragging.has(el)) return;
  const s = String(v);
  if (el.value !== s) el.value = s;
}

function setVal(el, v) {
  if (!el || document.activeElement === el) return;
  const s = String(v);
  if (el.value !== s) el.value = s;
}

function trackDragging(el) {
  el.addEventListener("pointerdown", () => dragging.add(el));
  el.addEventListener("blur", () => dragging.delete(el));
}

window.addEventListener("pointerup", () => dragging.clear());
window.addEventListener("pointercancel", () => dragging.clear());

/* ------------------------------------------------------------- messages */

let errTimer = 0;
let errKind = null;

// Persistent, dismissible failures.  A lighting desk that silently stops
// sending is worse than one that is obviously broken, so anything that
// means "the rig is not being driven" stays on screen until it is fixed or
// the operator says so - instead of auto-hiding after eight seconds, which
// is exactly how long it takes to look away.
const stickyErrs = new Map();

// ms = 0 means "do not auto-hide".
function showErr(msg, kind, cls, ms) {
  const el = $("#err-strip");
  $("#err-text").textContent = msg;
  el.className = cls === "info" ? "info" : "";
  errKind = kind || "action";
  clearTimeout(errTimer);
  const hold = ms === 0 ? 0 : (ms || 8000);
  if (hold) {
    errTimer = setTimeout(() => {
      el.classList.add("hidden");
      stickyErrs.delete(kind || "action");
    }, hold);
  } else {
    el.classList.remove("hidden");
  }
}

// Register a failure that must survive until it is cleared.
function showSticky(kind, msg) {
  if (stickyErrs.get(kind) === msg) return;
  stickyErrs.set(kind, msg);
  const joined = [...stickyErrs.values()].join("   |   ");
  showErr(joined, "sticky", "", 0);
  $("#err-dismiss").classList.remove("hidden");
}

function clearSticky(kind) {
  if (!stickyErrs.has(kind)) return;
  stickyErrs.delete(kind);
  if (!stickyErrs.size) hideErr();
  else showErr([...stickyErrs.values()].join("   |   "), "sticky", "", 0);
}

function hideErr(kind) {
  if (kind && errKind !== kind) return;
  clearTimeout(errTimer);
  $("#err-strip").classList.add("hidden");
  errKind = null;
}

/* ------------------------------------------------- cross-window signals */

let ch = null;
if (typeof BroadcastChannel !== "undefined") {
  try { ch = new BroadcastChannel("jarvis"); } catch (e) { ch = null; }
}

function broadcast(type, extra) {
  if (!ch) return;
  try { ch.postMessage(Object.assign({ type: type }, extra || {})); } catch (e) { /* ignore */ }
}

if (ch) {
  ch.onmessage = (e) => {
    const m = (e && e.data) || {};
    if (m.type === "mode" || m.type === "patch" ||
        m.type === "layout" || m.type === "show") loadState();
  };
}

/* ---------------------------------------------------------- state + feed */

async function loadState() {
  try {
    const d = await api("/api/console");
    S = d;
    patchRev = -1;                 // next lite poll must include heads
    lookSeq = -1;                  // and the look feed a fresh snapshot
    feedFails = 0;
    lookFails = 0;
    hideErr("feed");
    hideErr("state");
    renderAll();
    return d;
  } catch (err) {
    showErr("state: " + err.message, "state");
    if (!S) setTimeout(loadState, 2500);   // cold start with the server down
    return null;
  }
}

function scheduleReload() {
  if (reloadTimer) return;
  reloadTimer = setTimeout(() => { reloadTimer = 0; loadState(); }, 150);
}

async function feed() {
  if (!S || feeding) return;
  feeding = true;
  try {
    const d = await api("/api/console?lite=1&rev=" + patchRev);
    if (feedFails) { feedFails = 0; clearSticky("feed"); }
    lastGoodFeed = Date.now();
    applyLite(d);
  } catch (err) {
    feedFails++;
    // Persistent, not an 8-second toast.  The operator has to be able to
    // look away from the screen and still find out that the desk has lost
    // the server - and at 10 Hz a dead server costs 30 requests a second
    // that go nowhere, so back off once it is clearly gone.
    showSticky("feed", "SERVER UNREACHABLE — the desk has lost contact ("
      + err.message + ")");
    if (feedFails === 4) showSticky("feed", "SERVER UNREACHABLE ("
      + err.message + ")");
  } finally {
    feeding = false;
  }
}

function applyLite(d) {
  if (!S || !d) return;
  if (d.mode) S.mode = d.mode;
  if (typeof d.dry_run === "boolean") S.dry_run = d.dry_run;
  if (typeof d.live === "boolean") S.live = d.live;
  if (typeof d.master === "number") S.master = d.master;
  if (typeof d.blackout === "boolean") S.blackout = d.blackout;
  // The lock and the per-head limits ride the hot feed too: the header
  // has to say LOCKED the instant it becomes locked, and the LIMITS
  // panel has to show a limit that was just set - not on the next full
  // reload, which is up to a second away.
  if (d.lock !== undefined) S.lock = d.lock;
  if (d.lock_has_password !== undefined) {
    S.lock_has_password = d.lock_has_password;
  }
  // Repaint from HERE, where S.lock has just changed, rather than from
  // renderPatch - which only runs when the patch itself changes, so a
  // lock set from the command line or the API would leave the header
  // reading DESIGN while the desk was refusing every edit.
  if (d.lock !== undefined) renderLock();
  // ...and so does the dry-run button, for the same reason.
  if (typeof d.dry_run === "boolean") renderStatus();
  if (Array.isArray(d.selected)) S.selected = d.selected;
  if (d.output) S.output = Object.assign({}, S.output || {}, d.output);
  if (Array.isArray(d.fx)) S.fx = d.fx;          // running effects list
  // The programmer rides the hot feed, not just the full reload.  Without
  // this the intensity fader and colour swatch were re-rendered from a
  // value up to a second old, so a control the operator was actively
  // dragging snapped backwards under their finger ten times a second.
  if (d.programmer) S.programmer = d.programmer;
  // Undo state rides the hot feed, not just the full reload: the buttons
  // have to light up the instant an edit lands, or the operator learns to
  // press Ctrl+Z on faith rather than because the desk said it was there.
  if (d.undo) { S.undo = d.undo; renderUndo(S); }
  // The attribute grid's structure is fetched on a selection change; its
  // VALUES come from this feed, so a row the operator is dragging updates
  // immediately instead of waiting for a refetch.
  renderAttributes();
  // Palettes, presets and groups live on the hot feed now.  They used to
  // arrive only with a full reload triggered by a hand-maintained list of
  // "structural" actions, so recording a preset appeared to do nothing -
  // and a second browser tab never saw a new look at all.
  if (d.palettes) { S.palettes = d.palettes; palSig = null; renderPalettes(); }
  if (Array.isArray(d.presets)) { S.presets = d.presets; preSig = null; renderPresets(); }
  if (Array.isArray(d.groups)) { S.groups = d.groups; renderGroups(); }

  // patch revision first: the "heads" decision below uses the OLD revision
  const knownRev = patchRev;
  if (typeof d.patch_rev === "number" && d.patch_rev !== patchRev) {
    patchRev = d.patch_rev;
    if (knownRev !== -1) scheduleReload();   // changed behind our back
  }

  if (Array.isArray(d.heads)) {
    // A patch change ships the full head list (structure); light itself
    // comes from the look feed, which runs regardless of the patch rev.
    applyHeads(d.heads);
  }

  renderPills();
  renderSelection();
  renderProgrammer();
  renderFx();
  renderStatus();
  if (Array.isArray(d.playbacks)) applyLitePlaybacks(d.playbacks);
}

/* --------------------------------------------------- live drag updates
 *
 * A control being dragged has to reach the desk WHILE it is being
 * dragged.  Two ways this was wrong, and both are invisible until you
 * actually drag something:
 *
 *   - the pan/tilt pad sent on `pointerup` only, so the head stayed put
 *     for the whole drag and jumped when the finger came off;
 *   - the faders used a TRAILING debounce, which sends nothing at all
 *     while the pointer keeps moving and then fires once when it stops.
 *     A debounce is the right tool for a keystroke and the wrong tool for
 *     a drag.
 *
 * Sending on every pointermove is no better: a pointer reports well over
 * 100 times a second and each call is a round trip.  So: send at a fixed
 * rate during the drag, and always flush the final value - the light
 * tracks the control and never sticks on the last-but-one position.
 *
 * 45 ms is ~22 updates a second, deliberately just above the 20 Hz light
 * feed, so the operator is never ahead of what they can see.
 */
const LIVE_MS = 45;

function liveSender(fn, ms) {
  const gap = ms || LIVE_MS;
  let last = 0, timer = 0, pending = null;
  const fire = () => {
    last = Date.now();
    timer = 0;
    const v = pending;
    pending = null;
    fn(v);
  };
  return {
    push(v) {
      pending = v;
      const wait = gap - (Date.now() - last);
      if (wait <= 0) {
        if (timer) { clearTimeout(timer); timer = 0; }
        fire();
      } else if (!timer) {
        timer = setTimeout(fire, wait);
      }
    },
    // on release: never drop the value the operator actually let go at
    flush() {
      if (timer) { clearTimeout(timer); timer = 0; }
      if (pending !== null) fire();
    },
  };
}

/* -------------------------------------------------------------- actions */

/* Actions that change the PATCH or the show structure.  These need a
 * full state reload; everything else (programmer, cues, output) is
 * reflected by the result payload plus the live feed, so a slider drag
 * costs ONE round trip instead of three. */
const STRUCTURAL = new Set([
  "patch_from_csv", "patch_from_layout", "patch_clear", "add_heads",
  "remove_heads", "auto_patch", "set_address", "import_scan",
  "load_show", "record_cue", "include_palette", "import_show",
  "record_palette", "group_create", "group_delete",
]);

/* The 3D drag commits a stage position, not a pan/tilt value. */
async function doAction(action, params, opts) {
  opts = opts || {};
  let data;
  try {
    data = await api("/api/console", { action: action, params: params || {} });
  } catch (err) {
    if (!opts.quiet) showErr(action + ": " + err.message);
    return { ok: false, error: err.message };
  }
  const r = data.result || { ok: true };
  if (S && data.mode) S.mode = data.mode;
  if (S && typeof data.dry_run === "boolean") S.dry_run = data.dry_run;
  if (S && typeof data.live === "boolean") S.live = data.live;
  if (r.ok === false && !opts.quiet) {
    showErr(action + ": " + (r.error || "rejected"));
  }
  if (opts.structural || STRUCTURAL.has(action)) {
    await loadState();
  } else {
    // Cheap path: patch the local state from what the server just told
    // us, so the UI is correct immediately and the 100 ms feed confirms.
    if (Array.isArray(r.selected)) S.selected = r.selected;
    if (r.playback !== undefined) applyOnePlayback(r);
    if (r.selected !== undefined) renderSelection();
    if (r.output || r.live !== undefined) renderStatus();
    renderPills();
    if (["locate", "set_intensity", "set_colour", "set_attribute",
      "set_position", "record_cue", "cue_go", "run_fx", "clear_programmer",
    ].includes(action)) {
      lookSeq = -1;                 // resync the look feed on the next tick
    }
  }
  if (opts.broadcast && r.ok !== false) broadcast(opts.broadcast);
  return r;
}

/* Fold one action's playback result into the local state, so the console
 * is correct immediately instead of waiting for the 100 ms feed.
 *
 * Two shapes have to be reconciled.  `cue_go` reports the current cue as
 * `cue: <number>` plus a sibling `name`, while the feeds report it as an
 * object `{n, name, fade_s}`.  The state stores the object form (it is
 * what the cue row renders), so a number is folded into an object rather
 * than assigned over it - otherwise one GO would leave the card reading
 * "cue undefined" until the next poll.  The same applies to the stack
 * index: `cue_go` returns the cue NUMBER, not the index, so it is derived
 * from the stack.
 *
 * This touches the STATE objects only; renderPlaybacks() then redraws.
 */
function applyOnePlayback(row) {
  if (!S || !row || !S.playbacks) return;
  const pb = S.playbacks.find((p) => p.n === row.playback);
  if (!pb) return;
  const stack = Array.isArray(pb.stack) ? pb.stack : [];
  if (typeof row.active === "boolean") pb.active = row.active;
  if (typeof row.level === "number") pb.level = row.level;
  if (row.cue && typeof row.cue === "object") {
    pb.cue = row.cue;
  } else if (row.cue !== undefined && row.cue !== null) {
    const n = Number(row.cue);
    if (Number.isFinite(n)) {
      const fromStack = stack.find((c) => c.n === n) || {};
      pb.cue = {
        n: n,
        name: row.name || fromStack.name || "",
        fade_s: fromStack.fade_s !== undefined ? fromStack.fade_s : 0,
      };
    }
  }
  if (typeof row.index === "number") {
    pb.index = row.index;
  } else if (pb.cue) {
    const at = stack.findIndex((c) => c.n === pb.cue.n);
    if (at >= 0) pb.index = at;
  }
  if (row.follow) pb.follow = row.follow;
  renderPlaybacks();
}

/* ================================================================ render */

function renderAll() {
  if (!S) return;
  renderPills();
  renderPatch();
  renderGroups();
  renderPalettes();
  renderPresets();
  renderAttributes();
  renderProgrammer();
  renderFx();
  renderSelection();
  renderPlaybacks();
  renderShows();
  renderStatus();
}

/* ---------------------------------------------------------------- header */

function renderPills() {
  if (!S) return;
  const dry = !!S.dry_run;
  const dryPill = $("#pill-dry");
  // ARMED means the desk WILL put real DMX on the wire, and it was painted
  // with the success-green `.ok` class while the harmless not-yet-running
  // state got the neutral one.  The most dangerous state on the desk was
  // the only green thing in the header.  State is carried by the WORD, not
  // by hue alone, so a colour-blind operator can read it too.
  dryPill.textContent = dry ? "DRY RUN" : "OUTPUT ARMED";
  dryPill.className = "pill " + (dry ? "warn" : "danger");
  dryPill.title = dry
    ? "commands are simulated — nothing leaves the app (CONSOLE_DRY_RUN=false to arm)"
    : "OUTPUT ARMED — this desk will send real Art-Net. GO LIVE still needs a confirm step";

  const live = !!S.live;
  const livePill = $("#pill-live");
  livePill.textContent = live ? "SENDING ●" : "NOT SENDING ○";
  livePill.className = live ? "pill danger" : "pill";
  livePill.title = live
    ? "LIVE — Art-Net frames are leaving this machine right now"
    : "not sending — the output thread is stopped";
  livePill.setAttribute("role", "status");
  livePill.setAttribute("aria-live", "polite");

  const go = $("#btn-golive");
  const goLabel = live ? "STOP OUTPUT" : "GO LIVE";
  if (go.textContent !== goLabel) go.textContent = goLabel;
  go.className = "cbtn " + (live ? "danger" : "go");
  go.title = live ? "stop sending Art-Net frames"
    : dry ? "GO LIVE starts the engine in simulated mode (dry run)"
    : "GO LIVE sends real Art-Net frames — a confirm step is required";
  go.setAttribute("aria-pressed", live ? "true" : "false");

  // A cue stack whose rows point at heads that are no longer patched is
  // the "playback does nothing" report.  The engine computes it (it has
  // the cue rows); we just say it out loud.
  const stale = (S && S.stale_heads) || [];
  const warn = $("#stale-warn");
  if (warn) {
    warn.classList.toggle("hidden", !stale.length);
    if (stale.length) {
      warn.textContent = "cue data still references unpatched head"
        + (stale.length > 1 ? "s " : " ") + stale.join(", ")
        + " — re-record or re-import to light them";
    }
  }
}

/* ---------------------------------------------------------------- patch */

function rowSigOf(h) {
  return [h.head_no, h.name, h.universe, h.address, h.channels,
          h.role, h.kind, h.mapped].join(",");
}

/* ------------------------------------------------------- patch filter ---
 * The list is the ONLY way to see what is patched, and on a real rig it is
 * 200 rows of near-identical text.  The filter is what makes it navigable,
 * and "select shown" is what makes it a SELECTION tool: narrowing to the
 * six movers and taking all of them is the gesture that did not exist.
 * Filtering is client-side and instant - it never round-trips, so typing
 * feels immediate rather than waiting on a desk that is also driving DMX.
 */
function headMatchesFilter(h, words) {
  if (!words.length) return true;
  const hay = (headLabel(h) + " " + h.model + " " + h.manufacturer + " "
    + h.mode + " U" + h.universe + "." + h.address + " " + h.kind
    + (h.role || "") + " " + (h.mapped ? "" : "unmapped")).toLowerCase();
  return words.every((w) => hay.indexOf(w) >= 0);
}

function filterWords() {
  const el = $("#head-filter");
  return el ? el.value.trim().toLowerCase().split(/\s+/).filter(Boolean) : [];
}

function visibleHeads() {
  const words = filterWords();
  return (S.patch || []).filter((h) => headMatchesFilter(h, words));
}

function renderPatch() {
  if (!S) return;
  const patch = S.patch || [];
  const rowSig = patch.map(rowSigOf).join("|");
  if (rowSig !== patchRowSig) {
    patchRowSig = rowSig;
    rebuildHeadList(patch);
    selSig = null;                 // fresh rows: repaint the selection too
  }
  const shown = visibleHeads().length;
  const words = filterWords();
  $("#patch-count").textContent = words.length
    ? shown + " of " + patch.length + " heads · " + (S.universes || 0) + "U"
    : patch.length + " heads · " + (S.universes || 0) + "U";
  buildVizIfChanged();
  renderVizEmptyState();
  renderSelection();
  refreshArrange();
  renderLock();
  refreshLimits();
}

function rebuildHeadList(patch) {
  const list = $("#head-list");
  list.replaceChildren();
  if (!patch.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "patch is empty — add heads or load a layout";
    list.appendChild(empty);
    return;
  }
  const words = filterWords();
  let shown = 0;
  patch.forEach((h) => {
    if (!headMatchesFilter(h, words)) return;
    shown += 1;
    const row = headRow(h);
    // A per-row shortcut to "every head like this one", which is the
    // gesture the operator reaches for on a rig of one model repeated.
    const sim = document.createElement("button");
    sim.className = "simbtn";
    sim.textContent = "×n";
    sim.title = "select every head that is also a " + h.model;
    sim.onclick = (e) => {
      e.stopPropagation();
      doAction("select_similar", { model: h.model });
    };
    row.appendChild(sim);
    list.appendChild(row);
  });
  if (!shown) {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = "nothing matches — try fewer words";
    list.appendChild(none);
  }
}

/* What the filter searches, in one place.  A head's display name is its
   own `name` when set, and otherwise the fixture - so filtering on
   "intimidator" finds heads the operator renamed to "FOH spot 3". */
function headLabel(h) {
  return h.name || [h.manufacturer, h.model].filter(Boolean).join(" ");
}

function headRow(h) {
  const row = document.createElement("div");
  row.className = "hrow";
  row.dataset.head = h.head_no;

  const l1 = document.createElement("div");
  l1.className = "hline1";

  const selBtn = document.createElement("button");
  selBtn.className = "ibtn hsel";
  selBtn.title = "select (ctrl+click to add or remove, shift+click for a range)";
  // Every row's button used to be an identical "○" with an identical
  // title, so a screen reader announced 40 indistinguishable controls and
  // nothing exposed the selection state.  Name it, and expose the state.
  const label = "select head " + h.head_no + ", "
    + (h.name || [h.manufacturer, h.model].filter(Boolean).join(" ")
       || "head");
  selBtn.setAttribute("aria-label", label);
  selBtn.setAttribute("aria-pressed", "false");
  selBtn.textContent = "○";
  selBtn.onclick = (e) => {
    e.stopPropagation();
    selectHead(h.head_no, e.shiftKey, isToggleClick(e));
  };

  const no = document.createElement("span");
  no.className = "hno";
  no.textContent = "#" + h.head_no;

  const nm = document.createElement("span");
  nm.className = "hname";
  nm.textContent = h.name || [h.manufacturer, h.model].filter(Boolean).join(" ") || "head";
  nm.title = [h.manufacturer, h.model, h.mode].filter(Boolean).join(" · ");

  l1.append(selBtn, no, nm);

  if (h.mapped === false) {
    const warn = document.createElement("span");
    warn.className = "hwarn";
    warn.textContent = "⚠";
    warn.title = "no channel map — set attributes raw";
    l1.appendChild(warn);
  }

  const btns = document.createElement("span");
  btns.className = "hbtns";
  const edit = document.createElement("button");
  edit.className = "ibtn";
  edit.textContent = "✎";
  edit.title = "edit address";
  edit.onclick = () => startAddrEdit(row, h);
  const del = document.createElement("button");
  del.className = "ibtn danger";
  del.textContent = "✕";
  del.title = "remove this head";
  del.onclick = () => removeHead(h.head_no);
  btns.append(edit, del);
  l1.appendChild(btns);

  const l2 = document.createElement("div");
  l2.className = "hline2";
  const addr = document.createElement("span");
  addr.className = "haddr";
  addr.textContent = "U" + h.universe + "." + String(h.address).padStart(3, "0");
  l2.appendChild(addr);
  l2.appendChild(document.createTextNode(
    " · " + (h.role || "—") + " · " + (h.channels || 0) + "ch · " + (h.kind || "—")));

  const err = document.createElement("div");
  err.className = "herr hidden";

  row.append(l1, l2, err);
  // The whole row selects, not just the 22 px dot at its left edge.  On a
  // tablet that dot is not a target a finger can hit, and every other part
  // of the row - the name, the address, the channel count - was a dead
  // zone.  The inner controls stop propagation so they keep their own
  // meaning.
  row.classList.add("hclick");
  row.onclick = (e) => {
    if (e.target.closest("button, input, select, .addr-edit")) return;
    selectHead(h.head_no, e.shiftKey, isToggleClick(e));
  };
  return row;
}

function rowError(row, msg) {
  const el = row.querySelector(".herr");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
}

/* A head was dragged in the 3D view: update its row in place so the
 * coordinates on screen match the wire without a full reload. */
function updateHeadRow(r) {
  const row = document.querySelector('#head-list .hrow[data-head="'
    + r.head_no + '"]');
  if (!row) return;
  const sub = row.querySelectorAll(".hline2 span, .hline2")[1];
  const line = row.querySelector(".hline2");
  if (line && r.kind !== undefined) {
    const parts = line.textContent.split(" · ");
    if (parts.length >= 4) {
      parts[3] = r.kind;
      line.textContent = parts.join(" · ");
    }
  }
  void sub;
  // keep the local patch copy in step, so the 3D view is not rebuilt
  // from stale numbers on the next render
  if (S && Array.isArray(S.patch)) {
    const head = S.patch.find((h) => h.head_no === r.head_no);
    if (head) {
      head.x = r.x; head.y = r.y; head.z = r.z; head.kind = r.kind;
    }
  }
  if (viz && viz.setPositions) {
    const moves = {};
    moves[r.head_no] = { x: r.x, y: r.y, z: r.z, kind: r.kind };
    viz.setPositions(moves);
  }
}

function startAddrEdit(row, h) {
  const host = row.querySelector(".haddr");
  if (!host || host.dataset.editing) return;
  host.dataset.editing = "1";
  rowError(row, "");
  const prev = host.textContent;

  const wrap = document.createElement("span");
  wrap.className = "addr-edit";
  const u = document.createElement("input");
  u.type = "number"; u.min = "1"; u.value = h.universe; u.title = "universe (1-based)";
  const a = document.createElement("input");
  a.type = "number"; a.min = "1"; a.value = h.address; a.title = "address (1-based)";
  const ok = document.createElement("button");
  ok.className = "ibtn ok"; ok.textContent = "✓"; ok.title = "apply";
  const cancel = document.createElement("button");
  cancel.className = "ibtn"; cancel.textContent = "✕";
  wrap.append(u, a, ok, cancel);

  const close = () => {
    delete host.dataset.editing;
    wrap.remove();
    host.textContent = prev;   // a real change bumps rowSig → row rebuilt anyway
  };
  cancel.onclick = () => close();
  [u, a].forEach((el) => el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") ok.onclick();
    if (e.key === "Escape") close();
  }));
  ok.onclick = async () => {
    const uv = Number(u.value), ad = Number(a.value);
    if (!Number.isInteger(uv) || !Number.isInteger(ad) || uv < 1 || ad < 1) {
      rowError(row, "universe and address must be whole numbers ≥ 1");
      return;
    }
    const r = await doAction("set_address",
      { head: h.head_no, universe: uv, address: ad },
      { quiet: true, broadcast: "patch" });
    if (r.ok === false) {
      rowError(row, r.error || "address rejected");   // server names the clash
      return;
    }
    close();                                          // loadState redraws the row
  };
  host.replaceChildren(wrap);
  u.focus();
  u.select();
}

async function removeHead(n) {
  if (!window.confirm("Remove head #" + n + " from the patch?")) return;
  await doAction("remove_heads", { heads: [n] }, { broadcast: "patch" });
}

/* Selection from the patch list, and from the 3D view (same three rules
 * in both places, so one does not have to be relearned):
 *
 *   click             select just this one
 *   ctrl / cmd+click  add it, or take it out again
 *   shift+click       everything between this one and the last one clicked
 *
 * There was no way to add a single light: a plain click REPLACED the
 * selection and shift took a range, so picking four scattered heads meant
 * four clicks that each threw the previous three away, and the only way to
 * get more than one was to take everything in between them.  For a desk
 * where you program a look on a chosen subset, that made multi-select
 * effectively single-select.
 */
function selectHead(n, shift, toggle) {
  if (toggle) {
    const now = new Set(((S && S.selected) || []).map(Number));
    if (now.has(n)) now.delete(n);
    else now.add(n);
    if (!now.size) doAction("clear_selection", {});
    else doAction("select_heads", { heads: [...now] });
    return;                            // the anchor stays where it was
  }
  if (shift && lastAnchor !== null) {
    const lo = Math.min(lastAnchor, n), hi = Math.max(lastAnchor, n);
    doAction("select_heads", { head: lo, head_end: hi });
  } else {
    lastAnchor = n;
    doAction("select_heads", { head: n });
  }
}

// The modifier every list and every editor uses for "add to the selection".
function isToggleClick(e) {
  return !!(e && (e.ctrlKey || e.metaKey));
}

function renderSelection() {
  if (!S) return;
  const sel = S.selected || [];
  const sig = sel.join(",");
  if (sig === selSig) return;
  selSig = sig;
  const set = new Set(sel.map(Number));
  document.querySelectorAll("#head-list .hrow").forEach((row) => {
    const on = set.has(Number(row.dataset.head));
    row.classList.toggle("sel", on);
    row.setAttribute("aria-selected", on ? "true" : "false");
    const b = row.querySelector(".hsel");
    if (b) {
      b.classList.toggle("on", on);
      b.textContent = on ? "◉" : "○";
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  });
  // The 3D view is told what is selected, so a light picked in the view and
  // a row picked in the patch list are the same state.  One source of truth:
  // the selection.  Before this the view had a `picked` flag that nothing
  // ever set, so clicking a light highlighted the patch row and nothing
  // else - and the row highlight itself had no CSS rule.
  if (viz && viz.setSelected) viz.setSelected(sel);
  const line = sel.length
    ? "heads " + sel.join(", ") + (sel.length === 1 ? describeHead(sel[0]) : "")
    : "no heads selected";
  if ($("#sel-line").textContent !== line) $("#sel-line").textContent = line;
  const n = sel.length + " selected";
  if ($("#st-sel").textContent !== n) $("#st-sel").textContent = n;
}

// What a head IS, for the selection line: "heads 17" does not say whether
// that is the spot or a par, and the operator is about to edit it.
function describeHead(no) {
  const h = ((S && S.patch) || []).find((x) => x.head_no === Number(no));
  if (!h) return "";
  const bits = [];
  const name = [h.manufacturer, h.model].filter(Boolean).join(" ");
  if (name) bits.push(name);
  if (h.universe) bits.push("U" + h.universe + "." + String(h.address).padStart(3, "0"));
  return bits.length ? " \u00b7 " + bits.join(" \u00b7 ") : "";
}

/* --------------------------------------------------------------- groups */

function renderGroups() {
  if (!S) return;
  const groups = S.groups || [];
  const sig = groups.map((g) =>
    g.n + ":" + g.name + ":" + (g.heads || []).length).join("|");
  if (sig === groupSig) return;
  groupSig = sig;

  const box = $("#group-chips");
  box.replaceChildren();
  if (!groups.length) {
    const d = document.createElement("span");
    d.className = "muted small";
    d.textContent = "none — select heads, press +";
    box.appendChild(d);
    return;
  }
  groups.forEach((g) => {
    const chip = document.createElement("span");
    chip.className = "gchip";
    chip.title = "heads " + (g.heads || []).join(", ") + " — click to select the group";
    chip.onclick = () => doAction("select_group", { n: g.n });
    const txt = document.createElement("span");
    txt.textContent = g.name + " " + (g.heads || []).length;
    const x = document.createElement("button");
    x.className = "ibtn";
    x.textContent = "✕";
    x.title = "delete group";
    x.onclick = (e) => {
      e.stopPropagation();
      if (window.confirm('Delete group "' + g.name + '"?')) {
        doAction("group_delete", { n: g.n }, { broadcast: "patch" });
      }
    };
    chip.append(txt, x);
    box.appendChild(chip);
  });
}

async function createGroup() {
  const name = $("#group-name").value.trim();
  const heads = (S && S.selected) || [];
  if (!name) { showErr("group: type a name first"); return; }
  if (!heads.length) { showErr("group: select some heads first"); return; }
  const r = await doAction("group_create",
    { name: name, heads: heads.slice() }, { broadcast: "patch" });
  if (r.ok !== false) {
    $("#group-name").value = "";
    $("#group-form").classList.add("hidden");
  }
}

/* ------------------------------------------------------------ palettes */

/* ------------------------------------------------------------- presets ---
 * A palette is ONE attribute family ("House blue").  A preset is a
 * COMPLETE look - colour and beam and level together - which is what you
 * actually build with.  The app had no preset concept at all, so a look
 * meant setting every head by hand for every group.
 */
function renderPresets() {
  if (!S) return;
  const list = S.presets || [];
  const sig = list.map((p, i) =>
    (p && p.n !== undefined ? p.n : i) + ":" + ((p && p.name) || "")
    + ":" + Object.keys((p && p.values) || {}).length).join(",");
  if (sig === preSig) return;
  preSig = sig;

  const host = $("#presets");
  if (!host) return;
  host.replaceChildren();
  if (!list.length) {
    const none = document.createElement("span");
    none.className = "muted small";
    none.textContent = "— set something up, then +";
    host.appendChild(none);
    return;
  }
  list.forEach((p, i) => {
    const n = (p && typeof p.n === "number") ? p.n : i + 1;
    const roles = Object.keys((p && p.values) || {});
    const chip = document.createElement("button");
    chip.className = "pchip pre";
    // The attribute count is on the chip because the whole point of a
    // preset is that it holds MORE than one thing, and "Warm Wash"
    // alone does not tell you whether it is a colour or a whole look.
    chip.title = (p && p.name ? p.name + " — " : "")
      + roles.length + " attribute(s): " + roles.join(", ")
      + "\nclick to apply to the selection";
    const t = document.createElement("span");
    t.textContent = n + " " + ((p && p.name) || "look");
    chip.appendChild(t);
    const badge = document.createElement("i");
    badge.className = "pn";
    badge.textContent = String(roles.length);
    chip.appendChild(badge);
    chip.onclick = () => doAction("include_preset", { n: n });
    host.appendChild(chip);
  });
}

function wirePresets() {
  const open = $("#btn-preset");
  const form = $("#preset-form");
  if (open) open.onclick = () => {
    if (form) form.classList.toggle("hidden");
    const name = $("#preset-name");
    if (name && form && !form.classList.contains("hidden")) name.focus();
  };
  const cancel = $("#preset-cancel");
  if (cancel) cancel.onclick = () => form && form.classList.add("hidden");
  const ok = $("#preset-ok");
  if (ok) ok.onclick = async () => {
    const name = $("#preset-name");
    const label = (name && name.value.trim()) || "";
    const res = await doAction("record_preset", label ? { name: label } : {});
    if (res && res.ok !== false) {
      if (form) form.classList.add("hidden");
      if (name) name.value = "";
    }
  };
  const name = $("#preset-name");
  if (name) {
    name.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); $("#preset-ok").click(); }
      if (e.key === "Escape") { e.preventDefault(); form.classList.add("hidden"); }
    });
  }
}

function renderPalettes() {
  if (!S) return;
  const pals = S.palettes || {};
  const kinds = ["position", "colour", "beam"];
  const sig = kinds.map((k) =>
    (pals[k] || []).map((p, i) =>
      (p && p.n !== undefined ? p.n : i) + ":" + ((p && p.name) || "")).join(",")).join("|");
  if (sig === palSig) return;
  palSig = sig;

  const host = $("#palettes");
  host.replaceChildren();

  kinds.forEach((kind) => {
    const row = document.createElement("div");
    row.className = "pal-row";
    const lab = document.createElement("span");
    lab.className = "k";
    lab.textContent = kind;
    row.appendChild(lab);

    const list = pals[kind] || [];
    if (!list.length) {
      const none = document.createElement("span");
      none.className = "muted small";
      none.textContent = "—";
      row.appendChild(none);
    }
    list.forEach((p, i) => {
      const n = (p && typeof p.n === "number") ? p.n : i + 1;
      const chip = document.createElement("button");
      chip.className = "pchip";
      chip.title = "include this " + kind + " palette into the programmer";
      if (p && p.hex) {
        const dot = document.createElement("i");
        dot.style.background = p.hex;
        chip.appendChild(dot);
      }
      const t = document.createElement("span");
      t.textContent = (p && p.name) ? n + " " + p.name : String(n);
      chip.appendChild(t);
      chip.onclick = () => doAction("include_palette", { kind: kind, n: n });
      row.appendChild(chip);
    });

    const rec = document.createElement("button");
    rec.className = "ibtn";
    rec.textContent = "+";
    rec.title = "record a " + kind + " palette from the selection";
    rec.onclick = () => openPaletteForm(row, kind);
    row.appendChild(rec);
    host.appendChild(row);
  });
}

function openPaletteForm(row, kind) {
  if (row.nextElementSibling &&
      row.nextElementSibling.classList.contains("pal-form")) return;
  if (!((S && S.selected) || []).length) {
    showErr("palette: select some heads first");
    return;
  }
  const form = document.createElement("div");
  form.className = "inline-form full pal-form";
  const input = document.createElement("input");
  input.type = "text";
  input.maxLength = 32;
  input.placeholder = kind + " palette name";
  const ok = document.createElement("button");
  ok.className = "ibtn ok"; ok.textContent = "✓"; ok.title = "record";
  const cancel = document.createElement("button");
  cancel.className = "ibtn"; cancel.textContent = "✕"; cancel.title = "cancel";
  form.append(input, ok, cancel);
  row.insertAdjacentElement("afterend", form);
  input.focus();

  const close = () => form.remove();
  cancel.onclick = close;
  ok.onclick = async () => {
    const name = input.value.trim();
    if (!name) { showErr("palette: type a name"); input.focus(); return; }
    close();
    await doAction("record_palette", { kind: kind, name: name });
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") ok.onclick();
    if (e.key === "Escape") close();
  });
}

/* ----------------------------------------------------------- programmer */

// The values to show in the programmer controls, and whether every
// selected head actually agrees on them.
//
// The old version fell back to "the first head in the map", which need not
// be selected at all - so selecting a different head left the previous
// head's level sitting in the fader, reading as though it applied to the
// new one.  A desk that shows the wrong head's value is worse than one that
// shows nothing, because the operator trusts it.
function programmerValues() {
  const pv = (S && S.programmer && S.programmer.values) || {};
  const sel = (S && S.selected) || [];
  const roles = Object.create(null);
  let anyHead = 0, agree = 0;
  for (const n of sel) {
    const v = pv[String(n)] || pv[n];
    if (!v || !Object.keys(v).length) continue;
    anyHead++;
    let same = true;
    for (const role in roles) {
      if (v[role] !== roles[role]) { same = false; break; }
    }
    if (same) {
      for (const role in v) {
        if (!(role in roles)) { roles[role] = v[role]; agree++; }
      }
    } else {
      agree = -1;                      // mixed: no single value is honest
    }
  }
  return { values: (agree > 0 && anyHead) ? roles : {}, heads: anyHead,
           selected: sel.length, mixed: agree === -1 && anyHead > 0 };
}

function colourOf(vals) {
  if (typeof vals.colour === "string" && HEX_RE.test(vals.colour)) {
    return normHex(vals.colour);
  }
  if (["red", "green", "blue"].every((k) => typeof vals[k] === "number")) {
    return "#" + ["red", "green", "blue"]
      .map((k) => clampInt(vals[k], 0, 255, 0).toString(16).padStart(2, "0"))
      .join("");
  }
  return null;
}

function normHex(raw) {
  const s = String(raw || "").trim();
  if (!HEX_RE.test(s)) return null;
  return (s[0] === "#" ? s : "#" + s).toLowerCase();
}

function summaryOf(vals) {
  const parts = [];
  if (typeof vals.dimmer === "number") parts.push("dimmer " + vals.dimmer);
  const hex = colourOf(vals);
  if (hex) parts.push(hex);
  Object.keys(vals).sort().forEach((k) => {
    if (k === "dimmer" || k === "red" || k === "green" || k === "blue") return;
    parts.push(k + " " + vals[k]);
  });
  return parts.join(" · ");
}

function hasPanTilt() {
  const sel = new Set(((S && S.selected) || []).map(Number));
  if (!sel.size) return false;
  return (S.patch || []).some((h) =>
    sel.has(h.head_no) && h.map &&
    h.map.indexOf("pan") >= 0 && h.map.indexOf("tilt") >= 0);
}

function renderProgrammer() {
  if (!S) return;
  const master = clampInt(S.master, 0, 100, 100);
  setRange($("#master"), master);
  setVal($("#master-num"), master);

  const pv = programmerValues();
  const vals = pv.values;
  // A cleared programmer must read as 0, not as whatever was last there:
  // a fader that keeps showing a stale level tells the operator a head is
  // lit when the engine has nothing programmed for it.
  if (document.activeElement !== $("#intensity")) {
    setRange($("#intensity"), clampInt(vals.dimmer, 0, 100, 0));
    setVal($("#intensity-num"), clampInt(vals.dimmer, 0, 100, 0));
  }
  const hex = colourOf(vals);
  // The picker follows the ENGINE, not just the other way round, or it goes
  // stale the moment a colour arrives from somewhere else: a cue, a
  // palette, the agent, the undo button.  A control that disagrees with the
  // show is worse than no control, because it is trusted.
  //
  // But ONLY when the engine genuinely has a different colour.  Comparing
  // against `PICK.shown` rather than assigning unconditionally is the whole
  // point: the pick is sent through a 45 ms throttle, so for up to three feed
  // ticks the engine still holds the PREVIOUS colour - and writing that into
  // the box clobbered the colour the operator had just chosen, which then
  // snapped back.  Nudging brightness down appeared to do nothing.
  //
  // Also skipped while dragging or while the hex box has the focus, for the
  // same reason the pan/tilt pad never fights a finger.
  if (hex && !PICK.down && document.activeElement !== $("#hex-in")
      && hex !== PICK.shown) {
    $("#hex-in").value = hex;
    adoptHex(hex);
  }
  markSwatch($("#hex-in").value);
  renderPickInfo();

  $("#pad-wrap").classList.toggle("hidden", !hasPanTilt());
  if (!padDown && typeof vals.pan === "number" && typeof vals.tilt === "number") {
    movePad(vals.pan, vals.tilt);          // never fight a finger on the pad
  }
  // Say how many heads the value applies to, and when they disagree.  A
  // single number on a five-head selection is ambiguous: it looks like a
  // command, and it is not.  When only some of the selection holds a value
  // the count says so, because "1 head" next to a highlighted list of
  // eight is not an answer to "what will this change?".
  const n = pv.heads, total = pv.selected;
  const scope = !n
    ? ""
    : (total > 1 && n < total
        ? n + " of " + total + " heads"
        : (n === 1 ? "1 head" : n + " heads"))
      + (pv.mixed ? " · mixed values" : (n > 1 ? " · shared" : ""));
  const sum = summaryOf(vals);
  const line = scope ? (sum ? scope + " · " + sum : scope + " · no values")
                     : (sum || "no values");
  const box = $("#prog-sum");
  if (box.textContent !== line) box.textContent = line;
  box.classList.toggle("mixed", !!pv.mixed);
  const bo = !!S.blackout;
  $("#btn-blackout").classList.toggle("on", bo);
  const boLabel = bo ? "BLACKOUT ON" : "BLACKOUT";
  if ($("#btn-blackout").textContent !== boLabel) {
    $("#btn-blackout").textContent = boLabel;
  }
  $("#btn-blackout").setAttribute("aria-pressed", bo ? "true" : "false");
}

function buildSwatches() {
  const box = $("#swatches");
  SWATCHES.forEach((hex) => {
    const b = document.createElement("button");
    b.className = "swatch";
    b.style.background = hex;
    b.title = hex;
    b.dataset.hex = hex;
    b.onclick = () => {
      PICK.shown = hex;
      adoptHex(hex);
      doAction("set_colour", { hex: hex });
    };
    box.appendChild(b);
  });
}

function markSwatch(hex) {
  const cur = String(hex || "").toLowerCase();
  document.querySelectorAll("#swatches .swatch").forEach((b) =>
    b.classList.toggle("on", b.dataset.hex === cur));
}

/* ------------------------------------------------------------ colour picker

   There was a hex box and eleven swatches, which is not the same thing as
   being able to CHOOSE a colour.  Typing `#ff8800` is a lookup, not a
   decision: it requires already knowing the answer, which is the part that
   is hard.  So: a hue ring with a saturation/value square inside it, which
   is the only 2D layout that shows all three dimensions at once - a plain
   wheel has no brightness axis, so it cannot pick a dark colour at all.

   The maths is four small PURE functions above the drawing, extracted and
   run under node by the self-test rather than eyeballed, because a transposed
   red and green produces a plausible colour that is subtly wrong and nobody
   notices until the rig is lit.                                                      */

function hsvToRgb(h, s, v) {
  h = ((Number(h) % 360) + 360) % 360;
  s = Math.max(0, Math.min(1, Number(s)));
  v = Math.max(0, Math.min(1, Number(v)));
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let r = 0, g = 0, b = 0;
  if (h < 60)       { r = c; g = x; b = 0; }
  else if (h < 120) { r = x; g = c; b = 0; }
  else if (h < 180) { r = 0; g = c; b = x; }
  else if (h < 240) { r = 0; g = x; b = c; }
  else if (h < 300) { r = x; g = 0; b = c; }
  else              { r = c; g = 0; b = x; }
  return [Math.round((r + m) * 255), Math.round((g + m) * 255),
          Math.round((b + m) * 255)];
}

function rgbToHsv(r, g, b) {
  r = Math.max(0, Math.min(255, Number(r) || 0)) / 255;
  g = Math.max(0, Math.min(255, Number(g) || 0)) / 255;
  b = Math.max(0, Math.min(255, Number(b) || 0)) / 255;
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  const d = mx - mn;
  let h = 0;
  // The tolerance matters: for a grey d is 0 and the three branches are all
  // 0/0.  `d > 0` would be true for float dust on some conversions, and hue
  // would then jump to an arbitrary angle for a colour that has no hue.
  if (d > 1e-9) {
    if (mx === r)      h = 60 * (((g - b) / d) % 6);
    else if (mx === g) h = 60 * (((b - r) / d) + 2);
    else               h = 60 * (((r - g) / d) + 4);
    h = ((h % 360) + 360) % 360;
  }
  return [h, mx <= 0 ? 0 : d / mx, mx];
}

function hexToRgb(hex) {
  let s = String(hex || "").trim().replace(/^#/, "");
  // Three digits are expanded, because the ENGINE's `_parse_hex` expands
  // them.  A picker that rejects `#f80` rejects a colour the console would
  // have accepted, and "I typed a valid colour and it said no" is the kind
  // of small wrongness that makes an operator stop trusting a control.
  if (/^[0-9a-fA-F]{3}$/.test(s)) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return null;
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16),
          parseInt(s.slice(4, 6), 16)];
}

function rgbToHex(rgb) {
  return "#" + rgb
    .map((n) => clampInt(n, 0, 255, 0).toString(16).padStart(2, "0")).join("");
}

const PICK = { h: 28, s: 1, v: 1, down: false, sent: null, painted: null,
               shown: null };
// One throttled sender, like the pad's.  A drag across the picker is ~120
// pointermove events; at 45 ms that is a handful of requests instead of a
// hundred, and `flush()` on release guarantees the value the operator let go
// at is the one that lands - never the one that happened to be newest when
// the timer fired.
const pickSend = liveSender((v) => doAction("set_colour", v, { quiet: true }));
const PICK_SIZE = 196;
// The ring's inner edge as a fraction of its outer.  A thin ring around a
// LARGE square is worth the fraction it costs: on this panel the square is
// the part you actually aim at, and a fat ring turns the middle of the
// control into decoration.
const PICK_RING_IN = 0.78;

function pickBox() {
  const cx = PICK_SIZE / 2, cy = PICK_SIZE / 2;
  const R = PICK_SIZE / 2 - 2;              // 2px so the ring is not clipped
  const RI = R * PICK_RING_IN;
  // The square is inscribed in the ring's inner circle, INSET by 4px.  A
  // square flush to the circle puts its corners exactly on the boundary, so
  // the two hit regions overlap and pure hue / pure white become unreachable
  // at the corners depending on which test runs first.  The 4px gap is
  // owned by the square (clamped) and the ring starts at RI, so the regions
  // are disjoint and every pixel of the control does exactly one thing.
  return { cx, cy, R, RI, A: (RI - 4) / Math.SQRT2, GAP: 4 };
}

/* What the ENGINE would do with this colour, counted before the operator
   finds out by getting an error.

   `engine._colour_values` picks the first family the head has, in the order
   RGB, CMY, white, and returns NOTHING for a head with none of them - which
   is a wheel fixture, and there are two of those in the rig.  Picking a
   colour with one of those selected raises "selected heads have no colour
   channels", which is correct and useless: the operator did nothing wrong and
   the console still cannot tell them which heads.  So the count is computed
   here, from the same precedence, and shown WHILE they pick.

   Note it is "any of red/green/blue", not all three - that is what the engine
   tests, so a red-only head counts as reachable, because it is. */
function colourReach() {
  const sel = new Set(((S && S.selected) || []).map(Number));
  const out = { total: sel.size, rgb: [], cmy: [], white: [], none: [] };
  if (!sel.size) return out;
  ((S && S.patch) || []).forEach((h) => {
    if (!sel.has(h.head_no)) return;
    const roles = new Set(h.map || []);
    if (roles.has("red") || roles.has("green") || roles.has("blue")) out.rgb.push(h.head_no);
    else if (roles.has("cyan") || roles.has("magenta") || roles.has("yellow")) out.cmy.push(h.head_no);
    else if (roles.has("white")) out.white.push(h.head_no);
    else out.none.push(h.head_no);
  });
  return out;
}

function paintPicker() {
  const cv = $("#pick");
  if (!cv) return;
  const ctx = cv.getContext && cv.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const want = Math.round(PICK_SIZE * dpr);
  if (cv.width !== want || cv.height !== want) {
    cv.width = want; cv.height = want;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, PICK_SIZE, PICK_SIZE);
  const b = pickBox();

  // The ring, as overlapping arcs.  Not a conic gradient: that is one call
  // instead of 120, and it is not in every browser this has to run in.
  const SEG = 120;
  ctx.lineWidth = b.R - b.RI + 1;            // +1 so the segments overlap
  for (let i = 0; i < SEG; i++) {
    const a0 = (i / SEG) * Math.PI * 2 - Math.PI / 2;
    const a1 = ((i + 2) / SEG) * Math.PI * 2 - Math.PI / 2;
    ctx.beginPath();
    ctx.arc(b.cx, b.cy, (b.R + b.RI) / 2, a0, a1);
    ctx.strokeStyle = "hsl(" + (i * 360 / SEG) + ",100%,50%)";
    ctx.stroke();
  }
  // Two dark hairlines so the ring has an edge.  Without them it bleeds into
  // the panel and the control reads as a smear rather than a dial.
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(0,0,0,.55)";
  [b.R, b.RI].forEach((r) => {
    ctx.beginPath(); ctx.arc(b.cx, b.cy, r, 0, Math.PI * 2); ctx.stroke();
  });

  // The saturation/value square: flat hue, then white across, then black
  // down.  Two gradients over a solid fill is the standard construction and
  // it is the only way to get both axes without a per-pixel loop.
  ctx.fillStyle = "hsl(" + PICK.h + ",100%,50%)";
  ctx.fillRect(b.cx - b.A, b.cy - b.A, b.A * 2, b.A * 2);
  const gx = ctx.createLinearGradient(b.cx - b.A, 0, b.cx + b.A, 0);
  gx.addColorStop(0, "rgba(255,255,255,1)");
  gx.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = gx;
  ctx.fillRect(b.cx - b.A, b.cy - b.A, b.A * 2, b.A * 2);
  const gy = ctx.createLinearGradient(0, b.cy - b.A, 0, b.cy + b.A);
  gy.addColorStop(0, "rgba(0,0,0,0)");
  gy.addColorStop(1, "rgba(0,0,0,1)");
  ctx.fillStyle = gy;
  ctx.fillRect(b.cx - b.A, b.cy - b.A, b.A * 2, b.A * 2);

  // Markers.  Black under white on the SV dot, because the dot has to be
  // visible on pure black and on pure white, which are both reachable.
  const ha = PICK.h * Math.PI / 180 - Math.PI / 2;
  const hr = (b.R + b.RI) / 2;
  ctx.beginPath();
  ctx.arc(b.cx + Math.cos(ha) * hr, b.cy + Math.sin(ha) * hr,
          (b.R - b.RI) / 2 - 1.5, 0, Math.PI * 2);
  ctx.fillStyle = "hsl(" + PICK.h + ",100%,50%)";
  ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
  ctx.lineWidth = 1; ctx.strokeStyle = "#000"; ctx.stroke();

  const sx = b.cx - b.A + PICK.s * b.A * 2;
  const sy = b.cy - b.A + (1 - PICK.v) * b.A * 2;
  ctx.beginPath(); ctx.arc(sx, sy, 6, 0, Math.PI * 2);
  ctx.lineWidth = 3; ctx.strokeStyle = "#000"; ctx.stroke();
  ctx.lineWidth = 1.5; ctx.strokeStyle = "#fff"; ctx.stroke();

  const key = [PICK.h, PICK.s, PICK.v, cv.width].join(",");
  PICK.painted = key;
}

/* Only redraw when something actually changed.  `renderProgrammer` runs on
   every feed tick, and repainting a 200px canvas with 120 arcs twenty times
   a second for a picker nobody is touching is waste that shows up as
   jank on the one machine that cannot afford it. */
function repaintPicker() {
  const cv = $("#pick");
  if (!cv) return;
  const key = [PICK.h, PICK.s, PICK.v, cv.width].join(",");
  if (PICK.painted === key) return;
  paintPicker();
}

/* Take a hex from the engine, the hex box or a swatch into the picker.

   ONE RULE, written once, because there are three callers and they must
   agree: if the colour is desaturated, KEEP the operator's hue.

   A grey has no hue - `rgbToHsv` on `#808080` returns hue 0, s 0 - so a round
   trip through the engine throws the hue away.  The colour came from a
   palette, a cue or an undo, the feed reads it back at 20 Hz, and the dot
   jumps to the top of the wheel.  Then nudging hue does nothing visible,
   because the next tick puts it straight back.  A control that fights the
   operator is worse than one that is merely limited.

   And keeping the hue is FREE, not a fudge: at s = 0 the hue is multiplied
   out and has literally no effect on the output, so the two colours are the
   same colour.  Preserving it costs nothing and losing it is pure loss. */
function adoptHex(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return false;
  const hsv = rgbToHsv(rgb[0], rgb[1], rgb[2]);
  PICK.s = hsv[1];
  PICK.v = hsv[2];
  // Below one 8-bit step of saturation, hue is unrecoverable - so do not
  // pretend it is known and overwrite a hue the operator chose.
  if (hsv[1] > 0.004) PICK.h = hsv[0];
  repaintPicker();
  return true;
}

function setPick(h, s, v, quiet) {
  // A non-finite component here would flow through hsvToRgb to rgbToHex,
  // where `clampInt(NaN)` returns its default of 0 - so a NaN does not look
  // like a NaN on the wire, it looks like a CONCRETE WRONG COLOUR.  That is
  // how a mis-wired axis turned a white head black instead of throwing.
  // Refuse the whole pick rather than send one.
  const n = [Number(h), Number(s), Number(v)];
  if (!n.every((x) => Number.isFinite(x))) {
    if (window.console && console.warn) {
      console.warn("colour picker: refusing a non-finite pick", n);
    }
    return null;
  }
  PICK.h = ((n[0] % 360) + 360) % 360;
  PICK.s = Math.max(0, Math.min(1, n[1]));
  PICK.v = Math.max(0, Math.min(1, n[2]));
  const hex = rgbToHex(hsvToRgb(PICK.h, PICK.s, PICK.v));
  const box = $("#hex-in");
  // The hex box is the source of truth when it has the focus: overwriting a
  // half-typed `#ff8` mid-keystroke is the single most annoying thing a
  // colour control can do.
  if (box && document.activeElement !== box) box.value = hex;
  markSwatch(hex);
  repaintPicker();
  renderPickInfo();
  // A pick that lands on the colour already shown is not a change, and must
  // not cost an undo step.  Holding shift-down at zero saturation, or
  // pressing an arrow into a clamp, lands exactly where it started - and
  // charging a Ctrl+Z for it is how people stop trusting undo.
  const changed = hex !== PICK.shown;
  // Remember what the picker is showing, so the engine feed can tell the
  // difference between "the show moved" and "this is what I just set".
  PICK.shown = hex;
  if (!quiet && changed) pickSend.push({ hex });
  return hex;
}

function renderPickInfo() {
  const box = $("#pick-info");
  if (!box) return;
  const r = colourReach();
  const reached = r.rgb.length + r.cmy.length + r.white.length;
  const one = r.none.length === 1;
  let line, warn = false;
  if (!r.total) {
    line = "select heads to pick a colour";
  } else if (!reached) {
    line = "no colour channels on this selection — "
         + (one ? "head " : "heads ") + r.none.join(", ")
         + (one ? " has a colour wheel, not RGB"
                : " have a colour wheel, not RGB");
    warn = true;
  } else if (reached < r.total) {
    line = "reaches " + reached + " of " + r.total + " heads · "
         + (one ? "head " : "heads ") + r.none.join(", ")
         + (one ? " has no colour channels" : " have no colour channels");
    warn = true;
  } else if (r.white.length) {
    line = "reaches " + r.total + " heads · " + r.white.length
         + " white-only, so they take the luma, not the colour";
  } else {
    line = "reaches " + r.total + " head" + (r.total === 1 ? "" : "s")
         + (r.cmy.length ? " (CMY)" : "");
  }
  if (box.textContent !== line) box.textContent = line;
  box.classList.toggle("warn", warn);
  // Dim the control when it would do nothing, so the state is visible
  // before the click rather than in the error afterwards.
  const wrap = $("#pick-wrap");
  if (wrap) wrap.classList.toggle("inert", !r.total || !reached);
}

/* WHICH axis a key moves, as a PURE function of (key, shift, current).

   This was inline in the keydown handler, where it was wrong: `setPick`
   takes (h, s, v) and the up/down branches passed the VALUE into the `s`
   slot, leaving `v` undefined.  So ArrowDown moved SATURATION and set
   brightness to NaN - and NaN through `hsvToRgb` reached `rgbToHex`,
   `clampInt` turned it into 0, and the engine was sent `#000000`.  Pressing
   the down arrow on a white head turned it BLACK.

   Fifty-one green checks missed it, because they grepped for `case
   "ArrowDown"` and found it.  Grepping for a switch proves the key is
   handled; it cannot prove the key moves the axis you meant.  So the
   decision is pure, takes the current state as an argument, and the suite
   runs all twelve combinations and checks that the two axes you did NOT
   press are returned BIT IDENTICAL. */
function pickNudge(key, shift, cur) {
  const hueStep = shift ? 10 : 2;        // degrees
  const svStep = shift ? 0.1 : 0.02;     // saturation or value
  const { h, s, v } = cur;
  switch (key) {
    case "ArrowLeft":  return { h: h - hueStep, s, v };
    case "ArrowRight": return { h: h + hueStep, s, v };
    case "ArrowUp":
      return shift ? { h, s: s + svStep, v } : { h, s, v: v + svStep };
    case "ArrowDown":
      return shift ? { h, s: s - svStep, v } : { h, s, v: v - svStep };
    case "Home":       return { h: 0, s: 0, v: 1 };         // white
    default: return null;
  }
}

function pickFromPoint(px, py) {
  const b = pickBox();
  const dx = px - b.cx, dy = py - b.cy;
  const r = Math.sqrt(dx * dx + dy * dy);
  if (r >= b.RI && r <= b.R + 3) {
    // In the ring: hue only.  Zero degrees at the top, going clockwise, which
    // is what every colour picker does and what the eye expects - a dial that
    // starts at 3 o'clock reads as broken.
    const deg = Math.atan2(dx, -dy) * 180 / Math.PI;
    setPick((deg + 360) % 360, PICK.s, PICK.v);
    return;
  }
  const s = (px - (b.cx - b.A)) / (b.A * 2);
  const v = 1 - (py - (b.cy - b.A)) / (b.A * 2);
  setPick(PICK.h, s, v);
}

function wirePicker() {
  const cv = $("#pick");
  if (!cv) return;
  // Cached like the pad's: a getBoundingClientRect() per pointermove forces
  // a synchronous layout of the page for every event of the drag.
  let box = null;
  const remeasure = () => { box = cv.getBoundingClientRect(); };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(remeasure).observe(cv);
  else window.addEventListener("resize", remeasure);
  remeasure();

  const point = (e) => {
    if (!box || !box.width) remeasure();
    pickFromPoint(e.clientX - box.left, e.clientY - box.top);
  };

  cv.addEventListener("pointerdown", (e) => {
    PICK.down = true;
    remeasure();
    try { cv.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ }
    point(e);
    e.preventDefault();
  });
  cv.addEventListener("pointermove", (e) => { if (PICK.down) point(e); });
  // Throttled, exactly like the pan/tilt pad, and for the same reason: 120
  // pointermove events a second is 120 HTTP requests a second.  `set_colour`
  // is already in UNDO_COALESCE, so the whole drag is still ONE Ctrl+Z.
  const release = () => {
    if (!PICK.down) return;
    PICK.down = false;
    pickSend.flush();
  };
  cv.addEventListener("pointerup", release);
  cv.addEventListener("pointercancel", () => { PICK.down = false; pickSend.flush(); });

  // A canvas is not reachable by keyboard, which would make colour
  // unreachable too.  Arrows move the dot; Shift makes it coarse.  This is
  // the whole control without a mouse, so it is not optional garnish.
  cv.addEventListener("keydown", (e) => {
    const next = pickNudge(e.key, e.shiftKey, PICK);
    if (!next) return;
    e.preventDefault();
    e.stopPropagation();
    setPick(next.h, next.s, next.v);
  });

  // Paint once, HERE.  `renderProgrammer` only repaints when the hex field
  // holds a colour it can parse, and on a fresh load with a clear programmer
  // the field is EMPTY - so the picker stayed a blank 196px square with every
  // contract passing and nothing on screen.  Wiring a control is not the
  // same as drawing it.
  repaintPicker();
  renderPickInfo();
}

/* --------------------------------------------------------- pan/tilt pad */

const padPos = { pan: 128, tilt: 128 };

function paintPad() {
  $("#pad-cross").style.left = (padPos.pan / 255 * 100) + "%";
  $("#pad-cross").style.top = ((1 - padPos.tilt / 255) * 100) + "%";
  $("#pad-read").textContent = "P" + padPos.pan + " T" + padPos.tilt;
}

function movePad(pan, tilt) {
  padPos.pan = clampInt(pan, 0, 255, 128);
  padPos.tilt = clampInt(tilt, 0, 255, 128);
  paintPad();
}

function wirePad() {
  const pad = $("#ptpad");
  // The pad's box is cached rather than measured per pointermove: a
  // getBoundingClientRect() in the move handler forces a synchronous
  // layout of the whole page for every single event of a drag.
  let box = null;
  const remeasure = () => { box = pad.getBoundingClientRect(); };
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(remeasure).observe(pad);
  } else {
    window.addEventListener("resize", remeasure);
  }
  window.addEventListener("scroll", remeasure, true);
  remeasure();

  const point = (e) => {
    if (!box || !box.width || !box.height) remeasure();
    const x = Math.max(0, Math.min(1,
      (e.clientX - box.left) / Math.max(1, box.width)));
    const y = Math.max(0, Math.min(1,
      (e.clientY - box.top) / Math.max(1, box.height)));
    movePad(x * 255, (1 - y) * 255);
    // Send it as it is dragged, so the head turns under the finger.
    send.push({ pan: padPos.pan, tilt: padPos.tilt });
  };
  const send = liveSender((v) => doAction("set_position", v, { quiet: true }));

  pad.addEventListener("pointerdown", (e) => {
    padDown = true;
    remeasure();
    try { pad.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ }
    point(e);
    e.preventDefault();
  });
  pad.addEventListener("pointermove", (e) => { if (padDown) point(e); });
  const release = () => {
    if (!padDown) return;
    padDown = false;
    send.flush();
  };
  pad.addEventListener("pointerup", release);
  pad.addEventListener("pointercancel", () => { padDown = false; send.flush(); });
}

/* ---------------------------------------------------------- playbacks */

function renderPlaybacks() {
  if (!S) return;
  const pbs = S.playbacks || [];
  const sig = pbs.map((p) =>
    p.n + ":" + (p.name || "") + ":" + (p.stack || []).length).join("|");
  $("#pb-count").textContent = pbs.length + " faders";
  if (sig === pbStructSig) { updateAllCards(); return; }
  pbStructSig = sig;
  pbCards = {};
  const list = $("#pb-list");
  list.replaceChildren();
  pbs.forEach((p) => {
    const card = buildPlayback(p);
    pbCards[p.n] = card;
    list.appendChild(card.root);
  });
  updateAllCards();
}

function smallBtn(text, cls, title) {
  const b = document.createElement("button");
  b.className = "cbtn " + (cls || "");
  b.textContent = text;
  b.title = title || "";
  return b;
}

function buildPlayback(p) {
  const root = document.createElement("div");
  root.className = "pbcard";

  const head = document.createElement("div");
  head.className = "pbhead";
  const num = document.createElement("span");
  num.className = "n";
  num.textContent = "PB" + p.n;
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = p.name || "(unnamed)";
  const st = document.createElement("span");
  st.className = "pbstate";
  head.append(num, name, st);

  const lvl = document.createElement("input");
  lvl.type = "range";
  lvl.min = "0"; lvl.max = "100"; lvl.step = "1";
  lvl.value = String(typeof p.level === "number" ? p.level : 100);
  lvl.title = "playback level (HTP)";
  trackDragging(lvl);
  // A playback fader is usually used to bring a look up and down under the
  // operator's hand, so it tracks the drag rather than jumping at the end
  // of it.
  const sendLvl = liveSender(() =>
    doAction("playback_level",
             { playback: p.n, level: Number(lvl.value) }, { quiet: true }));
  lvl.addEventListener("input", () => sendLvl.push());
  lvl.addEventListener("change", () => sendLvl.flush());

  const btns = document.createElement("div");
  btns.className = "pbbtns";
  const go = smallBtn("GO", "go", "run the current cue");
  const back = smallBtn("⏮", "", "previous cue");
  const fwd = smallBtn("⏭", "", "next cue");
  const rel = smallBtn("release", "", "release the playback");
  go.onclick = () => playbackGo(p.n);
  back.onclick = () => doAction("cue_back", { playback: p.n });
  fwd.onclick = () => doAction("cue_forward", { playback: p.n });
  rel.onclick = () => doAction("playback_release", { playback: p.n });
  btns.append(go, back, fwd, rel);

  // auto-follow: arm the timed cue advance, set the delay in seconds.
  const frow = document.createElement("div");
  frow.className = "followrow";
  const fbtn = smallBtn("follow", "mini follow",
                        "auto-advance to the next cue (auto-follow)");
  const fdelay = document.createElement("input");
  fdelay.type = "number";
  fdelay.min = "0";
  fdelay.step = "0.5";
  const f0 = p.follow || {};
  fdelay.value = String(typeof f0.delay === "number" ? f0.delay : 2);
  fdelay.className = "fdelay";
  fdelay.title = "seconds between cues (0 = each cue's hold time)";
  const fstat = document.createElement("span");
  fstat.className = "fstat";
  frow.append(fbtn, fdelay, fstat);

  const cueNow = document.createElement("div");
  cueNow.className = "cue-now";

  const stack = document.createElement("div");
  stack.className = "stack";
  stack.setAttribute("role", "listbox");
  stack.setAttribute("aria-label", "cue stack");
  (p.stack || []).forEach((c, cueIndex) => {
    // A cue row was a plain div with no handler, so a specific cue could
    // only be reached by pressing GO until you got there.  A real desk
    // lets you take any cue directly.
    //
    // The row is wrapped, because a cue also has to be EDITABLE - and you
    // could not insert one, delete one, move one or rename one at all, so
    // a list could only ever be built in the order it was recorded.
    const wrap = document.createElement("div");
    wrap.className = "cuewrap";
    const row = document.createElement("button");
    row.type = "button";
    row.className = "cuerow";
    row.setAttribute("role", "option");
    row.title = "GO to cue " + c.n + " " + (c.name || "");
    const cn = document.createElement("span");
    cn.className = "num";
    cn.textContent = c.n;
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = c.name || "cue";
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = "fade " + (c.fade_s !== undefined ? c.fade_s : 0) +
      "s · hold " + (c.hold_s !== undefined ? c.hold_s : 0) + "s";
    row.append(cn, nm, t);
    row.onclick = async () => {
      if (!card.active) {
        await doAction("playback_activate", { playback: p.n });
      }
      // by cue NUMBER, which is what the row shows - not a stack index
      await doAction("cue_go", { playback: p.n, cue: c.n });
    };

    const tools = document.createElement("button");
    tools.type = "button";
    tools.className = "ibtn cueedit-btn";
    tools.textContent = "⋯";
    tools.title = "edit cue " + c.n + " — rename, timing, move, delete";
    tools.setAttribute("aria-label", "edit cue " + c.n);

    const panel = document.createElement("div");
    panel.className = "cueedit hidden";
    const nameIn = document.createElement("input");
    nameIn.type = "text";
    nameIn.value = c.name || "";
    nameIn.placeholder = "cue name";
    const fadeIn = document.createElement("input");
    fadeIn.type = "number"; fadeIn.step = "0.1"; fadeIn.min = "0";
    fadeIn.value = c.fade_s !== undefined ? c.fade_s : 0;
    fadeIn.title = "fade seconds";
    const holdIn = document.createElement("input");
    holdIn.type = "number"; holdIn.step = "0.1"; holdIn.min = "0";
    holdIn.value = c.hold_s !== undefined ? c.hold_s : 0;
    holdIn.title = "hold seconds";
    panel.append(nameIn, fadeIn, holdIn);

    const info = document.createElement("div");
    info.className = "cueinfo";

    const act = (label, title, fn) => {
      const b = document.createElement("button");
      b.className = "cbtn tiny";
      b.textContent = label;
      b.title = title;
      b.onclick = fn;
      panel.appendChild(b);
      return b;
    };
    const P = p.n, N = c.n;
    const last = cueIndex === (p.stack || []).length - 1;
    act("apply", "save the name, fade and hold",
      () => doAction("edit_cue", {
        playback: P, cue: N, name: nameIn.value,
        fade: Number(fadeIn.value), hold: Number(holdIn.value),
      }));
    act("what's in it", "what this cue actually lights",
      async () => {
        const r = await doAction("cue_info", { playback: P, cue: N });
        info.textContent = (r && r.summary) ? r.summary : "no detail";
      });
    act("insert below", "open an empty cue after this one",
      () => doAction("insert_cue", { playback: P, at: N + 1 }));
    if (N > 1) {
      act("↑", "move this cue up",
        () => doAction("move_cue", { playback: P, cue: N, to: N - 1 }));
    }
    if (!last) {
      act("↓", "move this cue down",
        () => doAction("move_cue", { playback: P, cue: N, to: N + 1 }));
    }
    act("delete", "remove this cue (undo restores it)",
      () => doAction("delete_cue", { playback: P, cue: N }));

    tools.onclick = (e) => {
      e.stopPropagation();
      const open = panel.classList.contains("hidden");
      panel.classList.toggle("hidden", !open);
      tools.classList.toggle("on", open);
      if (open && !info.textContent) {
        doAction("cue_info", { playback: P, cue: N }).then((r) => {
          if (r && r.summary) info.textContent = r.summary;
        });
      }
      if (open) nameIn.focus();
    };

    wrap.append(row, tools, panel, info);
    stack.appendChild(wrap);
  });

  const rec = document.createElement("button");
  rec.className = "cbtn mini rec-cue";
  rec.textContent = "+ Record cue";
  rec.title = "record the programmer as the next cue";
  rec.onclick = () => openCueForm(root, p.n);

  root.append(head, lvl, btns, frow, cueNow, stack, rec);

  const card = {
    root: root, lvl: lvl, st: st, cueNow: cueNow, stack: stack,
    head: head,
    n: p.n, cues: (p.stack || []),
    index: typeof p.index === "number" ? p.index : 0,
    active: !!p.active, cue: null,
    follow: p.follow || null,
    fbtn: fbtn, fdelay: fdelay, fstat: fstat,
  };
  // Ten full playback cards meant two to four screens of scrolling in a
  // column barely a screen tall, and the running playback could be
  // anywhere in it.  An inactive card now collapses to its header - the
  // number, the name and the state are all still there - and the running
  // one expands and scrolls itself into view.
  head.classList.add("pbhead-click");
  head.onclick = (e) => {
    if (e.target.closest("button, input, select")) return;
    doAction("playback_activate", { playback: p.n });
  };
  root.classList.toggle("collapsed", !card.active);
  head.setAttribute("title", "activate this playback (click)");
  fbtn.onclick = async () => {
    const on = !(card.follow && card.follow.on);
    const r = await doAction("follow_set", { playback: p.n, on: on });
    if (r && r.follow) { card.follow = r.follow; updateFollowRow(card); }
  };
  fdelay.addEventListener("change", async () => {
    const r = await doAction("follow_set",
      { playback: p.n, delay: Math.max(0, Number(fdelay.value) || 0) });
    if (r && r.follow) { card.follow = r.follow; updateFollowRow(card); }
  });
  updateFollowRow(card);
  highlightCue(card);
  return card;
}

async function playbackGo(n) {
  const card = pbCards[n];
  if (card && !card.active) await doAction("playback_activate", { playback: n });
  await doAction("cue_go", { playback: n });
}

function highlightCue(card) {
  Array.from(card.stack.children).forEach((row, i) => {
    const on = i === card.index;
    row.classList.toggle("on", on);
    row.setAttribute("aria-selected", on ? "true" : "false");
  });
  card.stack.setAttribute("aria-activedescendant",
                          card.cues.length ? String(card.index) : "");
}

function updateFollowRow(card) {
  const f = card.follow;
  const on = !!(f && f.on);
  card.fbtn.classList.toggle("on", on);
  card.fbtn.textContent = on ? (f.paused ? "follow ⏸" : "follow ●") : "follow";
  if (on && typeof f.in === "number") {
    card.fstat.textContent = "next cue in " + f.in.toFixed(1) + "s";
  } else if (on) {
    card.fstat.textContent = f.paused ? "paused" : "armed";
  } else {
    card.fstat.textContent = "";
  }
  if (document.activeElement !== card.fdelay && f
      && typeof f.delay === "number") {
    card.fdelay.value = String(f.delay);
  }
}

function updateCueLine(card) {
  const c = card.cues[card.index];
  const now = card.cue;
  const nxt = card.cues[card.index + 1];
  if (c) {
    card.cueNow.textContent = "cue " + c.n + " " + (c.name || "") +
      " · fade " + (c.fade_s !== undefined ? c.fade_s : 0) + "s"
      + (nxt ? " · next " + nxt.n + " " + (nxt.name || "") : " · last cue");
  } else if (now) {
    // A playback that has never run reads "not started", not a bare dash.
    // An empty cue line is indistinguishable from a broken one, and the
    // operator cannot tell whether the stack is loaded.
    card.cueNow.textContent = "not started · press GO";
  } else {
    card.cueNow.textContent = "no cues recorded";
  }
  updateNowPlaying();
}

/* The single most important readout on a lighting desk: what is running
 * right now, and what GO will do next.  It was a small amber line buried
 * inside a scrolling card, which is not where an operator looks mid-show. */
function updateNowPlaying() {
  const el = $("#now-playing");
  if (!el) return;
  const card = Object.keys(pbCards).map((k) => pbCards[k])
    .find((c) => c.active);
  if (!card) {
    if (el.textContent !== "no playback active") {
      el.textContent = "no playback active";
      el.className = "nowplaying empty";
    }
    return;
  }
  const c = card.cues[card.index];
  const nxt = card.cues[card.index + 1];
  const parts = ["PB" + card.n];
  if (card.cue || c) parts.push((card.cue ? card.cue.n : c.n) + " "
                                + ((card.cue && card.cue.name)
                                   || (c && c.name) || ""));
  else parts.push("not started");
  if (nxt) parts.push("→ next " + nxt.n + " " + (nxt.name || ""));
  const line = parts.join("  ·  ");
  if (el.textContent !== line) el.textContent = line;
  el.className = "nowplaying" + (card.cue || c ? "" : " empty");
}

function updateCardState(card) {
  const wasActive = card.root.classList.contains("active");
  card.root.classList.toggle("active", !!card.active);
  card.root.classList.toggle("collapsed", !card.active);
  // When a playback becomes the running one it must come to the operator,
  // not leave them to scroll hunting for it - the column is a screen tall
  // and holds ten of them.
  if (card.active && !wasActive && card.root.scrollIntoView) {
    try { card.root.scrollIntoView({ block: "nearest" }); } catch (e) { /* older */ }
  }
  const text = (card.active ? "active" : "off") +
    " · " + clampInt(card.lvl.value, 0, 100, 0) + "%" +
    (card.cues.length
      ? (card.index < 0 ? " · not started"
                        : " · " + (card.index + 1) + "/" + card.cues.length)
      : "");
  if (card.st.textContent !== text) card.st.textContent = text;
}

function updateAllCards() {
  Object.keys(pbCards).forEach((k) => {
    const c = pbCards[k];
    highlightCue(c);
    updateCueLine(c);
    updateCardState(c);
    updateFollowRow(c);
  });
  updateEmptyPlayback();
}

function updateEmptyPlayback() {
  const any = Object.keys(pbCards).some((k) => pbCards[k].active);
  $("#pb-empty").classList.toggle("hidden", any);
}

function applyLitePlaybacks(list) {
  let needReload = false;
  list.forEach((lp) => {
    const card = pbCards[lp.n];
    if (!card) { needReload = true; return; }
    if (typeof lp.level === "number") setRange(card.lvl, lp.level);
    if (typeof lp.active === "boolean") card.active = lp.active;
    if (typeof lp.index === "number" && lp.index !== card.index) {
      card.index = lp.index;
      highlightCue(card);
    }
    if (lp.cue) card.cue = lp.cue;
    if (lp.follow) card.follow = lp.follow;
    updateCueLine(card);
    updateCardState(card);
    updateFollowRow(card);
  });
  updateEmptyPlayback();
  if (needReload) scheduleReload();
}

function openCueForm(cardRoot, pb) {
  const btn = cardRoot.querySelector(".rec-cue");
  if (!btn || btn.classList.contains("hidden")) return;
  btn.classList.add("hidden");

  const form = document.createElement("div");
  form.className = "inline-form";
  const name = document.createElement("input");
  name.type = "text";
  name.maxLength = 32;
  name.placeholder = "cue name";
  const fade = document.createElement("input");
  fade.type = "number";
  fade.min = "0";
  fade.step = "0.5";
  fade.value = "2";
  fade.placeholder = "fade s";
  fade.className = "fades";
  fade.title = "fade time in seconds";
  const ok = document.createElement("button");
  ok.className = "ibtn ok"; ok.textContent = "✓"; ok.title = "record the cue";
  const cancel = document.createElement("button");
  cancel.className = "ibtn"; cancel.textContent = "✕"; cancel.title = "cancel";
  form.append(name, fade, ok, cancel);
  cardRoot.appendChild(form);
  name.focus();

  const close = () => {
    form.remove();
    btn.classList.remove("hidden");
  };
  cancel.onclick = close;
  ok.onclick = async () => {
    const f = Number(fade.value);
    if (!Number.isFinite(f) || f < 0) {
      showErr("record cue: fade must be 0 or more seconds");
      fade.focus();
      return;
    }
    const params = { playback: pb, fade: f };
    const nm = name.value.trim();
    if (nm) params.name = nm;
    close();
    await doAction("record_cue", params);
  };
  [name, fade].forEach((el) => el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") ok.onclick();
    if (e.key === "Escape") close();
  }));
}

/* ------------------------------------------------------- shows + status */

function renderShows() {
  if (!S) return;
  const shows = S.shows || [];
  const sig = shows.join("|");
  if (sig !== showsSig) {
    showsSig = sig;
    const sel = $("#load-show");
    const keep = sel.value;
    sel.replaceChildren();
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "📂 load show…";
    sel.appendChild(ph);
    shows.forEach((s) => {
      const o = document.createElement("option");
      o.value = s;
      o.textContent = s;
      sel.appendChild(o);
    });
    if (shows.indexOf(keep) >= 0) sel.value = keep;
  }
  const nameIn = $("#show-name");
  if (document.activeElement !== nameIn && S.show_file) {
    nameIn.placeholder = S.show_file;
  }
}

function renderStatus() {
  if (!S) return;
  const o = S.output || {};
  // The DRY RUN button, next to GO LIVE, because those are the two
  // questions in that order: may I transmit, and am I transmitting.  It
  // says which one is on rather than relying on colour, and the amber is
  // only there to be noticed across the room.
  const dry = $("#btn-dryrun");
  if (dry) {
    dry.dataset.on = S.dry_run ? "1" : "0";
    dry.textContent = S.dry_run ? "DRY RUN" : "OUTPUT OK";
    dry.title = (S.dry_run
      ? "DRY RUN: frames are built and counted, nothing leaves this "
        + "machine.\nClick to allow real output."
      : "Real output is allowed. Frames go out whenever the output is "
        + "running.\nClick to go back to dry run.")
      + (S.live ? "\n\nThe output is RUNNING, so switching to real output "
                 + "takes effect immediately." : "");
  }
  let health, cls;
  // A sender that throws on every 40 Hz tick still counted as "OK" here,
  // because nothing was looking at output.errors.  On a real rig that is
  // the difference between "the desk is working" and "the desk is failing
  // to send and the operator has been told nothing".
  const errs = Number(o.errors) || 0;
  const feedAge = Math.max(0, Math.round((Date.now() - lastGoodFeed) / 100) / 10);
  if (errs > lastSeenErrors) {
    lastErrorMsg = o.last_error || ("sender reported " + errs + " error(s)");
  }
  lastSeenErrors = errs;
  if (errs) {
    health = "SEND ERROR"; cls = "bad";
  } else if (!o.running) { health = S.dry_run ? "DRY RUN · STOPPED" : "STOPPED";
    cls = S.dry_run ? "warn" : "bad"; }
  else if (S.dry_run) { health = "DRY RUN · RUNNING"; cls = "warn"; }
  else if (typeof o.last_tick_age_ms === "number" && o.last_tick_age_ms > 500) {
    health = "STALLED"; cls = "bad";
  } else { health = "OK"; cls = "ok"; }

  const parts = [o.running && S.dry_run
    ? "simulated " + (o.simulated_frames || 0) + " frames"
    : "output: " + (o.frames_sent || 0) + " frames"];
  // Where the frames are actually going.  The operator set this once in
  // .env and then has no way to confirm it - and "am I sending to the right
  // IP" is the first question when a rig does not respond.
  if (o.host) parts.push("→ " + o.host);
  if (o.transport) parts.push(o.transport);
  if (o.running && typeof o.hz === "number") parts.push(o.hz.toFixed(1) + " Hz");
  if (o.running) parts.push("drift " + (o.drift_ms || 0) + " ms");
  parts.push(health);
  // A dead server used to be indistinguishable from a healthy idle one: the
  // footer just froze on its last value.  An age makes the difference.
  if (lastGoodFeed) parts.push("feed " + feedAge.toFixed(1) + "s ago");
  const el = $("#st-output");
  const line = parts.join(" · ");
  if (el.textContent !== line) el.textContent = line;
  el.className = (feedAge > 2 && lastGoodFeed) ? "bad" : cls;
  el.title = errs ? ("last error: " + lastErrorMsg) : "";
  if (errs) {
    // Errors are persistent, not a toast that vanishes: the operator has
    // to be able to come back to the desk and still see the failure.
    showErr("output error: " + lastErrorMsg, "feed", 0);
  }

  const h = (S.history || [])[0];
  const act = h
    ? "last: " + h.action + (h.ok === false ? " FAILED"
      : h.simulated ? " · dry run" : " · sent")
    : "last: —";
  if ($("#st-action").textContent !== act) $("#st-action").textContent = act;
  const sel = ((S.selected || []).length) + " selected";
  if ($("#st-sel").textContent !== sel) $("#st-sel").textContent = sel;
}

/* ------------------------------------------------------------------ fx */

// The effect rows are rebuilt only when the LIST changes, not on every
// 10 Hz tick.  They used to be torn down and recreated ten times a second:
// any keyboard focus on a stop button was gone within 100 ms and could
// never be reached, and 40 nodes per second of DOM churn is pure jank.
// The countdown text is updated in place, which is the part that really
// does change every tick.
function renderFx() {
  const box = $("#fx-list");
  if (!box) return;
  const rows = (S && S.fx) || [];
  const sig = rows.map((f) => [f.id, f.kind, f.role || f.attribute,
                               f.speed, f.spread, f.group,
                               (f.heads || []).join(".")].join(",")).join("|");
  if (sig === fxSig) { tickFxTimers(rows); return; }
  fxSig = sig;
  box.innerHTML = "";
  if (!rows.length) {
    box.className = "fx-list muted small";
    box.textContent = "no effects running";
    return;
  }
  box.className = "fx-list";
  for (const f of rows) {
    const row = document.createElement("div");
    row.className = "fx-row";
    row.dataset.fxId = String(f.id);

    const id = document.createElement("span");
    id.className = "num";
    id.textContent = "#" + f.id;

    const attr = document.createElement("span");
    attr.className = "fx-attr";
    attr.textContent = (f.kind || "sine") + " " + (f.role || f.attribute || "dimmer");

    const meta = document.createElement("span");
    meta.className = "fx-meta";
    meta.dataset.fxMeta = "1";

    const stop = document.createElement("button");
    stop.className = "ibtn";
    stop.title = "stop this effect";
    stop.setAttribute("aria-label", "stop effect " + f.id);
    stop.textContent = "■";
    stop.onclick = () => doAction("stop_fx", { id: f.id });

    row.append(id, attr, meta, stop);
    box.appendChild(row);
  }
  tickFxTimers(rows);
}

// The only part of an effect row that genuinely changes tick to tick.
function tickFxTimers(rows) {
  const box = $("#fx-list");
  if (!box) return;
  for (const f of rows) {
    const row = box.querySelector('[data-fx-id="' + String(f.id) + '"]');
    if (!row) continue;
    const meta = row.querySelector("[data-fx-meta]");
    if (!meta) continue;
    const bits = [Number(f.speed || 0).toFixed(1) + " Hz"];
    if (Number(f.spread)) bits.push(Math.round(f.spread) + "° spread");
    if (Number(f.remaining)) bits.push(Math.round(f.remaining) + "s left");
    if (Array.isArray(f.heads) && f.heads.length) bits.push(f.heads.length + " heads");
    if (f.group) bits.push("group " + f.group);
    const text = bits.join(" · ");
    if (meta.textContent !== text) {
      meta.textContent = text;
      meta.title = text;
    }
  }
}

/* ------------------------------------------------------------ autosave */

async function loadAutoStatus() {
  try {
    const d = await api("/api/status");
    const c = (d && d.console) || {};
    const el = $("#st-save");
    el.textContent = "autosave " + (c.autosave ? "on" : "off");
    el.classList.toggle("on", !!c.autosave);
    el.title = "engine state (patch, programmer, playbacks) is written to "
      + "data/autosave.json after every change"
      + (c.autosave ? "" : " — OFF (CONSOLE_AUTOSAVE=false)")
      + (c.autorestore === false ? " · restore disabled" : " · restored on start");
  } catch (err) {
    // footer keeps its placeholder — never block startup on a status read
  }
}

/* ----------------------------------------------------------- scan rig */

/* What the rig looks like, in words the operator can act on.
 *
 * The point of the sweep is that a negative result means something: it
 * says how many addresses were polled and which subnets, so "no node" is
 * a fact rather than a shrug.  A positive result names the node and its
 * IP, because the single most useful thing to know is what to put in
 * DMX_HOST.
 */
function describeScan(r) {
  const nodes = r.nodes || [];
  const unis = r.universes || [];
  if (!nodes.length && !unis.length) {
    return r.message || "nothing found";
  }
  const parts = [];
  if (nodes.length) {
    parts.push(nodes.length + " node"
      + (nodes.length > 1 ? "s" : "") + ": "
      + nodes.map((n) => (n.name || n.ip) + " @ " + n.ip).join(", "));
  }
  if (unis.length) {
    parts.push(unis.length + " universe"
      + (unis.length > 1 ? "s" : "") + ": "
      + unis.map((u) => "U" + u.universe
        + (u.channels ? " (" + u.channels + "ch used)" : " (idle)")
        + (u.node ? " on " + u.node : "")).join(", "));
  }
  if (r.polls_sent) parts.push(r.polls_sent + " polls");
  return parts.join(" · ");
}

/* ------------------------------------------------------ keyboard control
 *
 * A lighting desk is worked from the keyboard, and this one had almost
 * none: the only global keys were Escape.  Everything below is the set an
 * operator reaches for without looking, and every binding is also written
 * into the relevant control's tooltip so it can be discovered rather than
 * memorised.
 *
 * Guarded twice over: not while a text field has focus (typing a DMX
 * address must not fire the rig) and not while a dialog is open (except
 * the dialog's own Escape).
 */
const KEYMAP = [
  // playback - the controls used most, so they get the least effort
  { keys: [" "], label: "Space", act: "GO on the active playback",
    run: () => goActivePlayback() },
  { keys: ["Enter"], label: "Enter", act: "GO on the active playback",
    run: () => goActivePlayback() },
  { keys: ["b"], label: "B", act: "step BACK one cue",
    run: () => stepCue(-1) },
  { keys: ["c"], label: "C", act: "open the DMX channel sheet for the selection",
    run: () => openChannels() },
  { keys: ["]"], label: "]", act: "step forward one cue",
    run: () => stepCue(1) },
  { keys: ["["], label: "[", act: "step back one cue",
    run: () => stepCue(-1) },
  // programmer
  { keys: ["ArrowUp"], label: "↑", act: "intensity +1 (Shift +10)",
    run: (e) => nudgeIntensity(e.shiftKey ? 10 : 1) },
  { keys: ["ArrowDown"], label: "↓", act: "intensity −1 (Shift −10)",
    run: (e) => nudgeIntensity(e.shiftKey ? -10 : -1) },
  { keys: ["x"], label: "X", act: "toggle BLACKOUT",
    run: () => doAction("blackout", { state: S.blackout ? 0 : 1 }) },
  { keys: ["c"], label: "C", act: "clear the programmer",
    run: () => doAction("clear_programmer", {}) },
  { keys: ["l"], label: "L", act: "locate the selection",
    run: () => doAction("locate", {}) },
  { keys: ["m"], label: "M", act: "master 100% / 0%",
    run: () => doAction("master", { level: (S.master || 0) > 0 ? 0 : 100 }) },
  // selection
  { keys: ["1", "2", "3", "4", "5", "6", "7", "8", "9"], label: "1–9",
    act: "select head N (ctrl adds)",
    run: (e) => selectHeadByNumber(Number(e.key), e) },
  { keys: ["0"], label: "0", act: "select head 10",
    run: (e) => selectHeadByNumber(10, e) },
  { keys: ["a"], label: "A", act: "select all heads",
    run: () => doAction("select_all", {}) },
  { keys: ["Shift+a"], label: "Shift+A", act: "clear the selection",
    run: () => doAction("clear_selection", {}) },
  { keys: ["g"], label: "G", act: "group the selection",
    run: () => $("#btn-group") && $("#btn-group").click() },
  { keys: ["r"], label: "R", act: "record a cue on the active playback",
    run: () => recordCueQuick() },
  // view
  { keys: ["f"], label: "F", act: "frame the selected heads in 3D",
    run: () => frameViz() },
];

function activePlaybackNo() {
  const pbs = (S && S.playbacks) || [];
  const sel = activeCardNo;
  if (sel && pbs.some((p) => p.n === sel)) return sel;
  const active = pbs.find((p) => p.active);
  if (active) return active.n;
  return pbs.length ? pbs[0].n : 1;
}

async function goActivePlayback() {
  const n = activePlaybackNo();
  if (n) await playbackGo(n);
}

function stepCue(dir) {
  const n = activePlaybackNo();
  if (!n) return;
  doAction(dir > 0 ? "cue_forward" : "cue_back", { playback: n });
}

function nudgeIntensity(step) {
  const pv = programmerValues();
  const cur = typeof pv.values.dimmer === "number" ? pv.values.dimmer : 0;
  doAction("set_intensity", { level: clampInt(cur + step, 0, 100, 0) });
}

function selectHeadByNumber(n, e) {
  const patch = (S && S.patch) || [];
  if (!patch.some((x) => x.head_no === n)) {
    showErr("no head " + n + " in the patch", "info", "info");
    return;
  }
  // the same three rules as a click, so a keyboard build and a mouse build
  // do not diverge
  selectHead(n, !!(e && e.shiftKey), !!(e && (e.ctrlKey || e.metaKey)));
}

function recordCueQuick() {
  const n = activePlaybackNo();
  doAction("record_cue", { playback: n, name: "" });
}

function typingInAField(e) {
  const t = e.target;
  if (!t) return false;
  const tag = (t.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select"
         || t.isContentEditable;
}

// There is one modal (the add-heads library); the cue/group/palette prompts
// share a non-modal dialog that must not swallow the desk's keys.
//
// The dialog is #add-dialog.  #dlg-add is the confirm BUTTON inside it, and
// using that id here made this function always report "open" - which
// silently killed every keyboard shortcut on the desk.  A guard that is
// always true is worse than no guard: it looks like it is working.
function anyDialogOpen() {
  // Every modal, not just the add-heads one.  Each has to be in here for
  // two reasons: Escape must close it, and its own shortcut must not fire
  // from behind it and re-open on every press.
  //
  // The help overlay uses `hidden` (it is created hidden in the markup) and
  // the older two use a `.hidden` class, so BOTH are checked - a check for
  // one of them only would let `?` open help from behind help, which
  // looks like the key not working.
  return ["#add-dialog", "#ch-dialog", "#help-dialog"].some((sel) => {
    const d = $(sel);
    if (!d) return false;
    // A dialog is open when it is NEITHER hidden by class NOR by the
    // `hidden` attribute.  Both conventions are in use - the older two
    // carry a class, the help overlay carries the attribute - and the
    // guard has to be the AND of "not hidden by either", not the OR.
    // Writing `d.hidden === true || !classList.contains("hidden")` made
    // this return TRUE ALWAYS, because the help overlay is created with
    // `hidden`: every single-key shortcut in the console was dead and
    // nothing said why.
    return !(d.hidden === true || d.classList.contains("hidden"));
  });
}

// The 3D view has its own WASD/arrow bindings, so the desk's keys must not
// fight them.  The canvas only claims a key when it has focus.
function canvasHasFocus() {
  const a = document.activeElement;
  return !!(a && a.tagName === "CANVAS"
            && a.closest && a.closest("#viz-pane"));
}

function wireKeyboard() {
  document.addEventListener("keydown", (e) => {
    // Escape always closes whatever is open, even from a field.
    if (e.key === "Escape") {
      // Full screen first: it is the biggest thing on screen and the one an
      // operator reaches for Esc to leave.  The browser handles Esc for the
      // NATIVE full screen on its own, but the CSS overlay is ours, so
      // without this a full-screen view could only be left with the mouse.
      if (fsActive()) { applyFs(false); e.preventDefault(); return; }
      const help = $("#help-dialog");
      if (help && !help.hidden) { toggleHelp(false); return; }
      const ch = $("#ch-dialog");
      if (ch && !ch.classList.contains("hidden")) { closeChannels(); return; }
      if (anyDialogOpen()) { closeDialog(); return; }
      const panel = $("#ai-panel");
      if (panel && panel.classList.contains("open")) { setOpen(false); return; }
    }
    if (typingInAField(e)) return;
    // Undo/redo are handled BEFORE the "ctrl stands down" guard, because
    // that guard exists to stop browser shortcuts colliding with the
    // desk's single-key bindings - and Ctrl+Z is the one chord the
    // operator must never have to think about.  It is claimed even while
    // a modal is open: the modal is part of the show being edited, and
    // trapping undo there is how an operator loses work.
    if (e.ctrlKey || e.metaKey) {
      const k = (e.key || "").toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); doUndo(false); return; }
      if ((k === "z" && e.shiftKey) || k === "y") {
        e.preventDefault(); doUndo(true); return;
      }
      return;
    }
    if (canvasHasFocus()) return;          // the view owns the arrows
    if (anyDialogOpen()) return;
    const k = e.key;
    const lower = k.length === 1 ? k.toLowerCase() : k;
    const isShiftKey = (x) => x.indexOf("Shift+") === 0;
    // Shifted bindings are checked first and on their own, so Shift+A is
    // the clear-selection binding rather than select-all, and then the
    // plain bindings run for any shift state - which is what lets Shift and
    // ArrowUp reach the same entry and take a bigger step.
    if (e.shiftKey) {
      for (const b of KEYMAP) {
        if (!b.keys.includes("Shift+" + lower)) continue;
        e.preventDefault();
        b.run(e);
        return;
      }
    }
    for (const b of KEYMAP) {
      if (b.keys.some(isShiftKey)) continue;      // shift-only binding
      if (!b.keys.includes(k) && !b.keys.includes(lower)) continue;
      e.preventDefault();
      b.run(e);
      return;
    }
  });
}

// Write the bindings into the controls they belong to, so the map is
// discoverable from the UI instead of only from this file.
function annotateKeys() {
  const bind = {
    " ": "#pb-go", x: "#btn-blackout", f: "#cam-frame",
    a: "#btn-selectall", l: "#btn-locate", c: "#btn-clear",
    g: "#btn-group", m: "#master",
  };
  for (const b of KEYMAP) {
    const sel = bind[b.keys[0]];
    if (!sel) continue;
    const el = $(sel);
    if (!el) continue;
    el.title = (el.title ? el.title + "  ·  " : "")
      + b.label + " — " + b.act;
  }
  // and one place that lists all of them
  const help = $("#key-help");
  if (help) {
    help.innerHTML = KEYMAP.map((b) =>
      "<span><kbd>" + b.label + "</kbd>" + b.act + "</span>").join("");
  }
}

function wireScan() {
  const btn = $("#btn-scan");
  if (!btn) return;
  btn.onclick = async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = "scanning…";
    try {
      // sweep=true: if the broadcast pass finds nothing, poll every
      // address on the local /24 by unicast, because broadcast is
      // routinely dropped on Wi-Fi and nothing reports it.
      const d = await api("/api/console/scan",
                          { import: true, timeout: 1.5, sweep: true,
                            sweep_timeout: 1.5 });
      const r = d.result || {};
      if (r.scan_error) {
        showErr("scan: " + r.scan_error);
      } else {
        const seen = (r.universes || []).length;
        const imp = r.import;
        const found = describeScan(r);
        if (imp && imp.ok === false) {
          showErr("scan: " + found + " — " + (imp.error || "import refused"));
        } else if (imp && Array.isArray(imp.heads) && imp.heads.length) {
          showErr("scan: " + found + " · patched " + imp.heads.length
                  + " head(s) — set DMX_HOST to the node IP", "info");
        } else if (imp && Array.isArray(imp.skipped) && imp.skipped.length) {
          showErr("scan: " + found + " · already patched: "
                  + imp.skipped.join(", "), "info");
        } else {
          showErr("scan: " + found, "info");
        }
        // A node we found is the answer to "what do I put in DMX_HOST",
        // so say it outright rather than leaving it in a status line.
        if ((r.nodes || []).length) {
          const ip = r.nodes[0].ip;
          showErr("node found at " + ip + " — set DMX_HOST=" + ip
                  + " in .env (broadcast is often dropped on Wi-Fi)",
                  "info");
        }
        void seen;
      }
      await loadState();
      broadcast("patch");
    } catch (err) {
      showErr("scan: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "scan rig";
    }
  };
}

/* ------------------------------------------------------------ AI panel */

// Chips are the HIGH-FREQUENCY path: direct engine calls, no AI round trip.
const AI_CHIPS = [
  ["locate", "locate", {}],
  ["full", "set_intensity", { level: 100 }],
  ["clear", "clear_programmer", {}],
  ["blackout", "blackout", {}],
  ["pulse", "run_fx", { attribute: "dimmer", wave: "sine", speed: 1.5 }],
  ["stop fx", "stop_fx", {}],
];

function stepLabel(action, p) {
  p = p || {};
  if (action === "set_intensity") return "intensity " + p.level + "%";
  if (action === "blackout") return "blackout " + (p.state === 0 ? "off" : "on");
  if (action === "set_colour") return "colour " + (p.hex || p.colour || "");
  if (action === "run_fx") return "fx " + (p.attribute || "dimmer")
    + " " + (p.wave || p.kind || "sine");
  if (action === "add_heads") return p.qty + " × " + (p.query || "fixture");
  if (action === "master") return "master " + p.level + "%";
  if (action === "playback_level") return "PB" + p.playback + " " + p.level + "%";
  if (action === "record_cue") return "record cue → PB" + (p.playback || "?");
  if (action === "select_all") return "select all";
  if (action === "select_group") return "select group " + (p.group || "");
  if (action === "select_heads") return "select "
    + (p.head_end ? p.head + "–" + p.head_end : p.head);
  return action.replace(/_/g, " ");
}

function aiSay(kind, text, opts) {
  opts = opts || {};
  const log = $("#ai-log");
  const box = document.createElement("div");
  box.className = "ai-msg " + kind;
  box.textContent = text;

  if (opts.source) {
    const src = document.createElement("span");
    src.className = "ai-src";
    src.textContent = opts.source === "llm" ? "AI"
      : opts.source === "error" ? "ERR" : "OFFLINE";
    box.appendChild(src);
  }

  const runs = (opts.run && opts.run.steps_run) || [];
  const planned = opts.steps || [];
  if (runs.length || planned.length) {
    const pills = document.createElement("div");
    pills.className = "ai-steps";
    if (runs.length) {
      for (const s of runs) {
        const p = document.createElement("span");
        p.className = "step-pill " + (s.ok ? "ok" : "bad");
        p.textContent = stepLabel(s.action, s.params);
        p.title = s.summary || s.action;
        pills.appendChild(p);
      }
      const failed = opts.run && opts.run.steps_run
        .find((s) => !s.ok);
      if (failed) pills.title = "failed: " + (opts.run.error || "");
    } else {
      for (const s of planned) {
        const p = document.createElement("span");
        p.className = "step-pill";
        p.textContent = stepLabel(s.action,
          Object.assign({}, s.attributes, s.fx, s.timing));
        p.title = s.target + " → " + s.action;
        pills.appendChild(p);
      }
    }
    box.appendChild(pills);
  }

  log.appendChild(box);
  log.scrollTop = log.scrollHeight;
}

function wireAI() {
  const panel = $("#ai-panel");
  const btn = $("#btn-ai");
  const setOpen = (state) => {
    const on = state === undefined ? !panel.classList.contains("open") : state;
    panel.classList.toggle("open", on);
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) $("#ai-text").focus();
  };
  btn.onclick = () => setOpen();
  $("#ai-close").onclick = () => setOpen(false);
  // Escape is handled once, globally, in wireKeyboard - a second listener
  // here would close the panel and then fall through to the desk's keys.

  const chipBox = $("#ai-chips");
  for (const [label, action, params] of AI_CHIPS) {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = label;
    b.title = action + " (instant, no AI round trip)";
    b.onclick = () => doAction(action, params);
    chipBox.appendChild(b);
  }

  const send = async () => {
    const input = $("#ai-text");
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    aiSay("user", msg);
    try {
      const d = await api("/api/console/ai", {
        message: msg, apply: true, offline: !!$("#ai-offline").checked,
      });
      const r = d.result || {};
      const reply = (r.reply || "")
        + (r.note ? "\n(" + r.note + ")" : "");
      aiSay("bot", reply, { source: r.source, steps: r.steps, run: r.run });
      await loadState();          // programmer / fx / selection changed
    } catch (err) {
      aiSay("bot", "error: " + err.message, { source: "error" });
    }
  };
  $("#ai-send").onclick = send;
  $("#ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });

  // --- show generator: prompt → concepts → explicit confirm → import ----
  let design = null;

  $("#ai-design").onclick = async () => {
    const prompt = $("#ai-prompt").value.trim();
    const note = $("#ai-gen-note");
    if (!prompt) { note.textContent = "describe the show first"; return; }
    const btnDesign = $("#ai-design");
    btnDesign.disabled = true;
    note.textContent = "designing…";
    try {
      const d = await api("/api/console/generate", {
        prompt: prompt, offline: !!$("#ai-offline").checked,
      });
      const r = d.result || {};
      design = r;
      const concepts = (r.design && r.design.concepts) || [];
      const sel = $("#ai-concepts");
      sel.innerHTML = "";
      concepts.forEach((c, i) => {
        const o = document.createElement("option");
        o.value = String(i);
        o.textContent = c.name || "concept " + (i + 1);
        sel.appendChild(o);
      });
      sel.classList.toggle("hidden", !concepts.length);
      $("#ai-load").classList.toggle("hidden", !concepts.length);
      const bits = [];
      if (r.brief && r.brief.event) bits.push(r.brief.event);
      if (r.brief && r.brief.pace) bits.push(r.brief.pace);
      if (concepts.length) bits.push(concepts.length + " concepts");
      if (r.design && Array.isArray(r.design.assumptions)
          && r.design.assumptions.length) bits.push(r.design.assumptions[0]);
      note.textContent = bits.join(" · ")
        + (r.source === "llm" ? "" : "  [offline brief]");
      aiSay("bot", concepts.length
        ? "Designed " + concepts.length + " show concepts — pick one and "
          + "press ✓ load show (nothing runs until you confirm)."
        : "No concepts came back — try a fuller prompt.", { source: r.source });
    } catch (err) {
      note.textContent = "design failed: " + err.message;
    } finally {
      btnDesign.disabled = false;
    }
  };

  $("#ai-load").onclick = async () => {
    if (!design) return;
    const concepts = (design.design && design.design.concepts) || [];
    const concept = concepts[Number($("#ai-concepts").value || 0)];
    if (!concept) return;
    const name = $("#show-name").value.trim() || concept.name || "generated show";
    if (!window.confirm('Load "' + (concept.name || "concept")
        + '" as a cue stack on PB1?\nShow name: ' + name
        + "\nNothing plays until you press a playback GO.")) return;
    try {
      const d = await api("/api/console/import_show", {
        concept: concept, playback: 1, name: name,
      });
      const r = d.result || {};
      if (r.ok === false) {
        showErr("load show: " + (r.error || "refused"));
        return;
      }
      showErr(r.summary || ("loaded " + (r.cues || 0) + " cues"), "info");
      await loadState();
      broadcast("playback");
    } catch (err) {
      showErr("load show: " + err.message);
    }
  };
}

/* ---------------------------------------------------------- visualiser */

function buildVizIfChanged() {
  const patch = (S && S.patch) || [];
  // Only the fixture SET and its identity belong in this signature.  A
  // position used to be in here too, which meant every single drag of a
  // light tore the whole visualiser down and built it again - and because
  // the camera lives inside that object, the view snapped back to the
  // middle of the room on every nudge.  Positions are mutable state the
  // visualiser already accepts via setPositions(), so they never needed a
  // rebuild in the first place.
  const sig = patch.map((h) =>
    [h.head_no, h.model, h.mode, h.role, h.kind].join(",")).join("|");
  if (sig === vizSig) return;
  vizSig = sig;
  rebuildViz(patch);
}

function rebuildViz(patch) {
  const pane = $("#viz-pane");
  // Keep the operator's viewpoint across the rebuild.  Even with the
  // position-churn fixed, a re-patch or a loaded layout still rebuilds,
  // and losing the camera then is the same complaint all over again.
  const keepCam = viz && viz.camera ? viz.camera() : camSaved;
  if (viz && viz.destroy) { try { viz.destroy(); } catch (e) { /* ignore */ } }
  viz = null;
  // The visualiser picks a fixture BODY from the model name first and the
  // channel map second (viz.js bodyFor), so both have to come across -
  // without them every head draws as the same generic can and the whole
  // point of "3D models match the light" is lost.
  vizFixtures = patch.map((h) => ({
    head_no: h.head_no,
    name: h.name || "",
    role: h.role || "generic",
    kind: h.kind,
    manufacturer: h.manufacturer || "",
    model: h.model || "",
    mode: h.mode || "",
    channels: h.channels || 0,
    map: h.map || [],
    x: Number(h.x) || 0,
    y: Number(h.y) || 0,
    z: Number(h.z) || 0,
    _look: looks[h.head_no] || { hex: "#f4f7ff", a: 0 },
  }));
  pane.replaceChildren();
  if (!window.Viz) return;
  const venue = vizVenue();
  // The stage the visualiser scales to must be the SAME room the venue
  // describes, or the two overlap into two half-transparent boxes and the
  // operator cannot tell which one the beams are landing on.  When a venue
  // is stored it wins; otherwise the stage is sized around the patch.
  const patchSpan = { w: 0, d: 0 };
  vizFixtures.forEach((f) => {
    patchSpan.w = Math.max(patchSpan.w, Math.abs(f.x));
    patchSpan.d = Math.max(patchSpan.d, f.z);
  });
  const stageW = Math.max(venue.width_m || 0, patchSpan.w * 2 + 4, 8);
  const stageD = Math.max(venue.depth_m || 0, patchSpan.d + 4, 6);
  try {
    viz = window.Viz.create(pane, {
      stage: {
        width: stageW,   // centred x, span + margin
        depth: stageD,   // z: 0 upstage → depth downstage
        structure: "goalpost",
        fixtures: vizFixtures,
      },
      cues: [],
    }, {
      still: true,
      // The console is a real desk: the operator drags the lights to
      // where they actually hang.  editable turns on fixture picking
      // (click = select in the patch list, drag = move, shift+drag =
      // height), and the two callbacks commit the change to the engine.
      editable: true,
      onPick: (head, mods) => pickFixtureInPatch(head, mods),
      onMoveFixture: (head, x, y, z) => queueFixtureMove(head, x, y, z),
      // remember the viewpoint, and hand it straight back to the new view
      onCamera: (s) => { camSaved = s; syncCamButtons(); },
      venue: venue,
    });
    // Seed the live look map so the first feed tick eases from what the
    // head list already showed, instead of popping from black.
    if (viz.setLooks) viz.setLooks(looks, 0);
    else viz.redraw();
    if (keepCam && viz.setCamera) viz.setCamera(keepCam, true);
    else frameViz();
    syncCamButtons();
    // The twin is handed over AFTER the view exists, and a fresh view gets a
    // null twin until this runs - so the visualiser is never in a state
    // where it is holding a scene belonging to a destroyed GL context.
    loadTwin();
  } catch (e) {
    viz = null;
  }
}

/* ==========================================================================
 * THE GDTF TWIN
 *
 * The console owns this, not the visualiser, and that split is deliberate.
 * The twin answers "what does this fixture physically look like and where
 * is each of its parts right now"; the visualiser answers "how do I put
 * that on screen".  The visualiser is handed a finished scene and draws
 * whatever geometry the definitions in it carry, so the existing rendering
 * is untouched whenever a definition has nothing - which is the case for
 * every built-in profile, every fixture whose .gdtf has gone, and every
 * model that fails to load.
 *
 * Every step here is optional and every failure is silent by design.  A
 * console whose lights depend on a 3D file parsing is a console that stops
 * working when a 3D file does not parse, and the DMX must go out either
 * way.  The twin is a presentation layer; it is allowed to be absent.
 * ========================================================================== */
let twinScene = null;
let twinDeg = {};        // head_no -> {pan:[lo,hi], tilt:[lo,hi]}, from the engine
let twinTried = false;   // one attempt per patch revision, not one per frame

function twinModelBytes(defId, entry) {
  const url = "/api/console/model?id=" + encodeURIComponent(defId)
    + "&name=" + encodeURIComponent(entry.name);
  const headers = {};
  if (apiToken()) headers["X-Jarvis-Token"] = apiToken();
  return fetch(url, { headers: headers }).then((r) => {
    if (!r.ok) throw new Error("model " + r.status);
    return r.arrayBuffer();
  });
}

async function loadTwin() {
  if (!window.GDTF3D || !viz) return;
  twinTried = true;
  try {
    const d = await api("/api/console/models");
    if (!d || !d.definitions || !d.definitions.length) {
      if (viz.setTwin) viz.setTwin(null);
      return;
    }
    const scene = new window.GDTF3D.Scene();
    d.definitions.forEach((m) => scene.addManifest(m));
    // Hang position comes from the PATCH, not from the profile: where a
    // fixture physically is, is the operator's decision and the engine's
    // record, and duplicating it here would be a second source of truth.
    if (S && Array.isArray(S.patch)) {
      S.patch.forEach((h) => {
        const inst = scene.get(h.head_no);
        if (inst) inst.setPosition(h.x || 0, h.y || 0, h.z || 0);
      });
    }
    twinScene = scene;
    if (viz.setTwin) viz.setTwin(scene);
    // Models load AFTER the scene is handed over, so a fixture appears the
    // instant its geometry resolves rather than the instant the manifest
    // arrives.  Each definition's load is deduped inside the scene, and the
    // per-stem catch inside it means one bad model costs one part.
    for (const m of d.definitions) {
      if (!m.models || !Object.keys(m.models).length) continue;
      await scene.loadModels(m.id, twinModelBytes);
    }
    if (viz.redraw) viz.redraw();
    twinStatus();
  } catch (err) {
    // No error banner.  A missing or failing twin is not something the
    // operator can act on, and shouting about it would suggest the console
    // is broken when the only thing missing is decoration.
    twinScene = null;
    if (viz.setTwin) viz.setTwin(null);
  }
}

/* Degrees, not 0..1.  The feed carries a normalised aim and the ENGINE
 * carries the travel; the product is the physical angle, and that is what
 * the geometry solver wants.  Deriving it here rather than in the solver
 * keeps the travel a property of the engine's patch - it changes when the
 * mode changes, and the twin has no business knowing that. */
function feedTwin() {
  if (!twinScene) return;
  twinScene.instances.forEach((inst) => {
    const lk = looks[inst.head] || {};
    const dg = twinDeg[inst.head] || {};
    const toDeg = (v, r) => (typeof v === "number" && r && r.length === 2)
      ? r[0] + v * (r[1] - r[0]) : 0;
    inst.setDmx({
      hex: lk.hex || "#334155",
      a: lk.a || 0,
      panDeg: toDeg(lk.pan, dg.pan),
      tiltDeg: toDeg(lk.tilt, dg.tilt),
    });
  });
}

/* A diagnostic surface, deliberately, not a test hook.
 *
 * "Why is this head still a box when its profile has a model?" is a real
 * question, asked by real people, and the answer lives in a JavaScript
 * object's `failed` map - invisible from the DOM, unrecoverable from the
 * network tab once the request succeeded.  Without a way to read it, the
 * only available response is to guess, and a wrong guess about a lighting
 * rig is expensive.
 *
 * It is read-only, it exposes nothing that is not already either in the DOM
 * or in a response this page already fetched and holds, and it is how the
 * suite and the console agree on what "fallback" means. */
window.jarvisTwin = function () {
  if (!twinScene) return { present: false };
  const defs = [];
  twinScene.defs.forEach((d, id) => {
    defs.push({
      id: id, state: d.state, ok: d.ok, reason: d.reason || "",
      refs: d.refs, scale: d.scale,
      nodes: d.nodes.length,
      modelsWanted: Object.keys(d.models || {}),
      modelsLoaded: Object.keys(d.meshes || {}),
      failed: d.failed || {},
      onGpu: d.gl ? Object.keys(d.gl) : [],
    });
  });
  return {
    present: true, stats: twinScene.stats(), definitions: defs,
    twins: viz && viz.twinStats ? viz.twinStats() : null,
  };
};

/* Park the camera on one head, for support and for looking at a fixture
 * close up.  The named-view buttons compare YAW only, so FRONT and TOP
 * always light up together and a plan view cannot be told from an
 * elevation; a pose has to be settable directly for that to be fixable
 * from the outside. */
window.jarvisLook = function (headNo, dist) {
  if (!viz || !viz.setCamera || !viz.camera) return null;
  const h = (S && Array.isArray(S.patch))
    ? S.patch.find((x) => x.head_no === headNo) : null;
  if (!h) return null;
  const cam = viz.camera() || {};
  // The camera's target is `tgt`, an ARRAY - not tx/ty/tz.  The first
  // version of this passed tx/ty/tz, which setCamera silently ignored, so
  // the camera never moved and the helper looked like it was framing
  // nothing.  A setter that ignores unknown keys is exactly the kind of
  // thing a wrong guess fails at quietly.
  viz.setCamera({
    tgt: [h.x || 0, h.y || 0, h.z || 0],
    yaw: cam.yaw !== undefined ? cam.yaw : -0.55,
    pitch: 0.16,
    dist: dist || 1.8,
  }, true);
  if (viz.redraw) viz.redraw();
  return viz.camera();
};

/* Light a head up, so a fixture is drawn lit - which is when the model's
 * own colour comes from the beam rather than from the housing.  Both
 * `set_intensity` and `set_colour` act on the SELECTION, so the head is
 * selected first; doing it the other way round quietly does nothing to a
 * head that was not selected, and a fixture that stays dark looks like a
 * model that failed. */
window.jarvisLight = async function (headNo, hex, level) {
  if (!S) return null;
  await api("/api/console", { action: "select_heads",
                              params: { heads: [headNo] } });
  await api("/api/console", { action: "set_intensity",
                              params: { level: level === undefined ? 100 : level } });
  await api("/api/console", { action: "set_colour",
                              params: { hex: hex || "#ffb060" } });
  if (pushLooks) pushLooks(0);
  return true;
};;

function twinStatus() {
  const el = document.getElementById("twin-status");
  if (!el) return;
  // The `.hidden` CLASS, matching the rest of the console.  The `hidden`
  // attribute is reserved for the three elements that need it; a fourth
  // would be the second convention for one idea, which is the mistake the
  // permanently-open help dialog came from.
  const show = !!(twinScene && viz && viz.twinStats && viz.twinStats().instances);
  el.classList.toggle("hidden", !show);
  if (!show) { el.textContent = ""; return; }
  const s = viz.twinStats();
  const bits = [];
  if (s.fallback) {
    bits.push(s.fallback + " generic");
  }
  if (s.models) {
    bits.push(s.models + " model" + (s.models === 1 ? "" : "s")
              + " / " + s.tris.toLocaleString() + " tris");
  }
  bits.push(s.definitions + " type" + (s.definitions === 1 ? "" : "s")
            + " shared by " + s.instances + " heads");
  el.textContent = "3D: " + bits.join(" · ");
  el.title = s.fallback
    ? "Fixtures with no usable GDTF model are drawn as generic bodies."
    : "Real GDTF geometry, one shared model per fixture type.";
}

/* ------------------------------------------------------------ camera bar */
let camSaved = null;

// Frame the current selection, or the whole rig when nothing is selected.
// A first build has no camera to preserve, so it frames the room.
function frameViz() {
  if (!viz || !viz.frame) return;
  const sel = (S && S.selected) || [];
  const heads = vizFixtures.filter((f) => sel.indexOf(f.head_no) >= 0);
  viz.frame(heads.length ? heads : null);
}

function vizView(name) {
  if (viz && viz.view) viz.view(name);
}

function vizZoom(factor) {
  if (viz && viz.zoom) viz.zoom(factor);
}

// Reflect the active named view back onto the buttons, so the bar is a
// readout of where the camera is rather than a row of guesses.
function syncCamButtons() {
  const bar = $("#viz-cam");
  if (!bar) return;
  if (viz && viz.camera) camSaved = viz.camera();
  const c = camSaved;
  bar.querySelectorAll("[data-view]").forEach((b) => {
    const name = b.getAttribute("data-view");
    let on = false;
    if (c) {
      const want = { front: 0, left: -Math.PI / 2, right: Math.PI / 2,
                     back: Math.PI, top: 0, home: -0.55 }[name];
      if (want !== undefined) {
        // allow a little slack: these are rounded poses, not exact angles
        let d = Math.abs(((c.yaw - want) % (Math.PI * 2) + Math.PI * 3)
                         % (Math.PI * 2) - Math.PI);
        on = d < 0.12;
      }
    }
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const fs = $("#cam-full");
  if (fs) {
    const on = fsActive();
    fs.classList.toggle("on", on);
    fs.setAttribute("aria-pressed", on ? "true" : "false");
    fs.textContent = on ? "⛶ EXIT" : "⛶ FULL";
    fs.title = on ? "Leave full screen (Esc)"
                  : "Full screen \u2014 move lights with the whole view "
                    + "(Esc to leave)";
  }
  // Hide the gesture reference once the operator is driving the camera:
    // it has done its job, and it sits over the floor they are working on.
  const hint = $("#viz-hint");
  if (hint) hint.classList.toggle("dim", camTouchedByOperator());
}

function camTouchedByOperator() {
  return !!(viz && viz.touched && viz.touched());
}

/* Full screen for the 3D view.
 *
 * Hanging a rig means reaching over the whole room, and at desk size this
 * view is a letterbox between two columns - far too small to drag lights
 * around in.  This is the move Unity makes: give the viewport the whole
 * display and get out of the way.
 *
 * A CSS overlay does the work, NOT the Fullscreen API.  The API needs the
 * document to be focused, it can be refused, and a refused request can
 * leave its promise pending forever rather than rejecting - so a button
 * wired only to it looks dead, with nothing in the console to say why.
 * `position: fixed` cannot be refused and cannot hang.  The native API is
 * still requested on top: if it succeeds the browser chrome disappears as
 * well, and if it does not, the operator has the full-screen view anyway
 * and never has to know the difference.
 */
function fsActive() {
  const wrap = $("#viz-wrap");
  if (wrap && wrap.classList.contains("fs")) return true;
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

function applyFs(on) {
  const wrap = $("#viz-wrap");
  if (!wrap) return;
  wrap.classList.toggle("fs", on);
  document.body.classList.toggle("fs-lock", on);
  if (!on && (document.fullscreenElement || document.webkitFullscreenElement)) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) Promise.resolve(exit.call(document)).catch(() => { /* fine */ });
  }
  if (on) {
    // The canvas keeps its old drawing-buffer size until it is measured, so
    // the view would come back stretched; and the fly keys need the focus.
    window.dispatchEvent(new Event("resize"));
    const pane = wrap.querySelector("#viz-pane canvas");
    if (pane) { try { pane.focus({ preventScroll: true }); } catch (x) { /* */ } }
  } else {
    window.dispatchEvent(new Event("resize"));
  }
  syncCamButtons();
}

function toggleFullscreen() {
  if (fsActive()) { applyFs(false); return; }
  applyFs(true);                       // works unconditionally
  // and now try to also take the browser chrome out of the way
  const wrap = $("#viz-wrap");
  const req = wrap && (wrap.requestFullscreen || wrap.webkitRequestFullscreen);
  if (!req) return;
  let settled = false;
  const settle = () => { settled = true; };
  try {
    const r = req.call(wrap, { navigationUI: "hide" });
    if (r && typeof r.then === "function") {
      r.then(settle, () => {
        // Refused.  The overlay is already up and stays up, which is the
        // outcome the operator wanted, so this is not an error to shout
        // about - it only means the browser keeps its own toolbar.
        settle();
      });
    } else {
      settled = true;
    }
  } catch (x) {
    settle();
  }
  void settled;
}

function wireCamBar() {
  const bar = $("#viz-cam");
  if (!bar) return;
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    e.preventDefault();
    if (b.hasAttribute("data-view")) {
      vizView(b.getAttribute("data-view"));
    } else if (b.hasAttribute("data-zoom")) {
      vizZoom(Number(b.getAttribute("data-zoom")) || 1);
    } else if (b.id === "cam-frame") {
      frameViz();
    } else if (b.id === "cam-full") {
      toggleFullscreen();
    }
    syncCamButtons();
  });
  // Esc leaves full screen on its own, but the browser also fires this when
  // full screen ends for any other reason, so the button label cannot drift.
  document.addEventListener("fullscreenchange", syncCamButtons);
  document.addEventListener("webkitfullscreenchange", syncCamButtons);
}

// The patch can legitimately be empty - a fresh install has nothing in it -
// and the visualiser is the largest thing on screen.  An unexplained black
// rectangle reads as a broken app, so say what it is and what to do.
function renderVizEmptyState() {
  const node = $("#viz-empty");
  if (!node) return;
  node.classList.toggle("hidden", !!((S && S.patch) || []).length);
}

/* Venue geometry for the 3D view: what the operator drew, or a sensible
 * stage around the patch.  Never invented walls - with nothing stored we
 * show an open floor, which is the "be creative" starting point. */
function vizVenue() {
  const patch = (S && S.patch) || [];
  let maxX = 0, maxZ = 0;
  patch.forEach((h) => {
    maxX = Math.max(maxX, Math.abs(Number(h.x) || 0));
    maxZ = Math.max(maxZ, Number(h.z) || 0);
  });
  const w = Math.max(6, maxX * 2 + 2);
  const d = Math.max(5, maxZ + 2);
  const stored = (S && S.venue) || window.localStorage.getItem("jarvis.venue");
  let v = null;
  if (stored) {
    try { v = JSON.parse(stored); } catch (e) { v = null; }
  }
  if (!v || !v.width_m) {
    v = { width_m: w, depth_m: d, height_m: 0, name: "stage", surfaces: [] };
  }
  return v;
}

/* Click a light in the 3D view -> select + scroll to it in the patch. */
// A light was clicked in the 3D view.
//
// It selects that head (shift-click adds to the selection, matching the
// patch list), flashes the light so the click has an immediate visible
// consequence rather than waiting for the feed, scrolls the patch row into
// view, and leaves the highlight itself to renderSelection - which is the
// single source of truth for what is selected, in the list AND in the view.
function pickFixtureInPatch(head, mods) {
  if (!head) return;
  if (viz && viz.flash) viz.flash(head);
  selectHead(Number(head), !!(mods && mods.shift), !!(mods && mods.toggle));
  const row = document.querySelector('#head-list .hrow[data-head="'
    + head + '"]');
  if (row && row.scrollIntoView) {
    try { row.scrollIntoView({ block: "nearest" }); } catch (e) { /* older */ }
  }
}

/* Drag a light -> move it on the stage and persist the position.
 *
 * `x`, `y` and `z` are metres and go to the engine as numbers; `kind` is
 * a SEPARATE field.  The two used to be conflated, which sent
 * z: "truss" and made every drag fail on the server with a float()
 * error while the 3D view silently kept the dragged position - so the
 * light appeared to move and then jumped back on the next re-patch.
 */
let moveQueue = [];
let moveTimer = 0;
function queueFixtureMove(head, x, y, z) {
  moveQueue = [{
    head: head,
    x: Number(x) || 0,
    y: Math.max(0, Number(y) || 0),
    z: Math.max(0, Number(z) || 0),
    kind: (Number(y) || 0) >= 2.0 ? "truss" : "floor",
  }];
  clearTimeout(moveTimer);
  // Coalesce: one action per drag gesture, not one per pointermove.
  moveTimer = setTimeout(async () => {
    const m = moveQueue[0];
    moveQueue = [];
    if (!m) return;
    const r = await doAction("set_place", m, { quiet: true });
    if (r && r.ok === false) {
      // The 3D view is now showing a position the engine refused, so put
      // it back rather than leaving the operator with a lie on screen.
      showErr("move: " + (r.error || "failed"));
      if (viz && viz.setPositions) {
        const h = S.patch.find((p) => p.head_no === m.head);
        if (h) {
          viz.setPositions({ [m.head]: { x: h.x, y: h.y, z: h.z, kind: h.kind } });
        }
      }
    } else if (r && r.head_no) {
      updateHeadRow(r);
      // A head that hit the stage edge stops there rather than walking off
      // into nothing.  Say so: the light visibly refusing to move further
      // is otherwise indistinguishable from a dropped drag.
      if (r.clamped) {
        const b = r.bounds || {};
        showErr("head " + r.head_no + " reached the edge of the stage"
          + (b.half_width_m ? " (x ±" + b.half_width_m + " m, z ≤ "
             + b.max_depth_m + " m)" : "")
          + " — move the room or the light to go further", "move");
      } else {
        hideErr("move");
      }
    }
  }, 220);
}

/* Merge one server look row into the local map, keeping pan/tilt.

 * pan/tilt are only present when the fixture HAS the channel AND
 * something is driving it (engine `_aim01`).  That absence is
 * meaningful: it means "not driven", so the visualiser must fall back to
 * its geometric aim.  Copying a missing aim across as a number would
 * freeze a head that is actually being pointed around; dropping the aim
 * on every head that has no pan would freeze every static fixture.  So
 * `aim` is copied when it arrives and left untouched when it does not.
 */
function mergeLook(n, hex, a, aim) {
  const prev = looks[n];
  const next = { hex: hex, a: a };
  const hasAim = aim && (typeof aim.pan === "number" || typeof aim.tilt === "number");
  if (hasAim) {
    next.pan = aim.pan;
    next.tilt = aim.tilt;
  } else if (prev && (prev.pan !== undefined || prev.tilt !== undefined)) {
    // aim no longer driven: forget it so the visualiser re-derives
    next.pan = undefined;
    next.tilt = undefined;
  }
  if (!prev || prev.hex !== next.hex || prev.a !== next.a
      || prev.pan !== next.pan || prev.tilt !== next.tilt) {
    looks[n] = next;
    return true;
  }
  return false;
}

/* Every patched head, lit or not - the live feed only sends the lit ones. */
function applyHeads(heads) {
  let changed = false;
  const live = new Set();
  heads.forEach((h) => {
    if (!h) return;
    live.add(h.n);
    if (h.deg) twinDeg[h.n] = h.deg;
    const lk = h.look || {};
    const a = lk.on === false ? 0 : (typeof lk.a === "number" ? lk.a : 0);
    const hex = lk.hex || (looks[h.n] && looks[h.n].hex) || "#f4f7ff";
    if (mergeLook(h.n, hex, a, lk)) changed = true;
  });
  // Drop looks for heads that are no longer patched: the lite feed omits
  // heads while the patch rev is unchanged, so a re-patch could leave a
  // removed head glowing in the visualiser forever.
  Object.keys(looks).forEach((k) => {
    if (!live.has(Number(k))) {
      delete looks[k];
      changed = true;
    }
  });
  if (changed) pushLooks();
  else feedTwin();
}

/* Hand the current look map to the visualiser, which eases toward it. */
function pushLooks(holdMs) {
  vizFixtures.forEach((f) => {
    f._look = looks[f.head_no] || { hex: "#f4f7ff", a: 0 };
  });
  // The twin is fed here, next to the visualiser, rather than from the
  // feed's own tick: both draw on the same frame, so a head's beam and the
  // head pointing it can never disagree by a frame.
  feedTwin();
  if (!viz) return;
  if (viz.setLooks) viz.setLooks(looks, holdMs);
  else viz.redraw();
}

/* -------------------------------------------------------------- look feed */
// The lite feed carries STRUCTURE (patch, playbacks, effects) and only
// ships head looks when the patch revision changes - correct for the
// patch, wrong for light: a 4-second cue fade would step ten times a
// second.  This feed is the light: only emitting heads, at the output
// rate, and the visualiser eases between ticks (see viz.setLooks).
let lookSeq = -1;
let lookTimer = 0;
let lookFails = 0;

async function lookFeed() {
  if (!S) return;
  try {
    const d = await api("/api/console/look?since=" + lookSeq);
    if (d && d.full) lookSeq = -1;
    if (!d || d.seq === lookSeq) return;
    lookSeq = d.seq;
    lookFails = 0;
    if (!d.heads || !d.heads.length) {
      // everything dark: clear the map so heads ease out
      Object.keys(looks).forEach((k) => delete looks[k]);
      pushLooks(220);
      return;
    }
    let changed = false;
    const seen = new Set();
    d.heads.forEach((h) => {
      seen.add(h.n);
      if (h.deg) twinDeg[h.n] = h.deg;
      if (mergeLook(h.n, h.hex || "#f4f7ff", h.a || 0, h)) changed = true;
    });
    // The travel is recorded on every tick even when nothing visibly
    // changed, because a pan that does not alter the colour still has to
    // move the head, and the twin is fed from the same tick.
    feedTwin();
    // A head that stops emitting must fade to dark, not linger.
    Object.keys(looks).forEach((k) => {
      if (!seen.has(Number(k)) && looks[k].a > 0) {
        looks[k] = { hex: looks[k].hex, a: 0 };
        changed = true;
      }
    });
    if (changed) pushLooks(140);
  } catch (err) {
    lookFails++;
    if (lookFails === 5) showErr("look feed unreachable — " + err.message,
                                "feed");
  }
}

/* ================================================================ wiring */

function wireHeader() {
  $("#btn-assistant").onclick = () => window.open("/", "jarvis_assistant");

  $("#btn-dryrun").onclick = async () => {
    if (!S) return;
    const turning_off = !!S.dry_run;
    const params = { state: !S.dry_run };
    // One direction is free and the other is not.  Going to LIVE while
    // the output is already running starts driving real fixtures THIS
    // TICK, so it asks; going back to dry run is the safe direction and
    // must not be made harder to reach, because that is the one you want
    // in a hurry.
    if (turning_off && S.live) {
      if (!window.confirm(
          "Turn DRY RUN off.\n\n"
          + "The output is RUNNING, so real Art-Net frames will leave this "
          + "machine immediately and the rig can move.\n\nContinue?")) return;
      params.confirm = true;
    }
    const r = await doAction("set_dry_run", params, { broadcast: "mode" });
    if (r && r.ok === false) {
      showErr("dry run: " + (r.error || "refused"));
      return;
    }
    await loadState();
    renderStatus();
  };

  $("#btn-golive").onclick = async () => {
    if (!S) return;
    if (S.live) {
      await doAction("set_output", { state: false }, { broadcast: "mode" });
      return;
    }
    const params = { state: true };
    if (!S.dry_run) {                        // armed → the confirm step
      if (!window.confirm("GO LIVE — real Art-Net frames will leave this machine.\n" +
          "The rig can move. Continue?")) return;
      params.confirm = true;
    }
    await doAction("set_output", params, { broadcast: "mode" });
  };

  $("#btn-save").onclick = async () => {
    const name = $("#show-name").value.trim();
    if (!name) {
      showErr("save: type a show name first");
      $("#show-name").focus();
      return;
    }
    if (!/^[A-Za-z0-9 _-]{1,40}$/.test(name)) {
      showErr("save: letters, digits, spaces, dash and underscore only (max 40)");
      return;
    }
    try {
      const r = await api("/api/console/save", { name: name });
      showErr("saved → " + (r.file || name), "info");
      await loadState();
      broadcast("show");
    } catch (err) {
      showErr("save: " + err.message);
    }
  };

  $("#load-show").onchange = async (e) => {
    const name = e.target.value;
    e.target.value = "";
    if (!name) return;
    if (!window.confirm('Load show "' + name + '"? Patch, cues and playbacks are replaced.')) return;
    try {
      const r = await api("/api/console/load", { name: name });
      showErr("loaded ← " + (r.file || name), "info");
      await loadState();
      broadcast("show");
    } catch (err) {
      showErr("load: " + err.message);
    }
  };
}

function wirePatchTools() {
  $("#btn-add").onclick = openDialog;
  $("#btn-selectall").onclick = () => doAction("select_all", {});
  $("#btn-clearsel").onclick = () => doAction("clear_selection", {});
  $("#btn-autopatch").onclick = () => {
    if (!window.confirm("Re-pack every address in head order? Overlaps are resolved.")) return;
    doAction("auto_patch", {}, { broadcast: "patch" });
  };
  $("#btn-layout").onclick = async () => {
    if (!window.confirm("Replace the patch with the currently selected layout?")) return;
    try {
      const r = await api("/api/console/patch", { action: "from_layout" });
      if (r && r.ok === false) {
        showErr("layout: " + (r.error || "import failed"));
        return;
      }
      const n = r && typeof r.heads === "number" ? " · " + r.heads + " heads" : "";
      showErr("layout loaded" + n, "info");
      await loadState();
      broadcast("layout");
    } catch (err) {
      showErr("layout: " + err.message);
    }
  };

  $("#btn-group").onclick = () => {
    const f = $("#group-form");
    f.classList.toggle("hidden");
    if (!f.classList.contains("hidden")) $("#group-name").focus();
  };
  $("#group-cancel").onclick = () => {
    $("#group-form").classList.add("hidden");
    $("#group-name").value = "";
  };
  $("#group-ok").onclick = createGroup;
  $("#group-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") createGroup();
    if (e.key === "Escape") $("#group-cancel").click();
  });
}

function wireProgrammer() {
  const inten = $("#intensity");
  const intenNum = $("#intensity-num");
  const master = $("#master");
  const masterNum = $("#master-num");

  trackDragging(inten);
  trackDragging(master);

  // Live while dragging: the level follows the fader instead of snapping
  // into place when the pointer stops.  The old 180 ms trailing debounce
  // sent nothing at all for the whole drag and one call at the end.
  const sendIntensity = liveSender(() =>
    doAction("set_intensity",
             { level: clampInt(inten.value, 0, 100, 0) }, { quiet: true }));
  const sendMaster = liveSender(() =>
    doAction("master",
             { level: clampInt(master.value, 0, 100, 100) }, { quiet: true }));

  inten.addEventListener("input", () => {
    intenNum.value = inten.value;
    sendIntensity.push();
  });
  inten.addEventListener("change", () => sendIntensity.flush());
  intenNum.addEventListener("change", () => {
    const v = clampInt(intenNum.value, 0, 100, 0);
    intenNum.value = v;
    inten.value = v;
    doAction("set_intensity", { level: v });
  });

  master.addEventListener("input", () => {
    masterNum.value = master.value;
    sendMaster.push();
  });
  master.addEventListener("change", () => sendMaster.flush());
  masterNum.addEventListener("change", () => {
    const v = clampInt(masterNum.value, 0, 100, 100);
    masterNum.value = v;
    master.value = v;
    doAction("master", { level: v });
  });

  const hexIn = $("#hex-in");
  hexIn.addEventListener("change", () => {
    const hex = normHex(hexIn.value);
    if (!hex) {
      showErr("colour: use #rrggbb");
      hexIn.value = "";
      renderProgrammer();
      return;
    }
    // Move the picker too.  Three controls for one value is two controls too
    // many unless they are all told what the others did.
    PICK.shown = hex;
    adoptHex(hex);
    doAction("set_colour", { hex: hex });
  });
  $("#btn-white").onclick = () => {
    setPick(0, 0, 1, true);
    doAction("set_colour", { hex: "#ffffff" });
  };

  $("#btn-attr").onclick = () => {
    const a = $("#attr-name").value.trim();
    const raw = $("#attr-val").value;
    if (!a) { showErr("raw attr: name the attribute (e.g. shutter)"); return; }
    if (raw === "" || !Number.isFinite(Number(raw))) {
      showErr("raw attr: give a value 0–255");
      return;
    }
    doAction("set_attribute", {
      attribute: a,
      value: clampInt(raw, 0, 255, 0),
    });
  };

  // --- running effects (FX) -------------------------------------------
  $("#btn-fx-run").onclick = () => {
    const speed = Number.parseFloat($("#fx-speed").value);
    $("#fx-speed").value = Number.isFinite(speed)
      ? Math.min(20, Math.max(0.01, speed)) : 1.5;
    doAction("run_fx", {
      attribute: $("#fx-attr").value,
      wave: $("#fx-wave").value,
      speed: Number.parseFloat($("#fx-speed").value),
    });
  };
  $("#btn-fx-stop").onclick = () => doAction("stop_fx", {});

  $("#btn-locate").onclick = () => doAction("locate", {});
  $("#btn-clear").onclick = () => doAction("clear_programmer", {});
  $("#btn-blackout").onclick = () => {
    if (!S) return;
    const next = S.blackout ? 0 : 1;
    S.blackout = !!next;
    renderProgrammer();
    doAction("blackout", { state: next });
  };
}

/* ------------------------------------------------------- add heads dialog */

let dlgFixture = null;

function openDialog() {
  $("#add-dialog").classList.remove("hidden");
  $("#dlg-search").value = "";
  closePick();
  dlgErr(null);
  // The editor panels start collapsed: they are reached deliberately, and
  // a dialog that opens with two forms already expanded hides its own
  // search box.
  ["#fx-editor", "#fx-new"].forEach((sel) => {
    const el = $(sel);
    if (el) el.classList.add("hidden");
  });
  // Browse-first: the library list is populated immediately, so the dialog
  // is useful before a single character is typed.  A GDTF import may have
  // added types since the last open, so refresh every time.
  loadLibrary();
  // Always come back to the installed list, never to wherever the last
  // download left the tabs - the next thing to do is usually "add this".
  sharePane(false);
  $("#dlg-search").focus();
}

function closeDialog() {
  $("#add-dialog").classList.add("hidden");
}

function closePick() {
  dlgFixture = null;
  $("#dlg-pick").classList.add("hidden");
  $("#dlg-add").disabled = true;
  document.querySelectorAll("#dlg-lib-list .lib-item.on")
    .forEach((e) => e.classList.remove("on"));
}

function dlgErr(msg) {
  const el = $("#dlg-err");
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
}

/* The whole fixture library, fetched once and filtered client-side.
 *
 * Before, the dialog required typing at least two characters before
 * anything appeared - you could not browse, so "which fixtures do I
 * have?" was unanswerable and picking a type you half-remembered was
 * guesswork.  The side list is the library; the search box filters it.
 * The server still owns the query, so a fixture added by a GDTF import
 * while the dialog is open shows up on the next open. */
let library = [];
let libraryLoaded = false;

function fixtureLabel(r) {
  return ((r.manufacturer || "") + " " + (r.model || "")).trim();
}

/* ------------------------------------------------------- DMX channel sheet
 * The diagnostic this console did not have.  Every DMX fault starts with
 * three questions - which bytes, which channel, what address - and until
 * this existed the only way to answer them was a DMX tester at the far
 * end of the cable.
 *
 * The values are NOT computed here.  They arrive from the server's
 * `build_frames` output, i.e. the bytes that will actually be sent, so
 * the table cannot disagree with the wire the way a client-side
 * re-derivation would - it would be wrong in exactly the cases that
 * matter (16-bit splits, HTP merge, master and blackout) and wrong the
 * same way every time, so it would survive being checked once.
 */
function openChannels() {
  const dlg = $("#ch-dialog");
  if (!dlg) return;
  dlg.classList.remove("hidden");
  loadChannels();
}

function closeChannels() {
  const dlg = $("#ch-dialog");
  if (dlg) dlg.classList.add("hidden");
}

function loadChannels() {
  const body = $("#ch-body");
  if (!body) return;
  const heads = Array.isArray(S.selected) && S.selected.length
    ? S.selected : null;
  let url = "/api/console/channels";
  if (heads) url += "?heads=" + heads.join(",");
  body.replaceChildren(node("div", "muted small", "reading the DMX…"));
  api(url).then((data) => renderChannels(data)).catch((err) => {
    body.replaceChildren(node("div", "ch-note", "channels: " + err.message));
  });
}

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function renderChannels(data) {
  const body = $("#ch-body");
  const sum = $("#ch-summary");
  if (sum) sum.textContent = data.summary || "";
  body.replaceChildren();
  if (!data.heads || !data.heads.length) {
    body.appendChild(node("div", "empty small",
      "select one or more heads first"));
    return;
  }
  // The headline finding, stated once, before the tables: how many
  // channels the console has no control over at all.
  if (data.uncontrolled) {
    body.appendChild(node("div", "ch-note",
      data.uncontrolled + " of " + data.total + " channels are not "
      + "controllable from this console — the fixture profile does not "
      + "map a control onto them. They still carry DMX, so the light "
      + "still responds; the desk just cannot address them by name."));
  }
  const cols = ["ch", "abs", "control", "fixture label", "value", "state"];
  data.heads.forEach((h) => {
    const head = node("div", "ch-head");
    head.appendChild(node("b", null, "#" + h.head_no + "  " + h.name));
    head.appendChild(node("span", "muted small",
      "U" + h.universe + "." + h.address + "  ·  " + h.model
      + (h.mode ? " / " + h.mode : "") + "  ·  " + h.driven + "/"
      + h.footprint + " controllable"));
    body.appendChild(head);

    const grid = node("div", "ch-grid");
    cols.forEach((c) => grid.appendChild(node("span", "h", c)));
    h.channels.forEach((c) => {
      grid.appendChild(node("span", "ch-n", String(c.n)));
      grid.appendChild(node("span", "ch-abs", String(c.abs)));
      grid.appendChild(node("span", "ch-role", c.role));
      grid.appendChild(node("span", "ch-label", c.label));
      grid.appendChild(node("span", "ch-val" + (c.value ? "" : " zero"),
        c.value === null ? "—" : String(c.value)));
      grid.appendChild(node("span", "muted small",
        c.driven ? (c.programmed ? "set" : "") : "no control"));
      if (!c.driven || c.programmed) grid.classList.add(c.driven ? "live" : "dead");
    });
    body.appendChild(grid);
  });
}

/* ------------------------------------------------------------------ fan ---
 * Spread ONE value across the selection, head by head.  This is the most
 * lighting-specific thing a console does, and it was absent entirely: a
 * warm-to-cool sweep across a bar meant one action per head.
 *
 * The hint line under the control is not decoration.  A fan across a
 * mixed selection silently skipping the PARs produces a look that is wrong
 * on half the rig and says nothing, so the skipped heads are named here.
 */
/* ------------------------------------------------------- attribute grid ---
 * What the SELECTION can do, with every value visible at once - the thing
 * a console does that this panel did not.  It used to be an intensity
 * fader, a colour swatch and a free-text box in which you had to already
 * know the role was spelled `gobo_rot`.
 *
 * The value column is where the honesty lives, and it is the server's
 * answer rather than a client guess: a row shows the value, or MIXED when
 * the heads disagree (never an average), or unset.  A row only SOME heads
 * can do is starred, so a partial write is visible before it happens.
 */
let attrPage = 0;
let attrState = null;
let attrSig = null;

// The top value the PROGRAMMER uses for this attribute, which the server
// now states per row.  It used to be hardcoded to 100/255, which is right
// for a level and for a single-slot channel but wrong for a 16-bit pair -
// and the encoder happily showed values the frame merge was about to clamp
// away.
function attrMax(a) {
  if (a && typeof a.full === "number") return a.full;
  return a.level ? 100 : 255;
}

// True when the row is showing the fixture's OWN physical unit.  The value
// box then accepts "90" and sends degrees, and shows "90°" rather than the
// internal number - which is the entire reason the ranges were captured.
function attrIsPhysical(a) {
  return !!a && a.unit === "degree" && a.min !== null && a.max !== null;
}

function fmtAttrValue(a, value) {
  if (value === null || value === undefined) return "0";
  if (attrIsPhysical(a)) {
    const lo = Number(a.min);
    const hi = Number(a.max);
    const phys = lo + (Number(value) / attrMax(a)) * (hi - lo);
    return (Math.round(phys * 10) / 10) + "°";
  }
  return String(value);
}

function renderAttributes() {
  const list = $("#attr-list");
  const tabs = $("#attr-tabs");
  const note = $("#attr-note");
  if (!list || !tabs) return;
  const heads = Array.isArray(S.selected) ? S.selected : [];
  if (!heads.length) {
    attrState = null;
    attrSig = null;
    tabs.replaceChildren();
    note.textContent = "";
    note.classList.remove("warn");
    const empty = node("div", "attrempty",
      "select heads — this lists what they can do");
    list.replaceChildren(empty);
    return;
  }
  const sig = heads.join(",");
  if (sig === attrSig && attrState) {
    // Same heads: the STRUCTURE cannot have changed, but the VALUES have -
    // the operator is dragging one of them right now.  So repaint from the
    // hot feed rather than refetching.  Caching on the selection alone
    // (which was the first attempt) freezes the numbers.
    paintAttrValues();
    return;
  }
  attrSig = sig;
  api("/api/console/attributes").then((data) => {
    attrState = data;
    if (attrPage >= data.pages.length) attrPage = 0;
    drawAttrTabs();
    drawAttrRows();
  }).catch((err) => {
    list.replaceChildren(node("div", "attrempty", "attributes: " + err.message));
  });
}

/* Repaint the value cells from the programmer the hot feed already
   carries.  Local, so a value moves the instant the operator sets it and
   the grid still costs one round trip per selection, not per tick. */
function paintAttrValues() {
  if (!attrState) return;
  const values = ((S.programmer || {}).values) || {};
  const heads = (S.selected || []).map(String);
  document.querySelectorAll("#attr-list .attrrow").forEach((row) => {
    const role = row.dataset.role;
    if (!role) return;
    const seen = new Set();
    let any = false;
    for (const h of heads) {
      const v = (values[h] || {})[role];
      if (v !== undefined) { seen.add(Number(v)); any = true; }
    }
    const mixed = seen.size > 1;
    const val = row.querySelector(".attrval");
    const fill = row.querySelector(".attrfill");
    const a = (attrState
      && ((attrState.pages || [])[attrPage] || {}).attrs
      && ((attrState.pages || [])[attrPage].attrs || []).find(
        (x) => x.role === role)) || null;
    const max = a ? attrMax(a) : (row.dataset.level === "1" ? 100 : 255);
    row.dataset.full = String(max);
    row.classList.toggle("set", any);
    row.classList.toggle("mixedrow", mixed);
    if (val) {
      if (mixed) {
        val.classList.add("mixed");
        val.value = "";
        val.placeholder = "mixed";
      } else {
        val.classList.remove("mixed");
        val.placeholder = "";
        val.value = any ? fmtAttrValue(a, [...seen][0]) : "0";
      }
    }
    if (fill) {
      const shown = mixed ? 0 : (any ? [...seen][0] : 0);
      fill.style.width = (100 * shown / max).toFixed(1) + "%";
    }
  });
}

function drawAttrTabs() {
  const tabs = $("#attr-tabs");
  tabs.replaceChildren();
  (attrState.pages || []).forEach((p, i) => {
    const b = document.createElement("button");
    b.className = "attrtab" + (i === attrPage ? " on" : "");
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", i === attrPage ? "true" : "false");
    b.textContent = p.page;
    const c = document.createElement("span");
    c.className = "cnt";
    c.textContent = String(p.attrs.length);
    b.appendChild(c);
    b.onclick = () => { attrPage = i; drawAttrTabs(); drawAttrRows(); };
    tabs.appendChild(b);
  });
}

function drawAttrRows() {
  const list = $("#attr-list");
  const note = $("#attr-note");
  list.replaceChildren();
  const page = (attrState.pages || [])[attrPage];
  if (!page) {
    list.appendChild(node("div", "attrempty", "nothing on this page"));
    return;
  }
  const partial = (attrState.partial || []).length;
  note.textContent = partial
    ? attrState.heads + " heads · " + partial + " attribute(s) marked * are "
      + "not on every head — setting one is a partial write"
    : attrState.heads + " heads · every attribute here is on all of them";
  note.classList.toggle("warn", partial > 0);

  page.attrs.forEach((a) => {
    const row = document.createElement("div");
    row.className = "attrrow"
      + (a.set ? " set" : "")
      + (a.mixed ? " mixedrow" : "")
      + (a.partial ? " partial" : "");
    row.title = a.role + " — on " + a.heads + " of " + attrState.heads
      + " head(s)" + (a.partial ? "; setting it leaves the rest alone" : "");

    const name = document.createElement("span");
    name.className = "attrname";
    name.textContent = a.role;
    row.dataset.role = a.role;
    row.dataset.level = a.level ? "1" : "0";
    row.dataset.full = String(attrMax(a));
    if (a.mixed_range) {
      row.title += "; these heads travel " + a.mixed_range
        .map(([lo, hi]) => lo + ".." + hi + "°").join(" and ")
        + " — there is no single number for the lot";
    } else if (attrIsPhysical(a)) {
      row.title += "; " + a.min + ".." + a.max + "° of travel";
    }
    row.appendChild(name);

    const track = document.createElement("div");
    track.className = "attrtrack";
    const fill = document.createElement("div");
    fill.className = "attrfill";
    const shown = a.mixed ? 0 : (a.value || 0);
    fill.style.width = (100 * shown / attrMax(a)).toFixed(1) + "%";
    track.appendChild(fill);
    // Drag sets live, on release - the same discipline as the faders, so a
    // 20 Hz preview does not turn one gesture into 200 round trips.
    attachAttrDrag(track, a, attrMax(a));
    row.appendChild(track);

    const val = document.createElement("input");
    const physical = attrIsPhysical(a);
    // A degrees box is text, because the value it holds ends in a degree
    // sign and `type=number` would refuse the character.  Everything else
    // stays a number so it keeps its spinners and its own range checking.
    val.type = physical ? "text" : "number";
    val.className = "attrval" + (a.mixed ? " mixed" : "")
      + (physical ? " phys" : "");
    if (physical) {
      // The box shows the ANGLE and the limits are the light's travel,
      // so the browser's own validation is meaningful: typing 400 into a
      // 270-degree pan is out of range before anything is sent.
      val.min = String(Math.min(a.min, a.max));
      val.max = String(Math.max(a.min, a.max));
      val.step = "0.5";
      row.dataset.physical = "1";
    } else {
      val.min = "0";
      val.max = String(attrMax(a));
      row.dataset.physical = "0";
    }
    if (a.mixed) {
      val.value = "";
      val.placeholder = "mixed";
    } else {
      val.value = fmtAttrValue(a, a.value === null ? 0 : a.value);
    }
    val.title = physical
      ? a.role + " in degrees — this head travels " + a.min + ".."
        + a.max + "°"
      : a.role + ", 0.." + attrMax(a);
    val.setAttribute("aria-label", a.role + " value");
    val.onchange = () => {
      const typed = String(val.value).trim().replace(/[^\d.+-]/g, "");
      if (typed === "") { drawAttrRows(); return; }
      const num = Number(typed);
      if (Number.isNaN(num)) { drawAttrRows(); return; }
      commitAttr(a, num, row, physical ? "degree" : null);
    };
    val.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); val.blur(); }
      if (e.key === "Escape") { e.preventDefault(); drawAttrRows(); }
    };
    row.appendChild(val);

    const clr = document.createElement("button");
    clr.className = "attrclr";
    clr.textContent = "×";
    clr.title = "clear " + a.role + " from the selection";
    clr.onclick = () => {
      S.selected.forEach((h) => {
        const row2 = ((S.programmer || {}).values || {})[String(h)];
        if (row2) delete row2[a.role];
      });
      doAction("set_attr_range", { attribute: a.role, value: 0 })
        .then(() => { attrSig = null; renderAttributes(); });
    };
    row.appendChild(clr);
    list.appendChild(row);
  });
}

function commitAttr(a, value, row, unit) {
  const params = { attribute: a.role, value: value };
  if (unit) params.unit = unit;
  doAction("set_attr_range", params)
    .then((r) => {
      attrSig = null;
      if (r && r.partial) {
        showErr(r.summary, "attr");
        const n = $("#attr-note");
        if (n) { n.textContent = r.summary; n.classList.add("warn"); }
      } else {
        renderAttributes();
      }
    });
}

function attachAttrDrag(track, a, max) {
  // One sender per row, so two rows dragged at once cannot interleave.
  const push = liveSender(
    (value) => doAction("set_attr_range",
      { attribute: a.role, value: value }, { quiet: true }));
  const send = (clientX) => {
    const box = track.getBoundingClientRect();
    if (!box.width) return;
    const frac = Math.max(0, Math.min(1, (clientX - box.left) / box.width));
    const value = Math.round(frac * max);
    const fill = track.querySelector(".attrfill");
    if (fill) fill.style.width = (frac * 100).toFixed(1) + "%";
    push.push(value);
  };
  track.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* older */ }
    send(e.clientX);
    const move = (ev) => send(ev.clientX);
    const up = () => {
      track.removeEventListener("pointermove", move);
      track.removeEventListener("pointerup", up);
      track.removeEventListener("pointercancel", up);
      // Release MUST flush: a trailing debounce sends nothing during a
      // drag, so the value the operator let go at would be dropped.
      push.flush();
      attrSig = null;
      renderAttributes();
    };
    track.addEventListener("pointermove", move);
    track.addEventListener("pointerup", up);
    track.addEventListener("pointercancel", up);
  });
  track.addEventListener("dblclick", () => commitAttr(a, 0, track));
}

/* ------------------------------------------------------- fixture editor ---
 * Closes the loop the channel sheet opened.  The sheet says a channel is
 * `raw` - it carries DMX, the light responds, and the desk has no control
 * behind it.  This is where that gets fixed, and where a brand with no
 * GDTF on the Share gets a profile at all.
 *
 * The dropdown offers only ASSIGNABLE roles: `raw` is the state being
 * fixed, and offering it as a choice would make "this channel does
 * nothing" a normal, clickable answer rather than something you have to
 * mean.
 */
let fxProfile = null;

function fxSetLabel(modeId, channel, label) {
  return api("/api/fixtures/channel", {
    mode_id: modeId, channel: channel, label: label,
  }).then((data) => {
    if (data.error) {
      showErr("channel edit: " + data.error);
      return;
    }
    showErr(data.summary, "fxedit", "info");
    if (data.remapped && data.remapped.heads) {
      const n = $("#fx-count");
      if (n) {
        n.textContent = "re-mapped " + data.remapped.changed
          + " patched head(s)";
      }
    }
    // Reload so the row shows the role the new label ACTUALLY resolves
    // to, which is not necessarily the one asked for - and that gap is
    // exactly what the dropdown exists to close.
    return fxLoadProfile(fxProfile.id, true);
  }).catch((err) => showErr("channel edit: " + err.message));
}

function fxSetRange(modeId, channel, text) {
  // Empty clears the travel.  A single field rather than min and max
  // because the pair is always written and read together, and two boxes
  // invite half-typed values that mean nothing.
  const raw = String(text || "").trim();
  const parts = raw ? raw.replace("..", "\x00").split("\x00") : [];
  if (parts.length === 1) {
    showErr("travel should look like -117..117", "fxedit");
    return;
  }
  let lo = null, hi = null;
  if (parts.length === 2) {
    lo = parseFloat(parts[0]);
    hi = parseFloat(parts[1]);
    if (isNaN(lo) || isNaN(hi)) {
      showErr("travel should look like -117..117", "fxedit");
      return;
    }
  }
  api("/api/fixtures/range", {
    mode_id: modeId, channel: channel, min: lo, max: hi,
  }).then((data) => {
    if (data.error) { showErr("travel: " + data.error); return; }
    showErr(data.summary, "fxedit", "info");
    return fxLoadProfile(fxProfile.id, true);
  }).catch((err) => showErr("travel: " + err.message));
}

function fxDrawChannels(mode) {
  const host = $("#fx-channels");
  host.replaceChildren();
  if (!mode || !mode.channels || !mode.channels.length) {
    host.appendChild(node("div", "attrempty",
      "this mode has no channel list - it is a stub from a bad import"));
    return;
  }
  ["ch", "label", "control", "now", "travel"].forEach((c) =>
    host.appendChild(node("span", "h", c)));
  const assignable = fxProfile.assignable || [];
  const roleLabel = fxProfile.role_label || {};
  mode.channels.forEach((label, i) => {
    const info = (mode.roles || [])[i] || { role: "raw", controllable: false };
    // `raw` and `unused` are both "no control", but they mean opposite
    // things: `raw` is a label we do not understand and the operator may
    // want to fix it, while `unused` is a channel the fixture genuinely
    // does nothing with.  Painting them the same would nag about the
    // second and train the operator to ignore the first.
    const kind = info.controllable ? "ok" : (info.role === "unused" ? "none" : "raw");
    const row = document.createElement("div");
    row.className = "fx-ch-row " + kind;
    row.style.display = "contents";
    row.title = kind === "raw"
      ? "no control maps to this channel — pick one, or type what the light calls it"
      : (kind === "none" ? "this channel does nothing on the fixture"
        : "drives " + info.role);
    row.appendChild(node("span", "fx-ch-n", String(i + 1)));

    const lab = document.createElement("input");
    lab.type = "text";
    lab.className = "fx-ch-label";
    lab.value = label;
    lab.title = "the vendor's name for this channel";
    lab.onchange = () => fxSetLabel(mode.id, i + 1, lab.value.trim());
    row.appendChild(lab);

    const sel = document.createElement("select");
    sel.className = "cselect";
    sel.setAttribute("aria-label", "control for channel " + (i + 1));
    const keep = document.createElement("option");
    keep.value = "";
    keep.textContent = "— none —";
    sel.appendChild(keep);
    assignable.forEach((r) => {
      const o = document.createElement("option");
      o.value = r;
      o.textContent = r;
      sel.appendChild(o);
    });
    sel.value = info.controllable ? info.role : "";
    // Choosing a role writes the LABEL that produces it, because role is
    // derived from the label everywhere - one source of truth.  The label
    // box and this dropdown are the same operation from two ends.
    sel.onchange = () => fxSetLabel(mode.id, i + 1, sel.value
      ? (roleLabel[sel.value] || sel.value)
      : lab.value.trim());
    row.appendChild(sel);

    row.appendChild(node("span", "fx-ch-role", info.role));

    // The physical travel, when the profile has one.  This is the box
    // that turns "Tilt" into "aims 117 degrees up and 117 down", and it
    // is empty for a colour channel whose 0..1 is implied.
    const rng = document.createElement("input");
    rng.type = "text";
    rng.className = "fx-ch-span";
    const hasSpan = info.min !== null && info.min !== undefined
      && info.max !== null && info.max !== undefined;
    rng.value = hasSpan ? (info.min + ".." + info.max) : "";
    rng.placeholder = "0..1";
    rng.title = hasSpan
      ? "how far this channel actually travels"
      : "no travel declared — the desk will assume 0..1";
    rng.setAttribute("aria-label",
      "travel of channel " + (i + 1) + " in physical units");
    rng.onchange = () => fxSetRange(mode.id, i + 1, rng.value);
    rng.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); rng.blur(); }
      if (e.key === "Escape") { e.preventDefault(); fxLoadProfile(fxProfile.id, true); }
    };
    row.appendChild(rng);
    host.appendChild(row);
  });
}

function fxLoadProfile(id, keepMode) {
  return api("/api/fixtures/profile?id=" + id).then((data) => {
    if (data.error) { showErr("profile: " + data.error); return; }
    fxProfile = data;
    const head = $("#fx-head");
    if (head) {
      head.textContent = data.manufacturer + " " + data.model
        + (data.source ? "  [" + data.source + "]" : "");
    }
    const sel = $("#fx-mode");
    const wanted = keepMode && sel ? sel.value : null;
    sel.replaceChildren();
    (data.modes || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = m.name;
      o.textContent = m.name + " — " + m.channels.length + "ch"
        + (m.uncontrollable ? ", " + m.uncontrollable + " unmapped" : "");
      sel.appendChild(o);
    });
    const pick = (data.modes || []).find((m) => m.name === wanted)
      || (data.modes || [])[0];
    if (pick) {
      sel.value = pick.name;
      fxDrawChannels(pick);
      const n = $("#fx-count");
      if (n) {
        n.textContent = pick.uncontrollable
          ? pick.uncontrollable + " of " + pick.channels.length
            + " channels have no control"
          : "all " + pick.channels.length + " channels controllable";
      }
    }
  }).catch((err) => showErr("profile: " + err.message));
}

function fxOpenEditor() {
  if (!dlgFixture || !dlgFixture.id) {
    showErr("pick a fixture from the library first");
    return;
  }
  const ed = $("#fx-editor");
  const nw = $("#fx-new");
  if (nw) nw.classList.add("hidden");
  if (ed) ed.classList.remove("hidden");
  fxLoadProfile(dlgFixture.id).catch(() => {});
}

function fxOpenNew() {
  const ed = $("#fx-editor");
  const nw = $("#fx-new");
  if (ed) ed.classList.add("hidden");
  if (nw) nw.classList.remove("hidden");
  const chans = $("#fxn-chans");
  if (chans && !chans.value) chans.value = "";
  fxPreviewChannels();
  const m = $("#fxn-model");
  if (m) m.focus();
}

const FXN_EXAMPLES = {
  par: "Dimmer\nRed\nGreen\nBlue",
  mover: "Pan = -270..270\nTilt = -117..117\nDimmer\nShutter\n"
    + "Color Wheel\nGobo 1\nGobo 1 Rotate\nZoom\nPrism",
};

function fxPreviewChannels() {
  const box = $("#fxn-chans");
  const out = $("#fxn-preview");
  if (!box || !out) return;
  const lines = box.value.split("\n").map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) {
    out.textContent = "";
    out.classList.remove("warn");
    return;
  }
  const roles = lines.map((l) => l.split("=")[0].trim());
  const raw = roles.filter((l) => !/pan|tilt|dimmer|red|green|blue|white|amber|uv|wheel|gobo|shutter|strobe|zoom|focus|iris|frost|prism|speed|macro|zone/i
    .test(l));
  const spanned = lines.filter((l) => l.includes("=")).length;
  const bad = lines.filter((l) => {
    if (!l.includes("=")) return false;
    const p = l.split("=")[1].replace("..", "\x00").split("\x00");
    return p.length !== 2 || isNaN(parseFloat(p[0])) || isNaN(parseFloat(p[1]));
  });
  out.textContent = roles.length + " channel(s)"
    + (spanned ? " · " + spanned + " with travel" : "")
    + (raw.length
      ? " · " + raw.length + " will not map to a control: "
        + raw.slice(0, 3).join(", ") + (raw.length > 3 ? "…" : "")
      : " · all will map to a control")
    + (bad.length
      ? " · " + bad.length + " travel value(s) are not two numbers" : "");
  out.classList.toggle("warn", raw.length > 0 || bad.length > 0);
}

function wireFixtureEditor() {
  const openEd = $("#btn-editprofile");
  if (openEd) openEd.onclick = fxOpenEditor;
  const openNew = $("#btn-newprofile");
  if (openNew) openNew.onclick = fxOpenNew;
  const closeEd = $("#fx-close");
  if (closeEd) closeEd.onclick = () =>
    $("#fx-editor").classList.add("hidden");
  const closeNew = $("#fxn-close");
  if (closeNew) closeNew.onclick = () =>
    $("#fx-new").classList.add("hidden");
  const mode = $("#fx-mode");
  if (mode) {
    mode.onchange = () => {
      const m = ((fxProfile || {}).modes || [])
        .find((x) => x.name === mode.value);
      if (m) fxDrawChannels(m);
    };
  }
  const chans = $("#fxn-chans");
  if (chans) chans.addEventListener("input", fxPreviewChannels);
  const ex1 = $("#fxn-example"), ex2 = $("#fxn-example2");
  if (ex1) ex1.onclick = () => {
    $("#fxn-chans").value = FXN_EXAMPLES.par;
    $("#fxn-model").value = "LED PAR 4ch";
    fxPreviewChannels();
  };
  if (ex2) ex2.onclick = () => {
    $("#fxn-chans").value = FXN_EXAMPLES.mover;
    $("#fxn-model").value = "Moving Head";
    fxPreviewChannels();
  };
  const create = $("#fxn-create");
  if (create) create.onclick = () => {
    const channels = $("#fxn-chans").value.split("\n")
      .map((l) => l.trim()).filter(Boolean);
    api("/api/fixtures/create", {
      manufacturer: $("#fxn-man").value.trim(),
      model: $("#fxn-model").value.trim(),
      mode: $("#fxn-mode").value.trim() || "Default",
      channels: channels,
    }).then((data) => {
      if (data.error) { showErr("new profile: " + data.error); return; }
      showErr(data.summary, "fxnew", "info");
      $("#fx-new").classList.add("hidden");
      $("#fxn-chans").value = "";
      return loadLibrary();
    }).catch((err) => showErr("new profile: " + err.message));
  };
}

/* ------------------------------------------------------- command line -----
 * A text field over ONE action.  The parser is in the engine, not here,
 * so the console, the HTTP API and the agent all mean the same thing by
 * `1-4 pan 90` - a second JavaScript parser would disagree about exactly
 * the ambiguous cases, and the disagreement would be invisible until
 * something acted on the wrong heads.
 *
 * Three details that make it feel like a console rather than a form:
 *
 * * the RESULT is spoken back, not just accepted.  `pan = 170 on 2 of 2
 *   head(s) = 90°` tells the operator the number that went out AND the
 *   angle it means; a silent box tells them nothing and gets typed into
 *   again;
 * * UP/DOWN walk the history, because nobody remembers last week's line;
 * * the engine refuses trailing junk ("unexpected 'x'"), so the field can
 *   never be the reason the wrong thing happens.
 */
const CMD_HISTORY_MAX = 80;
let cmdHistory = [];
let cmdHistAt = -1;
let cmdSuggest = null;

function cmdOut(kind, html) {
  const out = $("#cmd-out");
  if (!out) return;
  out.className = "cmdout" + (kind ? " " + kind : "");
  out.replaceChildren();
  if (typeof html === "string") {
    out.appendChild(document.createTextNode(html));
  } else if (html) {
    out.appendChild(html);
  }
}

function cmdEcho(line) {
  const span = document.createElement("span");
  span.className = "cmdecho";
  span.textContent = "> " + line + "   ";
  return span;
}

async function cmdRun() {
  const box = $("#cmd");
  if (!box) return;
  const line = box.value.trim();
  if (!line) return;
  cmdHistory = cmdHistory.filter((h) => h !== line);
  cmdHistory.push(line);
  if (cmdHistory.length > CMD_HISTORY_MAX) cmdHistory.shift();
  cmdHistAt = cmdHistory.length;
  const r = await doAction("run_command", { text: line }, { quiet: true });
  box.value = "";
  hideSuggest();
  const help = $("#cmd-help");
  if (help) help.hidden = true;
  const out = $("#cmd-out");
  if (!r || r.ok === false) {
    // The error goes in the SAME line as the echo, so a failed command
    // leaves one readable sentence rather than a red line and a separate
    // message elsewhere on the screen.
    if (out) {
      out.replaceChildren(cmdEcho(line));
      out.className = "cmdout err";
      out.appendChild(document.createTextNode(
        (r && r.error) ? String(r.error) : "rejected"));
    }
    return;
  }
  if (r.help) {
    // `?` opens the syntax IN PLACE rather than in a modal, so the command
    // line stays usable while reading it.
    if (help) {
      help.textContent = r.help;
      help.hidden = false;
    }
    if (out) {
      out.replaceChildren(cmdEcho(line));
      out.className = "cmdout";
      out.appendChild(document.createTextNode("command syntax"));
    }
    // Remembered so the help overlay can show the same text rather than a
    // second, hand-written copy that drifts.
    S.cmd_help = r.help;
    return;
  }
  if (out) {
    out.replaceChildren(cmdEcho(line));
    out.className = "cmdout ok";
    const said = (r.transcript || []).concat(r.notes || []);
    out.appendChild(document.createTextNode(
      said.length ? said.join("   ·   ") : (r.summary || "ok")));
  }
  // A line can change the patch, the selection, the programmer and a cue at
  // once, so the honest thing is a full reload - and the command line is
  // typed, not dragged, so the cost is invisible.
  await loadState();
  lookSeq = -1;
  if (r.selection) S.selected = r.selection;
  renderSelection();
  renderPills();
  if (r.undo_label) renderUndo(r.undo);
}

/* --------------------------------------------------- inline suggestions --
 * Built from the ALREADY-PARSED vocabularies in the page where possible,
 * but the verb list comes from the engine's own help text, so the two
 * cannot drift apart into offering a command that does not exist.
 */
function cmdSuggestList(line) {
  const words = line.split(/\s+/);
  const last = (words[words.length - 1] || "").toLowerCase();
  const prior = words.slice(0, -1).filter(Boolean);
  const out = [];
  const push = (text, why) => out.push({ text: text, why: why });

  if (!prior.length) {
    // First word: the verbs, plus the selection shapes.  Taken from the
    // page's own copy of the verb list, and `?` is always available as the
    // escape hatch if one is missing.
    (Array.isArray(S.cmd_verbs) ? S.cmd_verbs
      : ["go", "back", "record", "clear", "home", "master", "blackout",
         "palette", "preset", "cue"]).forEach((v) => push(v, "command"));
    const top = (S.patch || []).map((h) => h.head_no);
    const nums = top.length
      ? [top[0], top[top.length - 1]]
      : [1, 4];
    push(String(nums[0]) + "-" + nums[1], "select a range");
    push("all", "select every patched head");
    push("none", "deselect");
    if ((S.groups || []).length) {
      push("group " + (S.groups[0] || {}).n, "select a group");
    }
    push("?", "all the syntax");
  } else if (prior.length === 1 && /^[\d.]/.test(prior[0])) {
    // After a selection: the roles the SELECTION ACTUALLY HAS.  Offering
    // `iris` for five PAR cans is noise; deriving the list from the
    // selection's own maps is the difference between a command line that
    // helps and one that has to be memorised.
    const heads = (S.selected || []).map(Number);
    const have = [];
    (S.patch || []).forEach((h) => {
      if (!heads.includes(h.head_no)) return;
      (h.map || []).forEach((r) => {
        if (r !== "raw" && r !== "unused" && !have.includes(r)) have.push(r);
      });
    });
    have.sort();
    if (!have.length) {
      // The typed range may not be the selection yet - suggest the verbs
      // instead of an empty box.
      ["clear", "go"].forEach((v) => push(v, "command"));
    }
    have.forEach((r) => push(r, "attribute"));
    ["off", "full", "+10", "-10"].forEach((v) => push(v, "value"));
  } else if (prior.length === 1 && prior[0] === "palette") {
    (S.palette_kinds || ["beam", "colour", "position"]).forEach(
      (k) => push(k, "palette family"));
  } else {
    // Deeper in a line: complete the numbers, which is the common case
    // (`cue 1` -> `cue 12`).
    (S.cue_numbers || []).forEach((n) => push(String(n), "cue"));
    ["on", "off", "full"].forEach((v) => push(v, "value"));
  }
  return { items: out.filter((o) => !last
    || o.text.toLowerCase().startsWith(last)) };
}

function showSuggest(items) {
  const box = $("#cmd-suggest");
  if (!box) return;
  if (!items || !items.length) { hideSuggest(); return; }
  box.replaceChildren();
  items.slice(0, 40).forEach((it, i) => {
    const row = document.createElement("div");
    if (i === 0) row.className = "on";
    const b = document.createElement("b");
    b.textContent = it.text;
    row.appendChild(b);
    const w = document.createElement("span");
    w.className = "muted small";
    w.textContent = "  " + it.why;
    row.appendChild(w);
    row.onmousedown = (e) => {
      e.preventDefault();
      const input = $("#cmd");
      const words = input.value.split(/\s+/);
      words[words.length - 1] = it.text;
      input.value = words.join(" ") + (it.text.endsWith(" ") ? "" : " ");
      input.focus();
      hideSuggest();
    };
    box.appendChild(row);
  });
  box.hidden = false;
}

function hideSuggest() {
  const box = $("#cmd-suggest");
  if (box) box.hidden = true;
}

/* ------------------------------------------------------------- arrange ---
 * ALIGN puts every selected head on ONE line, at the mean.  DISTRIBUTE
 * spaces them EVENLY between the two outermost.  MIRROR reflects the
 * shape about a centre.
 *
 * The distinction is the feature, not an implementation detail: a row of
 * six with the middle two bunched is not misaligned, so align is the
 * wrong tool and distribute is the right one.  Every console has all
 * three as separate commands for that reason.
 *
 * Each button is DISABLED below two heads rather than firing and reporting
 * "0 heads moved" - a button that is a no-op teaches people the button is
 * broken, and the keyboard command is still there for the one-head case.
 */
function arrAxis() {
  const sel = $("#arr-axis");
  return sel ? sel.value : "x";
}

function arrHint(text, warn) {
  const el = $("#arr-hint");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("warn", !!warn);
}

async function arrRun(action, extra) {
  const heads = (S.selected || []).map(Number);
  if (heads.length < 2) {
    arrHint("select 2 or more heads", true);
    return null;
  }
  const params = Object.assign({ heads: heads, axis: arrAxis() },
                               extra || {});
  arrHint("working…");
  const r = await doAction(action, params);
  if (!r || r.ok === false) {
    arrHint((r && r.error) || (action + " failed"), true);
    return r;
  }
  // A no-op is reported as a no-op, not dressed up as a move: align on an
  // already-aligned selection moved nothing and saying "aligned 6 heads"
  // would be a lie the operator has to check.
  const n = r.heads || 0;
  arrHint(r.summary || (n ? action + " moved " + n + " head(s)"
                          : action + " changed nothing"),
          !n);
  await loadState();
  return r;
}

async function aimRun() {
  const panBox = $("#aim-pan");
  const tiltBox = $("#aim-tilt");
  const pan = String((panBox && panBox.value) || "").trim();
  const tilt = String((tiltBox && tiltBox.value) || "").trim();
  if (pan === "" && tilt === "") {
    arrHint("type a pan or tilt value first", true);
    return;
  }
  const heads = (S.selected || []).map(Number);
  if (!heads.length) {
    arrHint("nothing selected", true);
    return;
  }
  const num = (t) => {
    const v = parseFloat(t);
    return isNaN(v) ? null : v;
  };
  const p = pan === "" ? null : num(pan);
  const t = tilt === "" ? null : num(tilt);
  if ((pan !== "" && p === null) || (tilt !== "" && t === null)) {
    arrHint("that is not a number", true);
    return;
  }
  const params = {};
  if (p !== null) params.pan = p;
  if (t !== null) params.tilt = t;
  // No `unit` is sent.  The server decides degrees-vs-logical from the
  // FIXTURE, and a client-side guess would be exactly the silent
  // reinterpretation §17.19 exists to prevent - and the hint would be
  // wrong for a light with no GDTF, where the same number means something
  // else entirely.  The reply says which reading was used.
  const r = await doAction("set_position", params);
  if (!r || r.ok === false) {
    arrHint((r && r.error) || "aim failed", true);
    return;
  }
  if (r.aimed === false) {
    arrHint(r.summary || "nothing to aim", true);
    return;
  }
  arrHint(r.summary || ("aimed " + r.heads + " head(s)"), r.partial);
  if (panBox) panBox.value = "";
  if (tiltBox) tiltBox.value = "";
  await loadState();
  lookSeq = -1;
}

/* ------------------------------------------------------- patch sheet -----
 * THE DOCUMENT THE RIG ACTUALLY HAS.  Until this existed, the answer to
 * "give me a patch sheet" was "go and do it by hand in some other
 * program" - which is how patch sheets get typed wrong.
 *
 * It is generated client-side from the LIVE `lite` feed rather than
 * fetched, for one reason: no route to fail.  Every other download in this
 * console can 404 if a file is moved; this cannot, because the bytes come
 * from the state already on screen.
 *
 * The CSV is BUILT HERE and not in the engine, which is a deliberate
 * exception to the "parser lives in one place" rule that governs the
 * command line.  The command line changes what the desk DOES, so two
 * parsers would fire the wrong thing; a CSV formatter changes nothing and
 * is only ever used to write a file the operator is looking at.  The
 * engine also has `patch_csv` - used by the HTTP API and the agent - and
 * the two are checked against each other in the test suite so they cannot
 * drift.
 */
const PATCH_CSV_COLUMNS = [
  "Head", "Name", "Manufacturer", "Model", "Mode", "Fixture",
  "Universe", "Address", "Footprint", "End", "Channels", "Type",
  "Position X", "Position Y", "Position Z", "Kind", "Curve",
  "Unpatched", "Roles", "Source",
];

// RFC 4180: a comma, a quote or a newline forces quoting, and a quote
// inside becomes two.  A fixture named `Foo, Bar "Special"` must not open
// with the wrong number of columns in every spreadsheet ever written.
function csvCell(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n\r]/.test(text)
    ? '"' + text.replace(/"/g, '""') + '"' : text;
}

function csvNum(value) {
  const n = Number(value);
  if (!isFinite(n)) return "0";
  return String(Math.round(n * 100) / 100);
}

function patchCsv(heads) {
  const wanted = heads && heads.length
    ? new Set(heads.map(Number)) : null;
  const rows = (S.patch || []).filter(
    (h) => !wanted || wanted.has(h.head_no));
  const out = [PATCH_CSV_COLUMNS.join(",")];
  rows.forEach((h) => {
    const map = h.map || [];
    const fp = Math.max(1, map.length);
    const addr = Number(h.address) || 1;
    const kind = h.kind || (Number(h.y) >= 2 ? "truss" : "floor");
    const roles = map.filter(
      (r) => r !== "raw" && r !== "unused");
    out.push([
      h.head_no,
      h.name || ("Head " + h.head_no),
      h.manufacturer || "",
      h.model || "",
      h.mode || "",
      [h.manufacturer, h.model].filter(Boolean).join(" ").trim(),
      h.universe, addr, fp, addr + fp - 1, map.length,
      h.kind || (Number(h.y) >= 2 ? "truss" : "floor"),
      csvNum(h.x), csvNum(h.y), csvNum(h.z),
      kind, h.curve || "linear",
      map.indexOf("raw") >= 0 ? "yes" : "",
      roles.length ? roles.join(" ") : "-",
      h.model_source || "",
    ].map(csvCell).join(","));
  });
  return out.join("\r\n") + "\r\n";
}

function csvDownloadName() {
  // A filename a filesystem and a browser will both accept.  The show name
  // is the obvious choice and also the one that breaks on the first
  // character Windows or a URL will not take.
  const first = (S.patch || [])[0] || {};
  let raw = String(first.name || "").trim() || "patch";
  raw = raw.replace(/[<>:"/\\|?*]/g, "-")
           .replace(/\s+/g, " ").replace(/^[ .]+|[ .]+$/g, "");
  return (raw || "patch") + ".csv";
}

async function exportPatchCsv() {
  const btn = $("#btn-patchcsv");
  if (btn) btn.disabled = true;
  try {
    // The selection when there is one: a patch sheet of what you are
    // working on beats a sheet of 200 heads when you are patching 6.
    // Said in the filename, so the two are never confused.
    const sel = (S.selected || []).map(Number);
    const only = sel.length && sel.length < (S.patch || []).length;
    const text = patchCsv(only ? sel : null);
    // A BOM, because Excel opens a bare .csv as one column and renders
    // every non-ASCII fixture name as mojibake.  Harmless to every other
    // reader.
    const blob = new Blob(["﻿" + text],
                          { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = csvDownloadName();
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Revoking immediately can cancel the download in some browsers, so
    // it waits for the event loop rather than being done on the same tick.
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    const n = text.split("\r\n").filter(Boolean).length - 1;
    showErr(n + " head(s) exported"
            + (only ? " (the selection)" : " (the whole rig)"),
            "csv", "info");
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ------------------------------------------------------------- the lock ---
 * Three states, and the difference between the two locked ones is the
 * whole feature:
 *
 *   OPERATE   the show runs; the SHOW cannot be edited.  Faders, cues, the
 *             programmer and effects all still work, because that IS the
 *             performance.  Re-patching a universe is not.
 *   LOCKED    as OPERATE, and the patch is frozen as well - which is what
 *             you want while the rig is up and a cable is being moved.
 *
 * Clicking cycles forward, so it is one key on a show day, and the
 * confirm is only asked for the two that stop you editing.
 */
const LOCK_NEXT = { design: "operate", operate: "locked", locked: "design" };
const LOCK_WORD = { design: "DESIGN", operate: "OPERATE", locked: "LOCKED" };

function renderLock() {
  if (!S) return;
  const btn = $("#btn-lock");
  const state = S.lock || "design";
  if (btn) {
    btn.textContent = LOCK_WORD[state];
    btn.dataset.state = state;
    btn.classList.toggle("has-pass", !!S.lock_has_password);
    btn.title = (state === "design"
      ? "DESIGN: edit anything."
      : state === "operate"
        ? "OPERATE: the show runs, the show cannot be edited. "
          + "Faders, cues and the programmer still work."
        : "LOCKED: the show runs and the patch is frozen too.")
      + "\n\nClick for the next state"
      + (S.lock_has_password ? " (password protected — LOCKED must be"
        + " unlocked with it)" : "");
  }
  // The class on <body> is what dims the editing controls, so the state
  // is visible before anything is pressed rather than after.
  document.body.classList.toggle("locked", state !== "design");
  document.body.dataset.lock = state;
}

async function cycleLock() {
  const from = (S && S.lock) || "design";
  const to = LOCK_NEXT[from];
  if (to === "design") {
    // Leaving a lock is the one direction that needs no password: the
    // point of the password is to stop someone ACCIDENTALLY editing, not
    // to stop the person who set it.
    const r = await doAction("set_lock", { state: "design" });
    if (r && r.ok === false) { arrHint(r.error, true); return; }
    arrHint("DESIGN — everything is editable again", false);
  } else {
    let password = null;
    if (from === "locked" && S.lock_has_password) {
      const typed = window.prompt("Password to leave LOCKED:") || "";
      if (!typed) return;
      password = typed;
    }
    const r = password
      ? await doAction("unlock", { password: password })
      : await doAction("set_lock", { state: to });
    if (r && r.ok === false) {
      arrHint(r.error, true);
      return;
    }
    arrHint(r.summary || LOCK_WORD[to], to !== "design");
  }
  await loadState();
  renderLock();
  refreshLimits();
  // The button is repainted from the hot feed, which lands a moment after
  // this call, so nothing is forced here - doing it twice would show the
  // OLD state for one frame, which is worse than one frame of no change.
}

/* ------------------------------------------------ limits and orientation --
 * Two facts about ONE fixture, both of which look like a broken rig if
 * you cannot record them: a dimmer that never really reaches 0, and a
 * head hung with pan and tilt the wrong way round.  Both are applied in
 * the FRAME, so the programmer still holds what was typed and the encoder
 * does not start lying after a reload.
 */
function limSelectedRoles() {
  const heads = (S.selected || []).map(Number);
  const out = [];
  (S.patch || []).forEach((h) => {
    if (!heads.includes(h.head_no)) return;
    (h.map || []).forEach((r) => {
      if (r !== "raw" && r !== "unused" && !out.includes(r)) out.push(r);
    });
  });
  out.sort();
  return out;
}

function renderLimRole() {
  const sel = $("#lim-role");
  if (!sel) return;
  const want = sel.value;
  const roles = limSelectedRoles();
  sel.replaceChildren();
  const add = (v, why) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v + (why ? "  " + why : "");
    sel.appendChild(o);
  };
  if (!roles.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "— select heads first —";
    sel.appendChild(o);
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  // dimmer first: it is the one with a floor on almost every fixture.
  if (roles.includes("dimmer")) add("dimmer");
  roles.forEach((r) => { if (r !== "dimmer") add(r); });
  if (want && roles.includes(want)) sel.value = want;
}

function refreshLimits() {
  if (!S) return;
  renderLimRole();
  const heads = (S.selected || []).map(Number);
  const list = $("#lim-list");
  const note = $("#lim-note");
  if (!list) return;
  list.replaceChildren();
  if (!heads.length) {
    if (note) note.textContent = "";
    return;
  }
  const rows = (S.patch || []).filter((h) => heads.includes(h.head_no));
  const bits = [];
  rows.forEach((h) => {
    Object.entries(h.limits || {}).forEach(([role, span]) => {
      bits.push("head " + h.head_no + " " + role + " "
                + (span[0] === null ? "-" : span[0]) + ".."
                + (span[1] === null ? "-" : span[1]));
    });
    const o = h.orient || {};
    const on = Object.keys(o).filter((k) => o[k]);
    if (on.length) {
      bits.push("head " + h.head_no + " "
                + on.map((k) => k.replace("invert_", "inv ")).join(" "));
    }
  });
  if (note) {
    note.textContent = bits.length
      ? bits.length + " set" : "";
  }
  if (bits.length) {
    const b = document.createElement("b");
    b.textContent = bits.join("   ·   ");
    list.appendChild(b);
  }
}

async function limSet() {
  const role = ($("#lim-role") || {}).value;
  if (!role) { arrHint("select some heads first", true); return; }
  const lo = String((($("#lim-lo") || {}).value) || "").trim();
  const hi = String((($("#lim-hi") || {}).value) || "").trim();
  const num = (t) => (t === "" ? null : parseFloat(t));
  const params = { role: role, low: num(lo), high: num(hi) };
  if (params.low === null && params.high === null) {
    arrHint("type a minimum, a maximum, or both", true);
    return;
  }
  if ((params.low !== null && isNaN(params.low))
      || (params.high !== null && isNaN(params.high))) {
    arrHint("that is not a number", true);
    return;
  }
  const r = await doAction("set_limits", params);
  if (!r || r.ok === false) {
    arrHint((r && r.error) || "could not set the limit", true);
    return;
  }
  arrHint(r.summary || "limit set");
  await loadState();
  refreshLimits();
}

async function limOrient(action, value) {
  const heads = (S.selected || []).map(Number);
  if (!heads.length) { arrHint("select some heads first", true); return; }
  const params = { heads: heads };
  if (action === "set_orient") {
    if (value === "clear") params.clear = true;
    else params[value] = true;
  } else {
    params.heads = heads;
  }
  const r = await doAction(action, params);
  if (!r || r.ok === false) {
    arrHint((r && r.error) || "could not set that", true);
    return;
  }
  arrHint(r.summary || "done");
  await loadState();
  refreshLimits();
  lookSeq = -1;
}

function wireLock() {
  const btn = $("#btn-lock");
  if (btn) btn.onclick = cycleLock;
  const key = document.addEventListener;
  key("keydown", (e) => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const k = (e.key || "").toLowerCase();
    if (k === "l") { e.preventDefault(); cycleLock(); }
  });
  const set = $("#btn-limits");
  if (set) set.onclick = limSet;
  const clr = $("#btn-unlimits");
  if (clr) {
    clr.onclick = () => limOrient("clear_limits", null);
  }
  [["#btn-invpan", "invert_pan"], ["#btn-invtilt", "invert_tilt"],
   ["#btn-swap", "swap"]].forEach(([sel, key2]) => {
    const b = $(sel);
    if (b) b.onclick = () => limOrient("set_orient", key2);
  });
  const un = $("#btn-unorient");
  if (un) un.onclick = () => limOrient("set_orient", "clear");
  const role = $("#lim-role");
  if (role) role.onchange = () => {
    const heads = (S.selected || []).map(Number);
    const h = (S.patch || []).find((x) => heads.includes(x.head_no));
    const cur = (h && h.limits && h.limits[role.value]) || [];
    if ($("#lim-lo")) $("#lim-lo").value = cur[0] === null || cur[0] === undefined ? "" : cur[0];
    if ($("#lim-hi")) $("#lim-hi").value = cur[1] === null || cur[1] === undefined ? "" : cur[1];
  };
}

function wireArrange() {
  const bind = (id, action, needsThree) => {
    const b = $(id);
    if (!b) return;
    b.onclick = () => {
      const n = (S.selected || []).length;
      const want = needsThree ? 3 : 2;
      if (n < want) {
        arrHint(action + " needs " + want + " or more heads, "
                + (n ? "you have " + n : "nothing is selected"), true);
        return;
      }
      arrRun(action);
    };
  };
  bind("#btn-align", "align");
  bind("#btn-distribute", "distribute", true);
  bind("#btn-mirror", "mirror");
  const at = $("#btn-mirror-at");
  if (at) {
    at.onclick = () => {
      const raw = String((($("#arr-about") || {}).value) || "").trim();
      const about = raw === "" ? null : parseFloat(raw);
      if (raw !== "" && isNaN(about)) {
        arrHint("that is not a position in metres", true);
        return;
      }
      arrRun("mirror", about === null ? {} : { about: about });
    };
  }
  const aim = $("#btn-aim");
  if (aim) aim.onclick = aimRun;
  const csv = $("#btn-patchcsv");
  if (csv) csv.onclick = exportPatchCsv;
  ["#aim-pan", "#aim-tilt"].forEach((sel) => {
    const box = $(sel);
    if (!box) return;
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); aimRun(); }
    });
  });
  // The buttons are DISABLED, not merely guarded, so a two-head selection
  // visibly cannot distribute rather than refusing on click.  Called from
  // the lite feed below, so it tracks the selection without hooking
  // renderSelection.
  refreshArrange();
}

/* Kept out of wireArrange so the hot feed can call it every tick without
 * re-reading the DOM more often than the selection actually changes. */
let arrLastN = -1;
function refreshArrange() {
  // `wireArrange` calls this at init, and at that point S is still null -
  // the first state fetch has not come back yet.  Without this the whole
  // init function throws on the last line, so nothing is wired and the
  // console comes up blank with no error anywhere.
  if (!S || !document.querySelector("#btn-align")) return;
  const n = (S.selected || []).length;
  if (n === arrLastN) return;
  arrLastN = n;
  const dis = (id, want) => {
    const b = $(id);
    if (b) b.disabled = n < want;
  };
  dis("#btn-align", 2);
  dis("#btn-mirror", 2);
  dis("#btn-mirror-at", 2);
  dis("#btn-distribute", 3);
  const aimBtn = $("#btn-aim");
  if (aimBtn) aimBtn.disabled = n < 1;
  if (n >= 2) arrHint("");
}

/* ------------------------------------------------------------ help -------
 * `?` anywhere opens this.  It is built from the DOM and the live state
 * rather than being a hand-written HTML block, because a help page that
 * has drifted from the buttons is worse than none: an operator who cannot
 * find a feature concludes the feature does not exist.
 */
const HELP_KEYMAP = [
  ["/", "focus the command line"],
  ["↑ ↓", "walk the command history"],
  ["Tab", "complete to what the selection can do"],
  ["Enter", "run the line"],
  ["Esc", "clear the line, or close this"],
  ["?", "this help"],
];

function helpRow(keys, what) {
  const row = document.createElement("div");
  row.className = "helprow";
  const k = document.createElement("span");
  k.className = "helpkeys";
  keys.split(" ").forEach((one, i) => {
    if (i) k.appendChild(document.createTextNode(" "));
    const kb = document.createElement("kbd");
    kb.textContent = one;
    k.appendChild(kb);
  });
  row.appendChild(k);
  const w = document.createElement("span");
  w.textContent = what;
  row.appendChild(w);
  return row;
}

function helpSection(title) {
  const s = document.createElement("div");
  s.className = "helpsection";
  const h = document.createElement("h4");
  h.textContent = title;
  s.appendChild(h);
  return s;
}

function helpList(items) {
  const ul = document.createElement("dl");
  items.forEach(([k, v]) => {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    ul.appendChild(dt);
    ul.appendChild(dd);
  });
  return ul;
}

function buildHelp() {
  const body = $("#help-body");
  if (!body) return;
  body.replaceChildren();
  try {
    fillHelp(body);
  } catch (err) {
    // A HELP PAGE THAT THROWS MUST NOT BECOME A MODAL THAT WILL NOT CLOSE.
    //
    // It did exactly that: the fill threw, the error propagated out of
    // `toggleHelp` BEFORE it set `hidden`, so the dialog stayed open with
    // an EMPTY card — a thin bar with a heading and nothing to read, and
    // the ✕ that would have closed it ran through the same function.
    // Blank help is bad.  Unclosable help is worse, because the way out
    // is to reload the page.
    body.replaceChildren();
    const p = document.createElement("p");
    p.className = "muted small";
    p.textContent = "The help could not be built (" + err.message + "). "
      + "Press Esc, ? or the ✕ to close it. The console itself is fine.";
    body.appendChild(p);
  }
}

function fillHelp(body) {
  const heads = (S && S.patch) ? S.patch.length : 0;
  const sel = (S && S.selected) ? S.selected.length : 0;

  let s = helpSection("where you are");
  s.appendChild(helpList([
    [heads + " head" + (heads === 1 ? "" : "s") + " patched",
     ((S && S.universes) || 0) + " universe"
     + (((S && S.universes) || 1) === 1 ? "" : "s")],
    [sel + " selected",
     sel ? "the command line and the attribute grid act on these"
          : "click heads, or type `all`"],
    [S && S.dry_run ? "DRY RUN" : ((S && S.live) ? "LIVE" : "standby"),
     S && S.dry_run ? "frames are built and shown but not sent"
                    : (S && S.live ? "Art-Net frames are going out"
                                   : "the sender is stopped — GO LIVE to start")],
  ]));
  body.appendChild(s);

  // The syntax, straight from the engine, so it cannot drift.
  s = helpSection("the command line");
  const pre = document.createElement("pre");
  pre.className = "helpsyntax";
  pre.textContent = (S && S.cmd_help
    ? S.cmd_help : "type ? in the command line for this").trim();
  s.appendChild(pre);
  body.appendChild(s);

  s = helpSection("keys");
  HELP_KEYMAP.forEach(([k, v]) => s.appendChild(helpRow(k, v)));
  body.appendChild(s);

  s = helpSection("the panels");
  s.appendChild(helpList([
    ["PATCH", "every head, with its address and footprint. Filter it, then "
      + "`select shown` to take what you can see"],
    ["GROUPS", "named selections. `group 1` in the command line selects one"],
    ["PRESETS", "whole looks. A preset is a complete set of values; a palette "
      + "is one attribute family"],
    ["PROGRAMMER", "the attribute grid, intensity, colour, fan and fx. This "
      + "is where the command line sits"],
    ["ARRANGE", "align, distribute and mirror — on a selection of 2 or more"],
    ["LIMITS", "a dimmer floor, and invert/swap for a head hung the other "
      + "way round"],
    ["CUES", "the stacks. `go`, `back`, `cue 3 go`, `cue 3 at 50`"],
  ]));
  body.appendChild(s);

  s = helpSection("three things worth knowing");
  s.appendChild(helpList([
    ["`off` removes an attribute",
     "it does not set it to zero — a zero overrides whatever is underneath, "
     + "a removal lets it show through"],
    ["a line is one undo step",
     "`1-4 pan 90` is one Ctrl+Z, and the undo button names the line"],
    ["a line is all-or-nothing",
     "if any part fails, none of it is applied — including the selection"],
    ["DESIGN / OPERATE / LOCKED",
     "OPERATE runs the show and refuses to edit it; LOCKED also freezes "
     + "the patch"],
  ]));
  body.appendChild(s);
}

function toggleHelp(force) {
  const dlg = $("#help-dialog");
  if (!dlg) return;
  // This overlay uses the `hidden` ATTRIBUTE, not the `.hidden` class the
  // older two modals use, because `.modal` sets `display:flex` and a
  // class would lose to it.  Toggling the class here meant the button and
  // `?` did nothing at all while `anyDialogOpen` - which checks the
  // attribute - reported the dialog as closed.  Two conventions, one
  // reader, both had to be spelled out.
  const open = force === undefined ? dlg.hidden === true : !!force;
  // HIDE FIRST, THEN BUILD.  Building first means anything that throws
  // leaves the dialog open with an empty card - and the close path runs
  // through this same function, so a help page that fails to build also
  // fails to close.  `buildHelp` catches its own errors now, but the order
  // is the thing that actually guarantees it.
  dlg.hidden = !open;
  if (!open) return;
  buildHelp();
  const close = $("#help-close");
  if (close) close.focus();
}

function wireHelp() {
  const close = $("#help-close");
  if (close) close.onclick = () => toggleHelp(false);
  const btn = $("#btn-help");
  if (btn) btn.onclick = () => toggleHelp();
  const dlg = $("#help-dialog");
  if (dlg) {
    dlg.addEventListener("click", (e) => {
      // Clicking the BACKDROP closes; clicking inside must not, or every
      // attempt to select a line of help text closes the dialog.
      if (e.target === dlg) toggleHelp(false);
    });
    // The dialog handles its OWN keys as well as the document's.
    //
    // Four independent routes out is not redundancy for its own sake: the
    // one time this matters is when the global handler is not reached -
    // focus inside a nested control, a browser that swallowed the key, a
    // dialog left open by a build that failed.  A modal you cannot get out
    // of is a page reload, and losing a show because of a help page is
    // not an acceptable trade.
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        e.stopPropagation();
        toggleHelp(false);
      }
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return;
    // `?` WORKS FROM A TEXT FIELD, and does not type itself.
    //
    // It used to be ignored there, which is the panel people are in when
    // they go looking for help - the command line.  So `?` silently did
    // nothing: it did not open help, and it did not close help that was
    // already open, and the key read as broken.  `preventDefault` is what
    // stops the character being typed; without it Shift+/ in a field
    // would put a `?` in the middle of a command.
    const tag = (document.activeElement || {}).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      e.preventDefault();
    }
    toggleHelp();
  });
}

function wireCommandLine() {
  const box = $("#cmd");
  if (!box) return;
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      cmdRun();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      box.value = "";
      hideSuggest();
      return;
    }
    if (e.key === "ArrowUp") {
      if (!cmdHistory.length) return;
      e.preventDefault();
      cmdHistAt = Math.max(0, cmdHistAt - 1);
      box.value = cmdHistory[cmdHistAt] || "";
      box.setSelectionRange(box.value.length, box.value.length);
      return;
    }
    if (e.key === "ArrowDown") {
      if (!cmdHistory.length) return;
      e.preventDefault();
      cmdHistAt = Math.min(cmdHistory.length, cmdHistAt + 1);
      box.value = cmdHistory[cmdHistAt] || "";
      return;
    }
    if (e.key === "Tab") {
      // Tab completes to the first match, which is the only thing a
      // keyboard user needs and the only thing that cannot be wrong in a
      // way they would not notice.
      const box2 = $("#cmd-suggest");
      const first = box2 && !box2.hidden ? box2.querySelector("div") : null;
      if (first) {
        e.preventDefault();
        const words = box.value.split(/\s+/);
        words[words.length - 1] = first.querySelector("b").textContent;
        box.value = words.join(" ") + " ";
        hideSuggest();
      }
      return;
    }
  });
  box.addEventListener("input", () => {
    const res = cmdSuggestList(box.value);
    showSuggest(res.items);
  });
  box.addEventListener("blur", () => window.setTimeout(hideSuggest, 120));
  // "/" focuses the command line from anywhere, the way it does on a real
  // console - but NOT while a dialog is open or the operator is typing
  // somewhere else.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey) return;
    const tag = (document.activeElement || {}).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (typeof anyDialogOpen === "function" && anyDialogOpen()) return;
    e.preventDefault();
    box.focus();
    box.select();
  });
}

function wireFan() {
  const btn = $("#btn-fan");
  const hint = $("#fan-hint");
  const show = (msg, warn) => {
    if (!hint) return;
    hint.textContent = msg || "";
    hint.classList.toggle("warn", !!warn);
  };
  if (btn) btn.onclick = async () => {
    const attr = ($("#fan-attr") || {}).value || "";
    const lo = Number(($("#fan-lo") || {}).value || 0);
    const hi = Number(($("#fan-hi") || {}).value || 0);
    const mode = ($("#fan-mode") || {}).value || "normal";
    const by = ($("#fan-by") || {}).value || "order";
    show("fanning…");
    const r = await doAction("fan", {
      attribute: attr.trim(), low: lo, high: hi, mode: mode, by: by,
    });
    if (!r || r.ok === false) {
      show((r && r.error) || "fan failed", true);
      return;
    }
    const first = r.first || {}, last = r.last || {};
    let msg = (r.summary || "") + " — head " + first.head + " at "
      + first.value + ", head " + last.head + " at " + last.value;
    if (r.skipped && r.skipped.length) {
      msg += ". Heads " + r.skipped.join(", ")
        + " have no " + r.attribute + " channel and were left alone.";
      show(msg, true);
    } else {
      show(msg);
    }
  };
}

function wirePatchFilter() {
  const box = $("#head-filter");
  if (box) {
    // Local and instant: no debounce, because it never leaves the
    // browser.  It only re-renders rows, and re-filtering on the 10 Hz
    // feed would fight the operator's typing.
    box.addEventListener("input", () => {
      if (S) { rebuildHeadList(S.patch || []); renderPatch(); }
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        $("#btn-filtersel").click();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        box.value = "";
        if (S) { rebuildHeadList(S.patch || []); renderPatch(); }
      }
    });
  }
  const sel = $("#btn-filtersel");
  if (sel) sel.onclick = (e) => {
    const heads = visibleHeads().map((h) => h.head_no);
    if (!heads.length) {
      showErr("no heads match the filter");
      return;
    }
    doAction("select_heads", { heads: heads, add: !!(e && e.shiftKey) });
  };
}

function wireChannels() {
  const btn = $("#btn-channels");
  if (btn) btn.onclick = openChannels;
  const close = $("#ch-close");
  if (close) close.onclick = closeChannels;
  const dlg = $("#ch-dialog");
  if (dlg) {
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg) closeChannels();
    });
  }
  const refresh = $("#ch-refresh");
  if (refresh) refresh.onclick = loadChannels;
}

/* ---------------------------------------------------------------- undo ---
 * The two buttons name the edit they will reverse, and grey out when
 * there is nothing.  Both halves matter: an always-live button teaches
 * the operator that undo is unreliable, and they stop using it.
 */
function renderUndo(st) {
  const u = (st && st.undo) || {};
  const undo = $("#btn-undo"), redo = $("#btn-redo");
  if (undo) {
    undo.disabled = !u.can_undo;
    undo.textContent = u.can_undo ? "↶ " + undoLabel(u.undo) : "↶ undo";
    undo.title = u.can_undo
      ? "undo: " + u.undo.replace(/_/g, " ") + "  (Ctrl+Z)"
      : "nothing to undo";
  }
  if (redo) {
    redo.disabled = !u.can_redo;
    redo.textContent = u.can_redo ? "↷ " + undoLabel(u.redo) : "↷ redo";
    redo.title = u.can_redo
      ? "redo: " + u.redo.replace(/_/g, " ") + "  (Ctrl+Shift+Z)"
      : "nothing to redo";
  }
}

function undoLabel(action) {
  if (!action) return "";
  const words = action.replace(/_/g, " ");
  return words.length > 12 ? words.slice(0, 11) + "\u2026" : words;
}

async function doUndo(redo) {
  const res = await doAction(redo ? "redo" : "undo", {});
  if (res && res.ok === false) showErr(res.error || "undo failed");
}

function wireUndo() {
  const undo = $("#btn-undo"), redo = $("#btn-redo");
  if (undo) undo.onclick = () => doUndo(false);
  if (redo) redo.onclick = () => doUndo(true);
}

function fixtureModes(r) {
  const modes = r.modes || [];
  if (!modes.length) return "no mode list";
  return modes.map((m) => m.channel_count + "ch").join(" / ")
    + " · " + modes.length + " mode(s)";
}

/* --------------------------------------------------------- GDTF Share ---
 * Browse and download official fixture profiles from inside the add-heads
 * dialog, so "I have a light this app has never heard of" is answered
 * without leaving the console or hunting for a .gdtf file.
 *
 * Two rules this follows, both learned the hard way:
 *   - an ERROR is never drawn as an empty list.  "no fixtures published"
 *     and "not signed in" and "the site is down" are different problems
 *     with different fixes, and an empty list reads as the first one.
 *   - downloading is not browsing.  It is a real action on the shared
 *     library, so it gets its own tab and its own button, and it reports
 *     back when it SUPERSEDED an existing profile - because that changes
 *     the channel map on every head already patched with it.
 */
let shareSt = null;
let shareResults = [];
let shareBusy = false;

function setShareStatus(text) {
  const el = $("#share-status");
  if (el) el.textContent = text || "";
}

function sharePane(on) {
  const lib = $("#dlg-pane-lib"), sh = $("#dlg-pane-share");
  if (lib) lib.classList.toggle("hidden", !!on);
  if (sh) sh.classList.toggle("hidden", !on);
  [["#tab-installed", !on], ["#tab-share", !!on]].forEach(([sel, isOn]) => {
    const t = $(sel);
    if (!t) return;
    t.classList.toggle("on", isOn);
    t.setAttribute("aria-selected", isOn ? "true" : "false");
  });
  if (on) {
    shareStatus();
    const q = $("#share-q");
    if (q) q.focus();
  }
}

function shareStatus() {
  return api("/api/gdtf/status").then((st) => {
    shareSt = st;
    const inPane = $("#share-signedin");
    const browse = $("#share-browse");
    if (inPane) inPane.classList.toggle("hidden", !!st.signed_in);
    if (browse) browse.classList.toggle("hidden", !st.signed_in);

    if (st.last_error) setShareStatus("· " + st.last_error);
    else if (st.signed_in) {
      setShareStatus(st.catalogue
        ? "· " + st.catalogue.toLocaleString() + " in catalogue"
        : "· signed in");
    } else if (st.configured) setShareStatus("· account configured");
    else setShareStatus("");

    // The footprint filter is built from what the catalogue actually
    // offers, so the operator can ask for "a 4-channel PAR" and get one
    // rather than an empty list of guesses.  The guard is "> 1", not
    // "> 0": the select ships with an "any footprint" placeholder, so
    // checking for emptiness never fired and the filter stayed unusable.
    const fp = $("#share-fp");
    if (fp && st.catalogue && fp.options.length <= 1) {
      [3, 4, 5, 7, 8, 9, 10, 12, 14, 16, 18, 20, 24, 28, 32].forEach((n) => {
        const o = document.createElement("option");
        o.value = String(n);
        o.textContent = n + " ch";
        fp.appendChild(o);
      });
    }
    return st;
  }).catch((err) => {
    setShareStatus("· " + err.message);
    shareSt = { signed_in: false, last_error: err.message };
  });
}

function shareQuery() {
  const q = $("#share-q").value.trim();
  const fp = $("#share-fp").value;
  return "/api/gdtf/search?limit=60&q=" + encodeURIComponent(q)
    + "&footprint=" + encodeURIComponent(fp);
}

function shareRender() {
  const box = $("#share-list");
  if (!box) return;
  box.replaceChildren();
  if (!shareResults.length) {
    const d = document.createElement("div");
    d.className = "empty small";
    d.textContent = shareBusy ? "searching gdtf-share.com…"
      : (shareSt && shareSt.last_error
        ? shareSt.last_error
        : "type to search 10,000+ published fixtures");
    box.appendChild(d);
    return;
  }
  const frag = document.createDocumentFragment();
  shareResults.forEach((r) => {
    const b = document.createElement("button");
    b.className = "lib-item";
    const nm = document.createElement("span");
    nm.className = "lib-name";
    nm.textContent = r.manufacturer + " " + r.fixture;
    const meta = document.createElement("span");
    meta.className = "lib-meta";
    meta.textContent = (r.modes || []).map((m) => m.dmxfootprint + "ch").join("/");
    const dl = document.createElement("span");
    dl.className = "lib-dl";
    dl.textContent = "get";
    dl.onclick = (ev) => { ev.stopPropagation(); shareDownload(r, dl); };
    b.append(nm, meta, dl);
    frag.appendChild(b);
  });
  box.appendChild(frag);
}

function shareSearch() {
  if (shareBusy) return;
  const st = shareSt;
  if (st && st.signed_in === false && st.configured === false) {
    shareRender();
    return;
  }
  shareBusy = true;
  shareRender();
  api(shareQuery()).then((data) => {
    if (data.error) {
      showErr("GDTF Share: " + data.error);
      shareResults = [];
    }
    // Deliberately NOT assigned to shareSt: a search reply carries
    // results, not session fields, so overwriting the status with it
    // silently reported "not signed in" and hid the sign-in form the
    // operator was looking at.
    shareResults = data.results || [];
  }).catch((err) => {
    showErr("GDTF Share: " + err.message);
    shareResults = [];
  }).then(() => {
    shareBusy = false;
    shareRender();
    shareStatus();
  });
}

function shareDownload(r, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  api("/api/gdtf/download", { rid: r.rid }).then((data) => {
    if (data.error) {
      showErr("GDTF Share: " + data.error);
      btn.disabled = false;
      btn.textContent = "get";
      return;
    }
    // Superseding a profile changes the channel map of every head already
    // patched with it, so it is never a silent success.
    showErr("GDTF Share: " + (data.summary || "downloaded"), "share-ok", "info");
    if (data.superseded && data.superseded.length) {
      showSticky("share-superseded",
        "GDTF Share: that replaced " + data.superseded.join(", ")
        + " — heads already patched with it now use the new channel map");
    }
    // Land the operator where they were trying to go: the fixture is now
    // installed, so show the installed list with it selected.
    return loadLibrary().then(() => {
      sharePane(false);
      const found = library.find((x) =>
        (x.manufacturer || "") === data.manufacturer
        && (x.model || "") === data.model);
      if (found) {
        const row = document.querySelector(
          '#dlg-lib-list .lib-item[data-fixture="' + found.id + '"]');
        pickFixture(found, row);
      }
    });
  }).catch((err) => {
    showErr("GDTF Share: " + err.message);
    btn.disabled = false;
    btn.textContent = "get";
  });
}

function shareLogin() {
  const user = $("#share-user").value.trim();
  const pass = $("#share-pass").value;
  if (!user || !pass) {
    showErr("GDTF Share: enter your email and password");
    return;
  }
  showErr("GDTF Share: signing in…", "share-in", "info");
  api("/api/gdtf/login", { user: user, password: pass }).then((data) => {
    if (data.error) {
      showErr("GDTF Share: " + data.error);
      return;
    }
    $("#share-pass").value = "";        // the browser forgets it at once
    $("#share-signedin").classList.add("hidden");
    $("#share-browse").classList.remove("hidden");
    setShareStatus("· signed in");
    shareSearch();
  }).catch((err) => showErr("GDTF Share: " + err.message));
}

function wireShare() {
  const t1 = $("#tab-installed"), t2 = $("#tab-share");
  if (t1) t1.onclick = () => sharePane(false);
  if (t2) t2.onclick = () => sharePane(true);
  const q = $("#share-q");
  if (q) {
    // Debounced: this hits the network, and the Share catalogue is
    // 13,000 entries - unlike the installed library, which filters
    // locally and needs no delay at all.
    let timer = null;
    q.addEventListener("input", () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(shareSearch, 320);
    });
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (timer) window.clearTimeout(timer);
        shareSearch();
      }
    });
  }
  const fp = $("#share-fp");
  if (fp) fp.onchange = shareSearch;
  const rf = $("#share-refresh");
  if (rf) rf.onclick = () => {
    const st = shareSt;
    if (st && !st.signed_in) return;
    const url = "/api/gdtf/search?limit=60&refresh=1&q="
      + encodeURIComponent(($("#share-q") || {}).value || "");
    shareBusy = true;
    shareRender();
    api(url).then((d) => { shareResults = d.results || []; })
      .catch((err) => showErr("GDTF Share: " + err.message))
      .then(() => { shareBusy = false; shareRender(); });
  };
  const lo = $("#share-login");
  if (lo) lo.onclick = shareLogin;
  const pass = $("#share-pass");
  if (pass) {
    pass.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); shareLogin(); }
    });
  }
}

function loadLibrary() {
  const box = $("#dlg-lib-list");
  if (!box) return Promise.resolve();
  const wait = document.createElement("div");
  wait.className = "muted small";
  wait.textContent = "loading…";
  box.replaceChildren(wait);
  return api("/api/fixtures?q=").then((data) => {
    library = data.results || [];
    libraryLoaded = true;
    const count = $("#dlg-lib-count");
    if (count) count.textContent = library.length + " types";
    renderLibrary();
  }).catch((err) => {
    box.replaceChildren();
    showErr("fixtures: " + err.message);
  });
}

function renderLibrary() {
  const box = $("#dlg-lib-list");
  if (!box) return;
  const q = $("#dlg-search").value.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  const hits = words.length
    ? library.filter((r) => {
      const hay = (fixtureLabel(r) + " " + (r.source || "")).toLowerCase();
      return words.every((w) => hay.indexOf(w) >= 0);
    })
    : library;
  const count = $("#dlg-lib-count");
  if (count) {
    count.textContent = words.length
      ? hits.length + " of " + library.length
      : library.length + " types";
  }
  box.replaceChildren();
  if (!hits.length) {
    const d = document.createElement("div");
    d.className = "empty small";
    d.textContent = libraryLoaded
      ? "nothing matches — try fewer words"
      : "no fixtures in the database yet (import GDTF files first)";
    box.appendChild(d);
    return;
  }
  const frag = document.createDocumentFragment();
  hits.forEach((r) => {
    const b = document.createElement("button");
    b.className = "lib-item";
    b.dataset.fixture = String(r.id);
    const nm = document.createElement("span");
    nm.className = "lib-name";
    nm.textContent = fixtureLabel(r);
    const meta = document.createElement("span");
    meta.className = "lib-meta";
    const modes = r.modes || [];
    meta.textContent = modes.length
      ? modes.map((m) => m.channel_count).join("/") + "ch"
      : "—";
    b.append(nm, meta);
    b.onclick = () => pickFixture(r, b, box);
    frag.appendChild(b);
  });
  box.appendChild(frag);
}

function runSearch() {
  // The side list IS the result list; filtering is local and instant.
  renderLibrary();
}

function pickFixture(r, btn) {
  dlgFixture = r;
  document.querySelectorAll("#dlg-lib-list .lib-item.on")
    .forEach((e) => e.classList.remove("on"));
  if (btn) btn.classList.add("on");
  $("#dlg-pick").classList.remove("hidden");
  $("#dlg-name").textContent = fixtureLabel(r);
  const modes = r.modes || [];
  $("#dlg-meta").textContent = [
    r.id ? "#" + r.id : "",
    (modes.length || "no") + " DMX mode(s)",
    fixtureModes(r),
    r.source ? "from " + r.source : "",
  ].filter(Boolean).join(" · ");
  const sel = $("#dlg-mode");
  sel.replaceChildren();
  sel.disabled = !modes.length;
  if (!modes.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "server default";
    sel.appendChild(o);
  } else {
    modes.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.name;
      o.textContent = m.name + " (" + m.channel_count + "ch)";
      sel.appendChild(o);
    });
  }
  $("#dlg-add").disabled = false;
  dlgErr(null);
  $("#dlg-qty").focus();
}

function optNum(sel) {
  const raw = $(sel).value.trim();
  if (!raw) return { ok: true };            // left empty → server decides
  const n = Number(raw);
  if (!Number.isFinite(n)) return { ok: false };
  return { ok: true, v: n };
}

async function addFromDialog() {
  if (!dlgFixture) return;
  const qty = Number($("#dlg-qty").value);
  if (!Number.isInteger(qty) || qty < 1 || qty > 64) {
    dlgErr("qty must be a whole number 1–64");
    return;
  }
  const params = { qty: qty };
  if (dlgFixture.id) params.fixture_id = dlgFixture.id;
  else {
    params.query = ((dlgFixture.manufacturer || "") + " " +
      (dlgFixture.model || "")).trim();
  }
  const mode = $("#dlg-mode").value;
  if (mode) params.mode = mode;

  const u = optNum("#dlg-u"), a = optNum("#dlg-a");
  if (!u.ok || !a.ok || (u.v !== undefined && u.v < 1) || (a.v !== undefined && a.v < 1)) {
    dlgErr("universe and address must be ≥ 1 (or empty to auto-address)");
    return;
  }
  if (u.v !== undefined) params.universe = Math.round(u.v);
  if (a.v !== undefined) params.address = Math.round(a.v);

  const xs = ["x", "y", "z"];
  for (const k of xs) {
    const r = optNum("#dlg-pos-" + k);
    if (!r.ok) { dlgErr(k + " must be a number (metres)"); return; }
    if (r.v !== undefined) params[k] = r.v;
  }

  dlgErr(null);
  const r = await doAction("add_heads", params, { quiet: true, broadcast: "patch" });
  if (r.ok === false) {
    dlgErr(r.error || "add_heads failed");
    return;
  }
  showErr("added " + qty + " × " +
    ((dlgFixture.manufacturer || "") + " " + (dlgFixture.model || "")).trim() +
    (mode ? " · " + mode : ""), "info");
  closeDialog();
}

function wireDialog() {
  $("#dlg-close").onclick = closeDialog;
  $("#add-dialog").addEventListener("click", (e) => {
    if (e.target.id === "add-dialog") closeDialog();
  });
  // Escape is handled once, globally, in wireKeyboard.
  // The filter is local and instant - no debounce needed, and typing
  // feels immediate.  The server call in runSearch only tops the list up.
  $("#dlg-search").addEventListener("input", runSearch);
  $("#dlg-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const first = $("#dlg-lib-list .lib-item");
      if (first) first.click();
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const first = $("#dlg-lib-list .lib-item");
      if (first) first.click();
    }
  });
  $("#dlg-add").onclick = addFromDialog;
}

/* ------------------------------------------------------------------ init */

function init() {
  buildSwatches();
  wireHeader();
  wirePatchTools();
  wireProgrammer();
  wirePad();
  wirePicker();
  wireDialog();
  wireShare();
  wireChannels();
  wireUndo();
  wirePresets();
  wireFan();
  wireArrange();
  wireLock();
  wireHelp();
  wireCommandLine();
  wirePatchFilter();
  wireFixtureEditor();
  wireScan();
  wireCamBar();
  wireKeyboard();
  annotateKeys();
  $("#err-dismiss").onclick = () => { stickyErrs.clear(); hideErr(); };
  wireAI();
  loadAutoStatus();          // one-shot: autosave indicator in the footer
  $("#standalone-note").classList.toggle("hidden", !!window.opener);
  paintPad();
  loadState();
  setInterval(feed, 100);
  // Light at ~20 Hz: the engine already interpolates the fade at output
  // rate, this is what the eye sees between feed ticks.  A hidden tab
  // polls far less often - rAF is throttled there anyway and nobody is
  // looking at the beams.
  lookTimer = setInterval(() => {
    if (document.hidden) return;
    lookFeed();
  }, 50);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { lookSeq = -1; lookFeed(); }
  });
}

init();
