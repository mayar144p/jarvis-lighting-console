// Workspaces (backlog A10 item 6): the screen arranged for the job - the
// fixture list and the programmer shown or not, which side each is on,
// how wide, the bottom dock (faders / buttons / timeline) and its height,
// and the programmer's tab.  Four ready ones and your own; each one keeps
// the screen as you left it.  Switch from the top bar or with Alt+1..9.
// Kept per computer (each operator's own), not in the show.
import { $, $$, h, menu, toast, promptBox, confirmBox } from "./ui.js";
import { BUILTIN, clean, MIN_W, MAX_W } from "./wslayouts.js";

const KEY = "jarvis.workspaces";

let saved = { active: "programming", list: [] };
let applying = false;

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "null");
    if (raw && Array.isArray(raw.list)) saved = { active: String(raw.active || "programming"), list: raw.list.filter((w) => w && w.id).map(clean) };
  } catch (e) { /* private window or a damaged entry: the ready ones */ }
  for (const b of BUILTIN) if (!saved.list.some((w) => w.id === b.id)) saved.list.splice(BUILTIN.indexOf(b), 0, { ...b });
  if (!saved.list.some((w) => w.id === saved.active)) saved.active = "programming";
}
function store() {
  try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* private window */ }
}

export const workspaces = () => saved.list.map((w) => ({ ...w }));
export const activeWorkspace = () => saved.list.find((w) => w.id === saved.active) || saved.list[0];

const ws = () => $(".workspace");
const dockPx = () => parseInt(ws().style.getPropertyValue("--pb-h"), 10) || 0;

// the screen as it is now
function capture(into) {
  const b = document.body;
  const sel = $("#prog-tabs button[aria-selected=\"true\"]");
  Object.assign(into, clean({
    ...into,
    fix: !b.classList.contains("ws-nofix"), prog: !b.classList.contains("ws-noprog"), swap: b.classList.contains("ws-swap"),
    fixW: parseInt(ws().style.getPropertyValue("--fixtures-w"), 10) || 0,
    progW: parseInt(ws().style.getPropertyValue("--prog-w"), 10) || 0,
    bottom: b.dataset.bottom || "faders", dock: dockPx(), tab: sel ? sel.dataset.tab : "",
  }));
}
function remember() {
  if (applying) return;
  const w = activeWorkspace();
  if (!w) return;
  capture(w);
  store();
  paintButton();
}

function apply(w) {
  applying = true;
  try {
    const b = document.body, el = ws();
    b.classList.toggle("ws-nofix", !w.fix);
    b.classList.toggle("ws-noprog", !w.prog);
    b.classList.toggle("ws-swap", !!w.swap);
    if (w.fixW) el.style.setProperty("--fixtures-w", w.fixW + "px"); else el.style.removeProperty("--fixtures-w");
    if (w.progW) el.style.setProperty("--prog-w", w.progW + "px"); else el.style.removeProperty("--prog-w");
    // the dock's height lives per mode (playbacks.js reads it when the mode is picked)
    try {
      if (w.dock) localStorage.setItem("jarvis.dockh." + w.bottom, String(w.dock));
      else localStorage.removeItem("jarvis.dockh." + w.bottom);
    } catch (e) { /* private window */ }
    const mode = $(`#pb-mode button[data-mode="${w.bottom}"]`);
    if (mode) mode.click();
    const tab = w.tab && $(`#prog-tabs button[data-tab="${w.tab}"]`);
    if (tab && w.prog) tab.click();
  } finally {
    applying = false;
  }
  paintButton();
  window.dispatchEvent(new Event("resize"));      // the 3D view fits its new space
}

/** Switch to a workspace by id or by its place (1..9). */
export function useWorkspace(which) {
  const w = typeof which === "number" ? saved.list[which - 1] : saved.list.find((x) => x.id === which);
  if (!w) return false;
  remember();                                     // the one we leave keeps how it was
  saved.active = w.id;
  store();
  apply(w);
  toast(`Workspace: ${w.name}`);
  return true;
}

function toggle(field) {
  const w = activeWorkspace();
  capture(w);
  w[field] = !w[field];
  if (!w.fix && !w.prog && field !== "swap") toast("The 3D view and the dock fill the screen");
  apply(w);
  store();
}

async function saveAs() {
  const name = await promptBox("New workspace", "Name", "", { ok: "Save", placeholder: "e.g. Festival busking" });
  if (!name || !name.trim()) return;
  const w = clean({ id: "u" + Date.now().toString(36), name: name.trim() });
  capture(w);
  w.name = name.trim().slice(0, 40);
  saved.list.push(w);
  saved.active = w.id;
  store();
  paintButton();
  toast(`Saved "${w.name}" - Alt+${Math.min(saved.list.length, 9)} brings it back`);
}

async function rename(w) {
  const name = await promptBox("Rename workspace", "Name", w.name, { ok: "Rename" });
  if (!name || !name.trim()) return;
  w.name = name.trim().slice(0, 40);
  store();
  paintButton();
}

async function remove(w) {
  if (!(await confirmBox("Delete workspace", `Delete "${w.name}"? The screen stays as it is.`, { ok: "Delete", danger: true }))) return;
  saved.list = saved.list.filter((x) => x !== w);
  if (saved.active === w.id) saved.active = saved.list[0].id;
  store();
  paintButton();
}

function reset(w) {
  const b = BUILTIN.find((x) => x.id === w.id);
  if (!b) return;
  Object.assign(w, clean(b));
  store();
  if (saved.active === w.id) apply(w);
  toast(`${w.name}: back to how it came`);
}

function openMenu(anchor) {
  remember();
  const cur = activeWorkspace();
  const builtin = BUILTIN.some((b) => b.id === cur.id);
  menu(anchor, [
    ...saved.list.map((w, i) => ({
      label: (w.id === cur.id ? "✓ " : "") + w.name,
      hint: i < 9 ? `Alt+${i + 1}` : "",
      run: () => useWorkspace(w.id),
    })),
    "-",
    { label: (cur.fix ? "✓ " : "") + "Fixture list", hint: "show or hide", run: () => toggle("fix") },
    { label: (cur.prog ? "✓ " : "") + "Programmer", hint: "show or hide", run: () => toggle("prog") },
    { label: "Swap sides", hint: cur.swap ? "programmer on the left now" : "programmer to the left", run: () => toggle("swap") },
    { label: "Widths back to normal", hint: "or double-click a panel's edge", disabled: !cur.fixW && !cur.progW, run: () => { cur.fixW = cur.progW = 0; apply(cur); store(); } },
    "-",
    { label: "Save as a new workspace…", run: saveAs },
    { label: `Rename "${cur.name}"…`, run: () => rename(cur) },
    builtin
      ? { label: `Reset "${cur.name}"`, hint: "how it came", run: () => reset(cur) }
      : { label: `Delete "${cur.name}"…`, danger: true, disabled: saved.list.length <= 1, run: () => remove(cur) },
  ]);
}

function paintButton() {
  const b = $("#ws-btn");
  if (!b) return;
  const w = activeWorkspace();
  $("#ws-name").textContent = w ? w.name : "";
  b.title = `Workspace: ${w ? w.name : ""} - the screen arranged for the job (Alt+1..9 to switch)`;
}

// Drag a side panel's inner edge to make it wider or narrower; double-click
// puts it back.
function wireGrip(panel, cssVar, field) {
  const grip = h("div.ws-grip", { role: "separator", "aria-orientation": "vertical", title: "Drag to resize - double-click for the normal width" });
  panel.append(grip);
  let x0 = 0, w0 = 0, on = false;
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    on = true;
    x0 = e.clientX;
    w0 = panel.getBoundingClientRect().width;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("on");
  });
  grip.addEventListener("pointermove", (e) => {
    if (!on) return;
    // the edge that faces the 3D view: dragging toward it widens the panel
    const r = panel.getBoundingClientRect(), stage = $("#stage-wrap").getBoundingClientRect();
    const sign = r.left < stage.left ? 1 : -1;
    const w = Math.max(MIN_W, Math.min(MAX_W, Math.round(w0 + sign * (e.clientX - x0))));
    ws().style.setProperty(cssVar, w + "px");
    window.dispatchEvent(new Event("resize"));
  });
  const end = () => { if (on) { on = false; grip.classList.remove("on"); remember(); } };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
  grip.addEventListener("dblclick", () => {
    ws().style.removeProperty(cssVar);
    const w = activeWorkspace();
    w[field] = 0;
    store();
    window.dispatchEvent(new Event("resize"));
  });
}

export function initWorkspaces() {
  if (document.documentElement.dataset.window) return;   // a second window keeps its own fixed layout
  load();
  const btn = $("#ws-btn");
  if (btn) btn.addEventListener("click", () => openMenu(btn));
  wireGrip($("#fixtures-panel"), "--fixtures-w", "fixW");
  wireGrip($("#programmer"), "--prog-w", "progW");
  // whatever changes the screen is kept in the workspace in use
  const later = () => setTimeout(remember, 0);
  for (const sel of ["#pb-mode", "#prog-tabs"]) { const el = $(sel); if (el) el.addEventListener("click", later); }
  const grip = $("#pb-grip");
  if (grip) { grip.addEventListener("pointerup", later); grip.addEventListener("dblclick", later); }
  document.addEventListener("keydown", (e) => {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    const m = /^Digit([1-9])$/.exec(e.code);
    if (!m || $$(".modal").length) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    e.preventDefault();
    useWorkspace(Number(m[1]));
  });
  apply(activeWorkspace());
}
