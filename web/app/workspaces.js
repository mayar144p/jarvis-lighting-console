// The three modes on screen: Set up, Program, Live.  One frame for all of
// them - the lights on the left, the work in the middle, the master rail
// on the right - and each mode shows what its job needs (wslayouts.js says
// what).  What a mode was left showing - the dock's tab, the dock over the
// whole work area - and the side panel's width are kept per computer (each
// operator's own), not in the show.  F1 / F2 / F3 or the switch in the
// title bar change mode.
import { $, $$, h, toast, choiceBox } from "./ui.js";
import { clean, frameFor, MIN_W, MAX_W, MODES, MODE_NAMES } from "./wslayouts.js";
import { renderHub } from "./modehub.js";
import { state, on } from "./store.js";
import { HAS_3D } from "./view3d.js";

const KEY = "haze.modes";

let saved = clean({});
let applying = false;

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "null");
    if (raw) { saved = clean(raw); return; }
    // a desk from before the three modes kept "workspaces": the mode it was
    // left in carries over, the rest starts as the desk comes
    const old = JSON.parse(localStorage.getItem("haze.workspaces") || "null");
    const was = old && Array.isArray(old.list) && old.list.find((w) => w && w.id === old.active);
    if (was) saved = clean({ mode: was.mode });
    localStorage.removeItem("haze.workspaces");
  } catch (e) { /* private window or a damaged entry: how the desk comes */ }
}
function store() {
  try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* private window */ }
}

/** The mode on screen, as {id, name, mode} (the tools and the tour ask). */
export const activeWorkspace = () => ({ id: saved.mode, name: MODE_NAMES[saved.mode], mode: saved.mode });

const ws = () => $(".workspace");
const px = (name) => parseInt(ws().style.getPropertyValue(name), 10) || 0;

// the screen as it is now, kept for the mode in use
function remember() {
  if (applying) return;
  const b = document.body, m = saved.mode;
  const sel = $("#prog-tabs button[aria-selected=\"true\"]");
  const next = clean({ ...saved, sideW: px("--side-w"), workW: px("--work-w"), tab: sel ? sel.dataset.tab : saved.tab });
  if (m !== "setup") next.by[m].bottom = b.dataset.bottom || next.by[m].bottom;
  saved = clean(next);
  store();
}

function paintModes() {
  for (const mb of $$("#mode-seg button")) {
    const lit = mb.dataset.mode === saved.mode;
    mb.classList.toggle("on", lit);
    mb.setAttribute("aria-pressed", String(lit));
  }
  const mx = $("#dock-max");
  if (mx) {
    const f = frameFor(saved);
    mx.hidden = f.mode !== "program";
    mx.classList.toggle("on", f.max);
    mx.setAttribute("aria-pressed", String(f.max));
    mx.title = f.max ? "Give the programmer its room back" : "The dock over the whole work area (the programmer hides)";
  }
}

function apply() {
  const f = frameFor(saved);
  applying = true;
  try {
    const b = document.body, el = ws();
    // the job: Set up has its own screen where the programmer is, Live has the dock
    b.dataset.mode = f.mode;
    b.classList.toggle("ws-nostage", !HAS_3D);
    b.classList.toggle("dock-max", f.max);
    // (Set up has no side panel: its patch table lists the lights)
    if (saved.sideW && f.mode !== "setup") el.style.setProperty("--side-w", saved.sideW + "px"); else el.style.removeProperty("--side-w");
    if (saved.workW) el.style.setProperty("--work-w", saved.workW + "px"); else el.style.removeProperty("--work-w");
    const dockTab = f.dock && $(`#pb-mode button[data-mode="${f.bottom}"]`);
    if (dockTab) dockTab.click();
    const tab = saved.tab && f.programmer && $(`#prog-tabs button[data-tab="${saved.tab}"]`);
    if (tab && !tab.hidden && tab.getAttribute("aria-selected") !== "true") tab.click();
  } finally {
    applying = false;
  }
  paintModes();
  renderHub(f.mode);
  import("./welcome.js").then((m) => m.offerModeTour(f.mode)).catch(() => {});
  window.dispatchEvent(new Event("resize"));      // what is drawn to fit (the 3D, the timeline) fits its new room
}

/** Switch to a mode: Set up, Program or Live, as it was left. */
export function useMode(m) {
  if (!MODES.includes(m)) return false;
  const was = saved.mode;
  remember();                                     // the one we leave keeps how it was
  saved.mode = m;
  store();
  apply();
  if (m === "live" && was !== "live") offerLock();
  return true;
}

/** The modes by their older names and places (the checks and saved
 *  shortcuts use them): "programming" is Program, 1..3 are the modes in order. */
export function useWorkspace(which) {
  const m = typeof which === "number" ? MODES[which - 1] : which === "programming" ? "program" : which;
  return useMode(m);
}

/** Program: the dock over the whole work area, or back under the programmer. */
export function setDockMax(on) {
  if (saved.mode !== "program") return;
  remember();
  saved.by.program.max = !!on;
  store();
  apply();
}

// Into Live with the show still open for editing: offer the lock, once a
// session (a stray drag on a busy night can't move a cue or a light).
// "Don't ask again" is kept on this computer.
let lockAsked = false;
async function offerLock() {
  const s = state.snap || {};
  if (lockAsked || (s.lock || "design") !== "design" || document.documentElement.dataset.window === "stage") return;
  try { if (localStorage.getItem("haze.noLockAsk") === "1") return; } catch (e) { /* private window */ }
  lockAsked = true;
  const i = await choiceBox("Lock the show for tonight?", {
    message: "Live: the show can still be edited. Lock it so a stray tap can't change a cue, a palette or the patch?",
    detail: "Operate: play and busk with the programmer. Playing only: GO, faders, buttons and blackout. The lock button at the top unlocks it.",
    buttons: ["Not now", "Don't ask again", "Playing only", "Operate"], primary: 3, cancel: 0,
  });
  if (i === 1) { try { localStorage.setItem("haze.noLockAsk", "1"); } catch (e) { /* private window */ } }
  if (i === 2 || i === 3) {
    const { run } = await import("./actions.js");
    run("set_lock", { state: i === 3 ? "operate" : "locked" }, { toast: true });
  }
}

// Drag a panel's inner edge to make it wider or narrower; double-click
// puts it back.  `sign`: +1 when dragging right widens it (the side panel),
// -1 when dragging left does (the work area beside the 3D).
function wireGrip(panel, cssVar, sign) {
  const grip = h("div.ws-grip", { role: "separator", "aria-orientation": "vertical", title: "Drag to resize - double-click for the normal width" });
  panel.append(grip);
  let x0 = 0, w0 = 0, held = false;
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    held = true;
    x0 = e.clientX;
    w0 = panel.getBoundingClientRect().width;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("on");
  });
  grip.addEventListener("pointermove", (e) => {
    if (!held) return;
    const w = Math.max(MIN_W, Math.min(MAX_W, Math.round(w0 + sign * (e.clientX - x0))));
    ws().style.setProperty(cssVar, w + "px");
    window.dispatchEvent(new Event("resize"));
  });
  const end = () => { if (held) { held = false; grip.classList.remove("on"); remember(); } };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
  grip.addEventListener("dblclick", () => {
    ws().style.removeProperty(cssVar);
    remember();
    window.dispatchEvent(new Event("resize"));
  });
}

export function initWorkspaces() {
  if (document.documentElement.dataset.window) return;   // a second window keeps its own fixed layout
  load();
  wireGrip($("#fixtures-panel"), "--side-w", 1);
  if (HAS_3D) wireGrip($("#work"), "--work-w", -1);
  for (const mb of $$("#mode-seg button")) mb.addEventListener("click", () => useMode(mb.dataset.mode));
  const mx = $("#dock-max");
  if (mx) mx.addEventListener("click", () => setDockMax(!frameFor(saved).max));
  // a new, empty show starts where it needs to: Set up (once per visit)
  let checked = false;
  on("snapshot", () => {
    if (checked || !state.snap) return;
    checked = true;
    if (!(state.snap.patch || []).length && saved.mode !== "setup") {
      useMode("setup");
      toast(`${MODE_NAMES.setup}: no lights yet - add them here, then Program`);
    }
  });
  // whatever changes the screen is kept for the mode in use
  const later = () => setTimeout(remember, 0);
  for (const sel of ["#pb-mode", "#prog-tabs"]) { const el = $(sel); if (el) el.addEventListener("click", later); }
  apply();
}
