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
  if (sel.length <= 6) return sel.join(", ");
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
    const g = hd && (hd.gobos || []).find((r) => v.gobo >= r[0] && v.gobo <= r[1]);
    if (g) return g[2];
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
  if (!pals.length) {
    tiles.push(tile(".dv-add", [h("b", DEFAULTS_LABEL[kind])], "For this rig, each light its own values",
      () => run("palette_defaults", { kind }, { toast: true })));
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
  Flip: "flip", Mirror: "mirror", Locate: "locate", "Line up…": "lineup", Clear: "clear", Undo: "undo", "Record…": "rec",
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
  return h("aside.dv-strip",
    h("div.dv-sec", h("span.k", "Select"),
      key("◀ Prev", "One light at a time: the one before", () => run("select_step", { step: "prev" })),
      key("Next ▶", "One light at a time: the next", () => run("select_step", { step: "next" })),
      key("All", "All the lights of the selection again", () => run("select_step", { step: "all" })),
      key("Odd", "Keep the odd ones", () => run("select_split", { split: "odd" })),
      key("Even", "Keep the even ones", () => run("select_split", { split: "even" })),
      key("Highlight", "The selection at full white, to find it", () => run("highlight", {}), ".toggle"),
      key("None", "Clear the selection", () => select([]))),
    h("div.dv-sec", h("span.k", "Position"),
      key("Flip", "Same spot, from the other end of the travel", () => run("flip", {}, { toast: true })),
      key("Mirror", "Pan reversed: left becomes right", () => run("mirror_pan", {}, { toast: true })),
      key("Locate", "Open white, centred: find the lights", () => run("locate", {}, { toast: true })),
      key("Line up…", "Line up with the real lights; invert one that turns the wrong way", async () => (await import("./lineup.js")).openLineUp())),
    h("div.dv-sec", h("span.k", "Programmer"),
      key("Clear", "Clear the programmer", () => run("clear_programmer"), ".warn"),
      key("Undo", "Undo the last change", () => run("undo", {}, { toast: true })),
      key("Record…", "Record the programmer as a cue", async () => (await import("./dialogs.js")).openCueDialog(await focusedPb()), ".rec"),
      key("Update", "Merge the programmer into the cue that's on (focused playback)", async () => {
        const n = await focusedPb();
        const pb = (snap().playbacks || []).find((p) => p.n === n);
        if (!pb || pb.index < 0) { toast(`Playback ${n} has no cue on`); return; }
        run("record_cue", { playback: n, cue: pb.index + 1, mode: "merge" }, { toast: true });
      })),
    h("div.dv-sec", h("span.k", "Windows"),
      key("Plot", "The rig drawn flat with every light's number: select, move", async () => (await import("./plot.js")).openPlot()),
      key("Cues", "The cue list of the focused playback", async () => (await import("./dialogs.js")).openCueList(await focusedPb())),
      key("Patch", "Every light's DMX address", async () => (await import("./fixtures.js")).openDmxMap()),
      key("Outputs", "What goes out to the nodes", async () => (await import("./monitors.js")).openNodeMonitor()),
      key("Macros", "Macros", async () => (await import("./macros.js")).openMacros()),
      key("Setup", "Settings", async () => (await import("./settings.js")).openSettings()),
      key("Help", "Help and keys", async () => (await import("./dialogs.js")).openHelp())),
    h("div.dv-sec.dv-pad", h("span.k", "Keypad"), shown, h("div.dv-keys", ...pad,
      key("⌫", "Rub out", () => { line = line.trimEnd().split(" ").slice(0, -1).join(" "); line = line ? line + " " : ""; shown.textContent = line || " "; }),
      key("CLR", "Clear the line", () => { line = ""; shown.textContent = " "; }),
      key("ENTER", "Run the line", enter, ".enter"))));
}

export function renderDeskView() {
  const box = $("#desk-view");
  if (!box || box.closest("[hidden]")) return;
  box.replaceChildren(h("div.dv",
    h("div.dv-grid", groupWindow(), paletteWindow(...KINDS[0]), paletteWindow(...KINDS[1]), paletteWindow(...KINDS[2])),
    strip()));
}

export function initDeskView() {
  if (built) return;
  built = true;
  on("snapshot", renderDeskView);
  on("selection", renderDeskView);
}
