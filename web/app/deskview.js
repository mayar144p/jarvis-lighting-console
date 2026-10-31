// The desk view (Programmer -> Palettes), after a pro desk's palette
// windows: Group | Position over Colour | Beam, each a grid of tiles - pick
// lights in Group, touch a palette and the selection takes it.  Beside
// them the command strip: selection, FLIP / MIRROR, record, the windows,
// and a keypad for the command line (1 THRU 4 @ FULL ENTER).
import { state, on, selected, patch } from "./store.js";
import { run, select } from "./actions.js";
import { $, h, menu, toast, promptBox, confirmBox } from "./ui.js";
import { goboIcon } from "./goboicon.js";
import { icon } from "./icons.js";

const KINDS = [["position", "Position", "P"], ["colour", "Colour", "C"], ["beam", "Beam", "B"]];
const DEFAULTS_LABEL = { colour: "Make the usual colours", position: "Make the usual positions", beam: "Make Narrow / Mid / Wide" };
let line = "";                    // the keypad's command line
let built = false;

const snap = () => state.snap || {};
const progVals = () => ((snap().programmer || {}).values) || {};

function selLabel() {
  const sel = selected();
  if (!sel.length) return "nothing selected";
  if (sel.length === 1) return `light ${sel[0]}`;
  if (sel.length <= 6) return `lights ${sel.join(", ")}`;
  return `${sel.slice(0, 5).join(", ")} … ${sel.length} lights`;
}

// a palette is lit when every selected light that it has values for holds them
export function paletteOn(kind, p) {
  const sel = selected();
  if (!sel.length) return false;
  const vals = progVals();
  let any = false;
  for (const n of sel) {
    const head = patch().find((x) => x.head_no === n);
    if (!head) continue;
    const own = (p.per || {})[String(n)] || p.values || {};
    const row = Object.entries(own).filter(([r]) => (head.map || []).includes(r));
    if (!row.length) continue;
    any = true;
    const pv = vals[String(n)] || {};
    if (!row.every(([r, v]) => pv[r] === v)) return false;
  }
  return any;
}

function tile(cls, label, title, onclick, extra = {}) {
  return h("button.dv-tile" + cls, { title, onclick, ...extra }, ...label);
}

// Hold a group tile to flash that group (a tap still selects it).  The
// release is caught on the window: the tile may be redrawn while held.
function holdToFlash(el, params) {
  let timer = 0;
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    timer = setTimeout(() => {
      el.classList.add("flashing");
      run("group_flash", { ...params, down: true }, { silentError: true });
      const swallow = (ev) => { ev.stopImmediatePropagation(); ev.preventDefault(); };
      const up = (ev) => {
        window.removeEventListener("pointerup", up, true);
        window.removeEventListener("pointercancel", up, true);
        el.classList.remove("flashing");
        run("group_flash", { down: false }, { silentError: true });
        // the click that follows a hold is not a tap
        if (ev.type === "pointerup") {
          window.addEventListener("click", swallow, { capture: true, once: true });
          setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 0);
        }
      };
      window.addEventListener("pointerup", up, true);
      window.addEventListener("pointercancel", up, true);
    }, 350);
  });
  const stop = () => clearTimeout(timer);
  el.addEventListener("pointerup", stop);
  el.addEventListener("pointerleave", stop);
  el.addEventListener("pointercancel", stop);
  return el;
}

function groupWindow() {
  const groups = snap().groups || [];
  const sel = selected();
  const same = (g) => g.heads.length === sel.length && g.heads.every((n) => sel.includes(n));
  const tiles = groups.map((g) => holdToFlash(tile(same(g) ? ".on" : "", [h("small", `G${g.n}`), h("b", g.name), h("span.dv-count", String(g.heads.length))],
    `Select ${g.name} (${g.heads.length} light${g.heads.length === 1 ? "" : "s"}) · hold to flash`,
    () => run("select_group", { n: g.n }),
    { oncontextmenu: (e) => { e.preventDefault(); groupMenu(e.currentTarget, g); } }), { group: g.n }));
  tiles.unshift(tile(sel.length && sel.length === patch().length ? ".on" : "", [h("small", "All"), h("b", "Every light"), h("span.dv-count", String(patch().length))],
    "Select every light", () => run("select_all")));
  tiles.push(tile(".dv-add", [h("b", "+ Group")], "Make a group of the selected lights", async () => {
    if (!sel.length) { toast("Select the lights first"); return; }
    const name = await promptBox("New group", "Name", "", { ok: "Make", placeholder: "e.g. Truss 1" });
    if (name) run("group_create", { name, heads: sel }, { toast: true });
  }));
  return win("Group", tiles);
}

function groupMenu(anchor, g) {
  menu(anchor, [
    { label: "Add the selection to it", run: () => run("group_edit", { n: g.n, add: true }, { toast: true }) },
    { label: "Rename…", run: async () => { const name = await promptBox("Rename group", "Name", g.name, { ok: "Rename" }); if (name) run("group_edit", { n: g.n, name }, { toast: true }); } },
    "-",
    { label: "Delete", danger: true, run: async () => { if (await confirmBox("Delete group", `Delete “${g.name}”? The lights stay.`, { ok: "Delete", danger: true })) run("group_delete", { n: g.n }, { toast: true }); } },
  ]);
}

// a beam palette with a gobo in it shows that gobo: its picture on the
// first light it was recorded on that has gobo pictures
function goboOf(p) {
  const rows = Object.entries(p.per || {}).map(([n, v]) => [+n, v]);
  if (!rows.length) rows.push(...patch().map((x) => [x.head_no, p.values || {}]));
  for (const [n, v] of rows) {
    if (!v || !("gobo" in v)) continue;
    const hd = patch().find((x) => x.head_no === n);
    if (!hd || !(hd.gobos || []).length) continue;
    const g = hd.gobos.find((r) => v.gobo >= r[0] && v.gobo <= r[1]);
    // the light's gobo list names only the gobos with a picture: a value
    // on none of them is the open slot (or a spin range) - a clear disc
    return g ? g[2] : "";
  }
  return null;
}

function swatch(kind, p) {
  if (kind === "beam") {
    const ref = goboOf(p);
    return ref === null ? null : goboIcon(ref);
  }
  if (kind !== "colour") return null;
  const v = p.values || {};
  const hex = p.hex || (("red" in v || "green" in v || "blue" in v)
    ? "#" + ["red", "green", "blue"].map((r) => Math.max(0, Math.min(255, v[r] || 0)).toString(16).padStart(2, "0")).join("") : null);
  return hex ? h("i.dv-sw", { style: { background: hex } }) : null;
}

const OFFER_ATTRS = { colour: ["red", "wheel", "cyan", "colour_macro"], beam: ["zoom"], position: ["pan"] };
const canMake = (kind) => patch().some((x) => (x.map || []).some((r) => (OFFER_ATTRS[kind] || []).includes(r))
  && (kind !== "position" || (x.map || []).includes("tilt")));
const noOffer = () => { try { return JSON.parse(localStorage.getItem("jarvis.dvNoOffer") || "[]"); } catch (e) { return []; } };
const setNoOffer = (v) => { try { localStorage.setItem("jarvis.dvNoOffer", JSON.stringify(v)); } catch (e) { /* private window */ } };

function paletteWindow(kind, title, letter) {
  const pals = (snap().palettes || {})[kind] || [];
  const tiles = pals.map((p) => tile(paletteOn(kind, p) ? ".on" : "", [h("small", `${letter}${p.n}`), swatch(kind, p), h("b", p.name),
    p.per ? h("span.dv-per", { title: "Each light keeps its own" }, "per light") : null],
    `${p.name}: apply to the selection (right-click: update, rename, delete)`,
    () => { if (!selected().length) { toast("Select lights first (Group window)"); return; } run("include_palette", { kind, palette: p.n }); },
    { oncontextmenu: (e) => { e.preventDefault(); paletteMenu(e.currentTarget, kind, p); } }));
  tiles.push(tile(".dv-add", [h("b", "+ Record")], `Record the selection's ${kind} as a palette`, async () => {
    if (!selected().length) { toast("Select lights and set them first"); return; }
    const name = await promptBox(`Record ${kind} palette`, "Name", "", { ok: "Record" });
    if (name) run("record_palette", { kind, name }, { toast: true });
  }));
  // the usual palettes, offered only when a light here can do them (no
  // "Narrow / Mid / Wide" for a rig with no zoom) - and the × puts it away
  if (!pals.length && canMake(kind) && !noOffer().includes(kind)) {
    const t = tile(".dv-add.dv-offer", [h("b", DEFAULTS_LABEL[kind])], "For this rig, each light its own values",
      () => run("palette_defaults", { kind }, { toast: true }));
    t.append(h("span.dv-tile-x", { role: "button", title: "Don't offer this here", "aria-label": "Don't offer this",
      onclick: (e) => { e.stopPropagation(); setNoOffer([...noOffer(), kind]); t.remove(); } }, "×"));
    tiles.push(t);
  }
  return win(title, tiles);
}

function paletteMenu(anchor, kind, p) {
  menu(anchor, [
    { label: "Update from the programmer", hint: "the cues that use it follow", run: () => {
      if (!selected().length) { toast("Select the lights and set them first"); return; }
      run("record_palette", { kind, palette: p.n, name: p.name }, { toast: true });
    } },
    { label: "Rename…", run: async () => { const name = await promptBox("Rename palette", "Name", p.name, { ok: "Rename" }); if (name) run("palette_edit", { kind, palette: p.n, name }, { toast: true }); } },
    "-",
    { label: "Delete", danger: true, hint: "cues keep their values", run: async () => {
      if (await confirmBox("Delete palette", `Delete “${p.name}”?`, { ok: "Delete", danger: true })) run("palette_edit", { kind, palette: p.n, delete: true }, { toast: true });
    } },
  ]);
}

function win(title, tiles) {
  return h("section.dv-win", h("header", h("b", title.toUpperCase()), h("span", `(${selLabel()})`)), h("div.dv-tiles", ...tiles));
}

// ---- the command strip --------------------------------------------------
// a key: its icon over its name, as on a pro desk's keys
const KEY_ICON = { "◀ Prev": "prev", "Next ▶": "next", All: "all", Odd: "odd", Even: "even", Highlight: "highlight", None: "none",
  Flip: "flip", Mirror: "mirror", Locate: "locate", "Line up": "lineup", Clear: "clear", Undo: "undo", "Record…": "rec",
  Update: "update", Plot: "plot", Cues: "cues", Patch: "patch", Outputs: "outputs", Macros: "macros", Setup: "setup",
  Help: "help", "⌫": "rubout", ENTER: "enter" };
function key(label, title, fn, cls = "") {
  const name = KEY_ICON[label];
  const text = label.replace(/^◀ |\s▶$/g, "");
  return h("button.dv-key" + cls, { title, onclick: fn, "aria-label": label === "⌫" ? "Rub out" : text },
    name ? icon(name) : null, label === "⌫" ? null : h("span", text));
}

async function focusedPb() { return (await import("./playbacks.js")).focusedPlayback(); }

function strip() {
  const shown = h("div.dv-line", { title: "The command line: 1 THRU 4 @ FULL, then ENTER" }, line || " ");
  const type = (t) => { line = (line + t).replace(/\s+/g, " ").trimStart(); shown.textContent = line || " "; };
  const enter = async () => {
    if (!line.trim()) return;
    const r = await run("run_command", { text: line.trim() }, { toast: true });
    if (r.ok) { line = ""; shown.textContent = " "; }
  };
  const pad = [["7"], ["8"], ["9"], ["THRU", " thru "], ["4"], ["5"], ["6"], ["+", " + "], ["1"], ["2"], ["3"], ["-", " - "],
    ["0"], ["."], ["@", " @ "], ["FULL", " full"]].map(([l, t]) => key(l, `Type ${l}`, () => type(t || l)));
  const kw = (id, title, ...keys) => [id, h("section.dv-win.dv-keywin",
    h("header", h("b", title.toUpperCase())), h("div.dv-sec", ...keys))];
  return [
    kw("select", "Select",
      key("◀ Prev", "One light at a time: the one before", () => run("select_step", { step: "prev" })),
      key("Next ▶", "One light at a time: the next", () => run("select_step", { step: "next" })),
      key("All", "All the lights of the selection again", () => run("select_step", { step: "all" })),
      key("Odd", "Keep the odd ones", () => run("select_split", { split: "odd" })),
      key("Even", "Keep the even ones", () => run("select_split", { split: "even" })),
      key("Highlight", "The selection at full white, to find it", () => run("highlight", {}), ".toggle"),
      key("None", "Clear the selection", () => select([]))),
    kw("poskeys", "Position",
      key("Flip", "Same spot, from the other end of the travel", () => run("flip", {}, { toast: true })),
      key("Mirror", "Pan reversed: left becomes right", () => run("mirror_pan", {}, { toast: true })),
      key("Locate", "Open white, centred: find the lights", () => run("locate", {}, { toast: true })),
      key("Line up", "Line up with the real lights; invert one that turns the wrong way", async () => (await import("./lineup.js")).openLineUp())),
    kw("progkeys", "Programmer",
      key("Clear", "Clear the programmer", () => run("clear_programmer"), ".warn"),
      key("Undo", "Undo the last change", () => run("undo", {}, { toast: true })),
      key("Record…", "Record the programmer as a cue", async () => (await import("./dialogs.js")).openCueDialog(await focusedPb()), ".rec"),
      key("Update", "Merge the programmer into the cue that's on (focused playback)", async () => {
        const n = await focusedPb();
        const pb = (snap().playbacks || []).find((p) => p.n === n);
        if (!pb || pb.index < 0) { toast(`Playback ${n} has no cue on`); return; }
        run("record_cue", { playback: n, cue: pb.index + 1, mode: "merge" }, { toast: true });
      })),
    kw("windows", "Windows",
      key("Plot", "The rig drawn flat with every light's number: select, move", async () => (await import("./plot.js")).openPlot()),
      key("Cues", "The cue list of the focused playback", async () => (await import("./dialogs.js")).openCueList(await focusedPb())),
      key("Patch", "Every light's DMX address", async () => (await import("./fixtures.js")).openDmxMap()),
      key("Outputs", "What goes out to the nodes", async () => (await import("./monitors.js")).openNodeMonitor()),
      key("Setup", "Settings", async () => (await import("./settings.js")).openSettings()),
      key("Help", "Help and keys", async () => (await import("./dialogs.js")).openHelp())),
    ["keypad", h("section.dv-win.dv-keywin.dv-padwin", h("header", { title: "The command line by touch: tap 1 THRU 4 @ FULL then ENTER to bring lights 1 to 4 to full - the same as typing it. With a keyboard you can hide this window (×)." },
      h("b", "KEYPAD"), h("span", "1 THRU 4 @ FULL, ENTER")), h("div.dv-sec.dv-pad", shown, h("div.dv-keys", ...pad,
      key("⌫", "Rub out", () => { line = line.trimEnd().split(" ").slice(0, -1).join(" "); line = line ? line + " " : ""; shown.textContent = line || " "; }),
      key("CLR", "Clear the line", () => { line = ""; shown.textContent = " "; }),
      key("ENTER", "Run the line", enter, ".enter"))))],
  ];
}

// The desk view is a board of windows, each where you put it: drag a
// window by its title to move it, by its corner to size it - snapped to a
// grid, remembered on this computer (Reset puts them back).  The board fills
// the height there is: a taller dock makes the windows shorter and their
// contents scroll - nothing spills over.
// The keys are built once (a key swapped out under a finger lost the tap -
// the keys "lagged"); the palette windows are redrawn only when what they
// show changes (groups, palettes, the selection, its values).
const COLS = 24, ROWS = 12;
const STACK_BELOW = 880;          // px: a narrower board stacks its windows
const LAYOUT_KEY = "jarvis.dvLayout";
const DEFAULT_LAYOUT = {
  group: { x: 0, y: 0, w: 7, h: 6 }, position: { x: 7, y: 0, w: 7, h: 6 },
  colour: { x: 0, y: 6, w: 7, h: 6 }, beam: { x: 7, y: 6, w: 7, h: 6 },
  select: { x: 14, y: 0, w: 5, h: 4 }, poskeys: { x: 14, y: 4, w: 5, h: 4 }, progkeys: { x: 14, y: 8, w: 5, h: 4 },
  keypad: { x: 19, y: 0, w: 5, h: 8 }, windows: { x: 19, y: 8, w: 5, h: 4 },
};
let layout = (() => {
  try { return { ...DEFAULT_LAYOUT, ...(JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") || {}) }; } catch (e) { return { ...DEFAULT_LAYOUT }; }
})();
const saveLayout = () => { try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch (e) { /* private window */ } };
let board = null, shownKey = "", zTop = 1;

// how small and how big a window may be sized (in board cells): small
// enough to tuck away, never so small its keys can't be read, never so big
// its keys float in empty space
const SIZE = {
  select: { w: [3, 8], h: [3, 6] }, poskeys: { w: [3, 8], h: [3, 5] }, progkeys: { w: [3, 8], h: [3, 5] },
  windows: { w: [3, 8], h: [3, 6] }, keypad: { w: [4, 9], h: [5, 12] },
};
const sizeOf = (id) => SIZE[id] || { w: [3, 24], h: [2, 12] };

const NAMES = { group: "Group", position: "Position palettes", colour: "Colour palettes", beam: "Beam palettes",
  select: "Select keys", poskeys: "Position keys", progkeys: "Programmer keys", windows: "Window keys", keypad: "Keypad" };
const hidden = () => (layout._hidden || []);

function place(el, id) {
  el.hidden = hidden().includes(id);
  const r0 = layout[id] || DEFAULT_LAYOUT[id], sz = sizeOf(id);
  // a layout saved before the limits: shown within them
  const r = { ...r0, w: Math.max(sz.w[0], Math.min(sz.w[1], r0.w)), h: Math.max(sz.h[0], Math.min(sz.h[1], r0.h)) };
  el.style.gridColumn = `${r.x + 1} / span ${Math.min(r.w, COLS - r.x)}`;
  el.style.gridRow = `${r.y + 1} / span ${Math.min(r.h, ROWS - r.y)}`;
}

// a window's title moves it, its corner sizes it (a finger or the mouse)
function movable(el, id) {
  el.dataset.win = id;
  const head = el.querySelector("header");
  const grip = h("div.dv-resize", { title: "Drag to size this window" });
  el.append(grip);
  const drag = (handle, resize) => handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || (!resize && e.target.closest("button"))) return;
    e.preventDefault();
    const b = board.getBoundingClientRect();
    const cw = b.width / COLS, ch = b.height / ROWS;
    const start = { ...(layout[id] || DEFAULT_LAYOUT[id]) }, sx = e.clientX, sy = e.clientY;
    el.style.zIndex = ++zTop;
    el.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const dx = Math.round((ev.clientX - sx) / cw), dy = Math.round((ev.clientY - sy) / ch);
      const r = { ...start };
      const sz = sizeOf(id);
      if (resize) {
        r.w = Math.max(sz.w[0], Math.min(COLS - r.x, sz.w[1], start.w + dx));
        r.h = Math.max(sz.h[0], Math.min(ROWS - r.y, sz.h[1], start.h + dy));
      }
      else { r.x = Math.max(0, Math.min(COLS - r.w, start.x + dx)); r.y = Math.max(0, Math.min(ROWS - r.h, start.y + dy)); }
      layout[id] = r;
      place(el, id);
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      el.classList.remove("dragging");
      saveLayout();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up, { once: true });
    handle.addEventListener("pointercancel", up, { once: true });
  });
  look(el, id);
  if (head) {
    head.title = "Drag to move this window";
    head.append(h("button.dv-opt", { title: "This window's look: tile size, text size, tiles across, numbers", "aria-label": `${NAMES[id] || id}: look`,
      onclick: (e) => { e.stopPropagation(); lookMenu(e.currentTarget, el, id); } }, "⋯"));
    head.append(h("button.dv-x", { title: "Hide this window (Show brings it back)", "aria-label": `Hide ${NAMES[id] || id}`,
      onclick: (e) => { e.stopPropagation(); layout._hidden = [...new Set([...hidden(), id])]; saveLayout(); place(el, id); } }, "×"));
    drag(head, false);
  }
  drag(grip, true);
  place(el, id);
  return el;
}

// A window's look, kept with its place: tile size (s / m / l / xl / fill),
// text size, how many tiles across (0 = as many as fit), numbers on/off
function look(el, id) {
  const lk = (layout[id] && layout[id].look) || {};
  el.dataset.size = lk.size || "m";
  el.dataset.text = lk.text || "m";
  el.dataset.nums = lk.nums === false ? "off" : "on";
  if (lk.cols) { el.dataset.cols = lk.cols; el.style.setProperty("--cols", lk.cols); } else { delete el.dataset.cols; el.style.removeProperty("--cols"); }
}
function setLook(el, id, change) {
  const r = { ...(layout[id] || DEFAULT_LAYOUT[id]) };
  r.look = { ...(r.look || {}), ...change };
  layout[id] = r;
  saveLayout();
  look(el, id);
}
function lookMenu(anchor, el, id) {
  const lk = (layout[id] && layout[id].look) || {};
  const tick = (on, label) => (on ? "✓ " : "") + label;
  const tiles = el.querySelector(".dv-tiles");
  const sizes = [["s", "Small"], ["m", "Medium"], ["l", "Large"], ["xl", "Extra large"], ...(tiles ? [["fill", "Fill the window"]] : [])];
  menu(anchor, [
    ...sizes.map(([v, label]) => ({ label: tick((lk.size || "m") === v, `${tiles ? "Tiles" : "Keys"}: ${label}`), run: () => setLook(el, id, { size: v }) })),
    "-",
    ...[["s", "Small"], ["m", "Normal"], ["l", "Large"], ["xl", "Extra large"]].map(([v, label]) => ({ label: tick((lk.text || "m") === v, `Text: ${label}`), run: () => setLook(el, id, { text: v }) })),
    ...(tiles ? ["-",
      ...[[0, "As many as fit"], [2, "2"], [3, "3"], [4, "4"], [5, "5"], [6, "6"], [8, "8"]].map(([v, label]) => ({ label: tick((lk.cols || 0) === v, `Across: ${label}`), run: () => setLook(el, id, { cols: v }) })),
      "-",
      { label: tick(lk.nums !== false, "Numbers on the tiles"), hint: "G1, P2, C3…", run: () => setLook(el, id, { nums: lk.nums === false }) }] : []),
    "-",
    { label: "Back to the standard look", run: () => setLook(el, id, { size: "m", text: "m", cols: 0, nums: true }) },
    { label: "This look on every window", hint: "size and text", run: () => {
      for (const w of board.querySelectorAll(":scope > [data-win]")) setLook(w, w.dataset.win, { size: lk.size === "fill" && !w.querySelector(".dv-tiles") ? "l" : (lk.size || "m"), text: lk.text || "m" });
    } },
  ]);
}

export function resetDeskLayout() {
  layout = { ...DEFAULT_LAYOUT };
  saveLayout();
  if (board) for (const el of board.children) if (el.dataset.win) { el.style.zIndex = ""; place(el, el.dataset.win); }
}

export function renderDeskView() {
  const box = $("#desk-view");
  if (!box || box.closest("[hidden]")) return;
  const s = snap();
  const key = JSON.stringify([s.groups, s.palettes, selected(), progVals(), patch().length]);
  if (!board || !box.contains(board)) {
    board = h("div.dv-board");
    board.append(...strip().map(([id, el]) => movable(el, id)));
    // too narrow for the 24-column board (a laptop's programmer panel): the
    // windows stack at their own height instead of being crushed
    const fit = () => { if (board) board.classList.toggle("dv-stack", board.clientWidth > 0 && board.clientWidth < STACK_BELOW); };
    new ResizeObserver(fit).observe(board);
    box.replaceChildren(h("div.dv",
      h("div.dv-bar", h("span.muted.small", "Drag a window by its title to move it, by its corner to size it; × hides it."),
        h("span.grow"),
        h("button.btn.small.ghost", { title: "Bring back a hidden window", onclick: (e) => menu(e.currentTarget,
          Object.entries(NAMES).map(([id, name]) => ({ label: (hidden().includes(id) ? "" : "✓ ") + name, run: () => {
            layout._hidden = hidden().includes(id) ? hidden().filter((x) => x !== id) : [...hidden(), id];
            saveLayout();
            const el = board.querySelector(`:scope > [data-win="${id}"]`);
            if (el) place(el, id);
          } }))) }, "Show ▾"),
        h("button.btn.small.ghost", { onclick: resetDeskLayout, title: "Every window back where it started, all shown" }, "Reset layout")),
      board));
    shownKey = "";
  }
  if (key === shownKey) return;
  shownKey = key;
  const wins = { group: groupWindow(), position: paletteWindow(...KINDS[0]), colour: paletteWindow(...KINDS[1]), beam: paletteWindow(...KINDS[2]) };
  for (const [id, el] of Object.entries(wins)) {
    const old = board.querySelector(`:scope > [data-win="${id}"]`);
    const made = movable(el, id);
    if (old) { made.style.zIndex = old.style.zIndex; board.replaceChild(made, old); } else board.prepend(made);
  }
}

export function initDeskView() {
  if (built) return;
  built = true;
  on("snapshot", renderDeskView);
  on("selection", renderDeskView);
}
