// Quick buttons: a grid of instant buttons, as customisable as a button can
// be.  Any button picks its lights (all, the selection, a group, a type -
// or just the odd / even / left / right half of them), what it does (flash,
// dim, colour, strobe, blackout, effects, a mix of all of those, or the look
// on stage captured as it is), how it behaves (hold, on/off, a timed shot,
// turn off after N seconds, one-at-a-time with its radio group) and how it
// looks (its name and tile colour).  Pages have names; in Edit, drag a
// button onto another slot to move it.  Pressing is live (never an undo
// step); setting a button up is an edit, saved with the show.
import { state, on, patch } from "./store.js";
import { run } from "./actions.js";
import { $, $$, h, modal, toast, promptBox } from "./ui.js";

// what a button does, as the editor offers it (engine kinds + two made of
// "custom": dim, and the look captured from the stage)
const DOES = [
  ["flash", "Flash", "Full on while held - keeps moving"],
  ["dim", "Dim", "Holds the lights down to a level"],
  ["colour", "Colour", "Changes their colour"],
  ["strobe", "Strobe", "Strobes them"],
  ["kill", "Blackout lights", "Turns these lights off"],
  ["fx", "Effect", "Runs one effect (circle, rainbow, chase…)"],
  ["move", "My move", "Plays one of your saved moves (Move tab)"],
  ["custom", "Mix", "Any mix of level, dim, colour, strobe and effects"],
  ["capture", "From the stage", "Whatever is on stage now: colours, positions, effects"],
  ["go", "GO", "GO on a playback"],
  ["release", "Release", "Releases a playback"],
  ["preset", "Preset", "Applies a recorded preset"],
  ["blackout", "Blackout all", "Everything off while held"],
  ["sfx", "Fire SFX", "Confetti / CO2 / flame / sparks (needs ARM)"],
  ["fog", "Fog / haze", "Fog or haze output"],
  ["laser", "Laser", "Laser output (needs ARM)"],
  ["arm", "ARM FX", "Arms fire and lasers"],
  ["fxkill", "KILL FX", "Stops every effect, disarms"],
];
const KIND_COLOUR = { flash: "#f8fafc", strobe: "#fde047", colour: null, kill: "#64748b", fx: "#a78bfa",
  custom: "#38bdf8", move: "#a78bfa", go: "#22c55e", release: "#f97316", preset: "#38bdf8", blackout: "#ef4444",
  sfx: "#f97316", fog: "#cbd5e1", laser: "#22d3ee", arm: "#ef4444", fxkill: "#ef4444" };
const FX = [["rainbow", "Rainbow"], ["colour_chase", "Colour chase"], ["alternate", "Alternate"], ["breathe", "Breathe"],
  ["pulse", "Pulse"], ["dimmer_chase", "Dimmer chase"], ["sparks", "Sparks"], ["circle", "Circle"],
  ["pan_sweep", "Sweep"], ["tilt_bounce", "Bounce"], ["figure_eight", "Figure 8"], ["fan_pan", "Fan"],
  ["gobo_spin", "Gobo spin"], ["prism_fan", "Prism fan"], ["zoom_pulse", "Zoom pulse"], ["strobe_random", "Random strobe"]];
const MOVES = new Set(["circle", "pan_sweep", "tilt_bounce", "figure_eight", "fan_pan"]);
const TYPES = [["spot", "Spots"], ["beam", "Beams"], ["wash", "Washes"], ["par", "PARs"], ["bar", "Bars"]];
const FX_TYPES = [["confetti", "Confetti"], ["co2", "CO2"], ["flame", "Flames"], ["spark", "Sparks"],
  ["sfx", "Other effects"], ["atmos", "Fog / haze"], ["laser", "Lasers"]];
const SWATCH = ["#ffffff", "#ff0000", "#ff5a00", "#ffb000", "#ffe600", "#00ff40", "#00ffcc", "#00c8ff",
  "#0033ff", "#7a00ff", "#ff00cc", "#ff3c8c"];
const TINTS = ["#38bdf8", "#22c55e", "#f59e0b", "#ef4444", "#a78bfa", "#ec4899", "#f8fafc", "#64748b"];
const SPLITS = [["", "All of them"], ["odd", "Odd"], ["even", "Even"], ["left", "Left half"], ["right", "Right half"]];
const FX_KINDS = new Set(["sfx", "fog", "laser", "arm", "fxkill"]);
const NO_TARGET = new Set(["go", "release", "preset", "blackout", "arm", "fxkill"]);
const ONE_SHOT = new Set(["go", "release", "preset", "arm", "fxkill"]);
// Icons a tile can carry: 24x24 stroke paths (the engine keeps the same names).
const ICONS = {
  bolt: "M13 2 4 14h7l-1 8 9-12h-7z",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  star: "m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z",
  heart: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z",
  fire: "M12 22c4 0 7-2.7 7-7 0-4-3-6-4-9-1 2-2 3-3.5 3.5C12 7 11 4 9 2c0 4-4 7-4 13 0 4.3 3 7 7 7z",
  snow: "M12 2v20M4.2 7l15.6 10M4.2 17 19.8 7M9 4l3 2 3-2M9 20l3-2 3 2",
  drop: "M12 2.7 6.3 8.4a8 8 0 1 0 11.4 0z",
  music: "M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  strobe: "M3 12h3l2-6 4 12 3-9 2 3h4",
  spin: "M21 12a9 9 0 1 1-3-6.7M21 3v6h-6",
  sparkle: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  stop: "M6 6h12v12H6z",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M19 12l-7 7-7-7",
};
const SVGNS = "http://www.w3.org/2000/svg";
function icon(name) {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "qicon");
  const p = document.createElementNS(SVGNS, "path");
  p.setAttribute("d", ICONS[name] || "");
  svg.append(p);
  return svg;
}

// kinds whose brightness can fade in / out
const FADES = new Set(["flash", "dim", "custom", "capture", "kill"]);
// the console's own keys a button may not take
const RESERVED = new Set(["b", "x", "a", "l", "c", "r", "o", "i", "d", "g", "f", "/", "?", " "]);

let page = 1;
let editing = false;
let lastKey = "";
const held = new Set();

const quick = () => (state.snap && state.snap.quick) || { buttons: [], active: [], names: {}, pages: 8, slots: 24 };
const activeIds = () => new Set((state.lite && state.lite.quick_active) || quick().active || []);
const groups = () => (state.snap && state.snap.groups) || [];
const autoGroups = () => (state.snap && state.snap.auto_groups) || [];

// press and release are separate requests to a threaded server: a release
// is only sent once its press was answered, or a quick tap could be handled
// release-first and leave the button (a flash, a CO2 hold) on
const inflight = new Map();
function press(btn, down) {
  if (down) held.add(btn.id); else held.delete(btn.id);
  const before = inflight.get(btn.id) || Promise.resolve();
  const next = before.then(() => run("quick_press", { id: btn.id, down }, { silentError: false })).catch(() => {});
  inflight.set(btn.id, next);
}
// a release anywhere lets go of every held button, even if the grid was
// re-drawn under the finger
window.addEventListener("pointerup", () => {
  for (const id of [...held]) {
    const b = quick().buttons.find((x) => x.id === id);
    if (b && b.mode === "hold") press(b, false); else held.delete(id);
  }
}, true);

// "PARs · odd · hold" - what a pad does, in its corner
function targetText(t = {}) {
  let who = "All";
  if (t.group !== undefined) who = (groups().find((g) => g.n === t.group) || {}).name || `Group ${t.group}`;
  else if (t.auto) who = (autoGroups().find((g) => g.key === t.auto) || {}).name || t.auto;
  else if (t.type) who = ([...TYPES, ...FX_TYPES].find(([k]) => k === t.type) || [0, t.type])[1];
  else if (t.heads) who = `${t.heads.length} light${t.heads.length === 1 ? "" : "s"}`;
  return t.split ? `${who} · ${t.split}` : who;
}
function modeText(b) {
  if (ONE_SHOT.has(b.kind)) return "tap";
  if (b.mode === "tap" && b.seconds) return `${b.seconds}s shot`;
  const m = b.mode === "latch" ? "on/off" : b.mode;
  return b.seconds ? `${m} · ${b.seconds}s` : m;
}

function renderPages() {
  const q = quick();
  const names = q.names || {};
  const used = new Set(q.buttons.map((b) => b.page));
  const box = $("#qb-pages");
  const pages = [];
  for (let p = 1; p <= (q.pages || 8); p++) {
    // pages 1-4 always; later ones once used, or the next free one in Edit
    if (p > 4 && !used.has(p) && p !== page && !(editing && p === Math.max(4, ...used) + 1)) continue;
    pages.push(h("button" + (p === page ? ".on" : ""), {
      dataset: { page: p },
      title: editing ? "Click to open · double-click to rename" : names[p] || `Page ${p}`,
      onclick: () => { page = p; render(true); },
      ondblclick: () => renamePage(p),
    }, names[p] || String(p)));
  }
  box.replaceChildren(...pages);
}

async function renamePage(p) {
  const names = quick().names || {};
  const name = await promptBox(`Page ${p}`, "Name (empty for a number)", names[p] || "", { ok: "Save", placeholder: "e.g. Movers" });
  if (name !== null) run("quick_page", { page: p, name });
}

// The slots a big tile takes besides its own, or null when one of them
// is used (then it shows at normal size).
const COLS = 8;
function tileFit(slot, size, total, used) {
  const col = (slot - 1) % COLS;
  const w = (size === "wide" || size === "big") && col < COLS - 1 ? 2 : 1;
  const t = (size === "tall" || size === "big") && slot + COLS <= total ? 2 : 1;
  const take = [];
  for (let r = 0; r < t; r++) for (let c = 0; c < w; c++) if (r || c) take.push(slot + r * COLS + c);
  return take.some(used) ? null : { w, t, take };
}

function render(force = false) {
  const box = $("#qb-grid");
  if (!box) return;
  const q = quick();
  const key = JSON.stringify([q.buttons, q.names, page, editing, groups().map((g) => [g.n, g.name]),
    autoGroups().map((g) => [g.key, g.name])]);
  if (!force && key === lastKey) return;        // only redraw on a change: a click needs the same element
  lastKey = key;
  const active = activeIds();
  const byslot = new Map(q.buttons.filter((b) => b.page === page).map((b) => [b.slot, b]));
  const cells = [];
  // big tiles take the empty slots next to / under them
  const total = q.slots || 24, covered = new Set(), span = new Map();
  for (const [slot, b] of [...byslot].sort((a, c) => a[0] - c[0])) {
    if (!b.size || covered.has(slot)) continue;
    const fit = tileFit(slot, b.size, total, (x) => byslot.has(x) || covered.has(x));
    if (!fit) continue;                         // no room: stays normal size
    fit.take.forEach((x) => covered.add(x));
    span.set(slot, [fit.w, fit.t]);
  }
  for (let slot = 1; slot <= total; slot++) {
    if (covered.has(slot)) continue;
    const b = byslot.get(slot);
    const drop = (el) => {
      if (!editing) return;
      el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop"); });
      el.addEventListener("dragleave", () => el.classList.remove("drop"));
      el.addEventListener("drop", (e) => {
        e.preventDefault();
        el.classList.remove("drop");
        const from = JSON.parse(e.dataTransfer.getData("text/plain") || "null");
        if (from && !(from.page === page && from.slot === slot)) {
          run("quick_move", { page: from.page, slot: from.slot, to_page: page, to_slot: slot, copy: e.altKey });
        }
      });
    };
    if (!b) {
      const el = h("button.qbtn.empty", {
        title: editing ? "Set up this button" : "Empty - press Edit to set it up",
        onclick: () => { if (editing) editButton(slot, null); },
      }, editing ? "+" : "");
      drop(el);
      cells.push(el);
      continue;
    }
    const tint = b.tint || b.colour || KIND_COLOUR[b.kind] || "#94a3b8";
    const el = h("button.qbtn" + (active.has(b.id) ? ".on" : ""), {
      title: `${b.label} · ${targetText(b.target)} · ${modeText(b)}${editing ? " (click to edit, drag to move, Alt-drag to copy)" : ""}`,
      dataset: { id: b.id },
      draggable: editing ? "true" : null,
    }, h("span.qline", b.icon ? icon(b.icon) : null, h("b", b.label)), h("small", NO_TARGET.has(b.kind) ? modeText(b) : `${targetText(b.target)} · ${modeText(b)}`),
    b.key ? h("kbd.qkey", b.key.toUpperCase()) : null);
    el.style.setProperty("--tint", tint);
    const sp = span.get(slot);
    if (sp) {
      el.style.gridColumn = `${(slot - 1) % COLS + 1} / span ${sp[0]}`;
      el.style.gridRow = `${Math.floor((slot - 1) / COLS) + 1} / span ${sp[1]}`;
      el.classList.add(sp[1] > 1 ? "qbig" : "qwide");
    }
    if (editing) {
      el.addEventListener("click", () => editButton(slot, b));
      el.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", JSON.stringify({ page, slot })));
      drop(el);
    } else {
      el.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        el.setPointerCapture(e.pointerId);
        el.classList.add("down");
        press(b, true);
      });
      const up = () => {
        el.classList.remove("down");
        if (b.mode === "hold" && held.has(b.id)) press(b, false);
        else held.delete(b.id);
      };
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
      el.addEventListener("lostpointercapture", up);
    }
    cells.push(el);
  }
  box.replaceChildren(...cells);
  renderPages();
  $("#qb-edit").classList.toggle("on", editing);
  $("#qb-edit").textContent = editing ? "Done" : "Edit";
  const empty = !q.buttons.some((b) => b.page === page);
  $("#qb-suggest").hidden = !empty;
}

// ---------------------------------------------------------------- editor
function chips(options, value, pick, { multi = false } = {}) {
  return h("div.chip-row.qe-chips", ...options.map(([v, label, title]) => h(
    "button.chip" + ((multi ? value.includes(v) : value === v) ? ".on" : ""),
    { type: "button", title: title || null, onclick: () => pick(v) }, label)));
}
function swatches(list, value, pick, { none = null } = {}) {
  return h("div.qe-swatches",
    none ? h("button.chip" + (!value ? ".on" : ""), { type: "button", onclick: () => pick(null) }, none) : null,
    ...list.map((c) => h("button.qe-sw" + (value && value.toLowerCase() === c ? ".on" : ""), {
      type: "button", "aria-label": c, title: c, style: { background: c }, onclick: () => pick(c),
    })),
    h("input.qe-custom", { type: "color", value: value || "#ffffff", title: "Any colour",
      onchange: (e) => pick(e.target.value) }));
}
function slider(value, min, max, step, pick, unit = "%") {
  const out = h("output.qe-out", `${value}${unit}`);
  const input = h("input", { type: "range", min, max, step, value });
  input.addEventListener("input", () => { out.textContent = `${input.value}${unit}`; pick(+input.value, false); });
  return h("div.qe-slider", input, out);
}
const row = (label, ...kids) => h("div.qe-row", h("div.qe-label", label), h("div.qe-body", ...kids));

function fromButton(b) {
  // the editor's working copy of a button
  const s = {
    does: b ? b.kind : "flash", label: b ? b.label : "", tint: b ? b.tint || null : null,
    mode: b ? b.mode : "hold", seconds: b && b.seconds ? b.seconds : "", exclusive: b ? b.exclusive || "" : "",
    target: b ? { ...(b.target || { all: true }) } : { all: true },
    colour: b ? b.colour || null : null, level: b && b.level !== undefined ? b.level : 100,
    dim: b && b.dim !== undefined ? b.dim : 30, hz: b ? b.hz || 10 : 10,
    fx: b && b.fx ? b.fx : "rainbow", params: b && b.params ? { ...b.params } : {},
    mix: { level: b && b.level !== undefined, dim: b && b.dim !== undefined, colour: !!(b && b.colour),
      strobe: !!(b && b.hz), kill: !!(b && b.kill) },
    fxList: b && b.fx_list ? b.fx_list.map((f) => f.name) : [], fxListParams: b && b.fx_list ? b.fx_list : [],
    values: b ? b.values || null : null, attrs: b ? b.attrs || null : null, recapture: !b,
    move: b ? b.move || "" : "", size: b ? b.size || "" : "", icon: b ? b.icon || "" : "",
    fadeIn: b && b.fade_in ? b.fade_in : 0, fadeOut: b && b.fade_out ? b.fade_out : 0, key: b ? b.key || "" : "",
    playback: b ? b.playback || 1 : 1, cue: b ? b.cue || "" : "", preset: b ? b.preset || "" : "",
    fogLevel: b && b.kind === "fog" ? b.level || 100 : 100,
  };
  if (b && b.kind === "custom") {
    const only = Object.keys(b).filter((k) => ["level", "hz", "kill", "attrs", "values", "fx_list", "colour"].includes(k) && b[k]);
    if (b.dim !== undefined && !only.length) s.does = "dim";
    else if (b.values || b.attrs) s.does = "capture";
    else s.does = "custom";
  }
  return s;
}

function toButton(s) {
  const d = s.does;
  const kind = d === "dim" || d === "capture" ? "custom" : d;
  const label = s.label.trim() || (DOES.find(([k]) => k === d) || [0, "Button"])[1];
  const b = { kind, label, mode: ONE_SHOT.has(d) ? "tap" : s.mode };
  if (s.tint) b.tint = s.tint;
  if (s.size) b.size = s.size;
  if (s.icon) b.icon = s.icon;
  if (!NO_TARGET.has(d)) b.target = { ...s.target };
  if (!ONE_SHOT.has(d)) {
    if (s.seconds !== "" && +s.seconds > 0) b.seconds = +s.seconds;
    if (s.exclusive.trim()) b.exclusive = s.exclusive.trim();
  }
  if (d === "flash") { b.level = s.level; if (s.colour) b.colour = s.colour; }
  if (d === "dim") b.dim = s.dim;
  if (d === "colour") b.colour = s.colour || "#ffffff";
  if (d === "strobe") { b.hz = s.hz; if (s.colour) b.colour = s.colour; }
  if (d === "fx") { b.fx = s.fx; b.params = s.params; }
  if (d === "move") b.move = s.move;
  if (FADES.has(d)) { if (s.fadeIn) b.fade_in = s.fadeIn; if (s.fadeOut) b.fade_out = s.fadeOut; }
  if (s.key) b.key = s.key;
  if (d === "custom" || d === "capture") {
    if (s.mix.level) b.level = s.level;
    if (s.mix.dim) b.dim = s.dim;
    if (s.mix.colour && s.colour) b.colour = s.colour;
    if (s.mix.strobe) b.hz = s.hz;
    if (s.mix.kill) b.kill = true;
    if (s.fxList.length) b.fx_list = s.fxList.map((name) => s.fxListParams.find((f) => f.name === name) || { name });
  }
  if (d === "capture") {
    if (s.recapture) b.capture = true;
    else { if (s.values) b.values = s.values; if (s.attrs) b.attrs = s.attrs; }
  }
  if (d === "go" || d === "release") b.playback = +s.playback || 1;
  if (d === "go" && s.cue !== "") b.cue = +s.cue;
  if (d === "preset") b.preset = +s.preset;
  if (["sfx", "fog", "laser"].includes(d) && s.seconds !== "") b.seconds = +s.seconds;
  if (d === "fog") b.level = s.fogLevel;
  return b;
}

function editButton(slot, btn) {
  const s = fromButton(btn);
  const body = h("div.qe");
  const selected = (state.snap && state.snap.selected) || [];
  const tags = [...new Set(quick().buttons.map((b) => b.exclusive).filter(Boolean))];

  const draw = () => {
    const d = s.does;
    const fxKind = FX_KINDS.has(d);
    const t = s.target;
    const tv = t.group !== undefined ? "g" + t.group : t.auto ? "a" + t.auto : t.type ? "t" + t.type : t.heads ? "sel" : "all";
    const setT = (v) => {
      const split = s.target.split;
      s.target = v === "all" ? { all: true } : v === "sel" ? { heads: [...selected] }
        : v[0] === "g" ? { group: +v.slice(1) } : v[0] === "a" ? { auto: v.slice(1) } : { type: v.slice(1) };
      if (split) s.target.split = split;
      draw();
    };
    const kids = [
      row("Name", h("input.qe-name", { type: "text", value: s.label, maxlength: 24, placeholder: "e.g. Flash red",
        oninput: (e) => { s.label = e.target.value; } })),
      row("Tile colour", swatches(TINTS, s.tint, (c) => { s.tint = c; draw(); }, { none: "Auto" })),
      row("Tile size", h("div.chip-row", chips([["", "Normal"], ["wide", "Wide"], ["tall", "Tall"], ["big", "Big"]], s.size,
        (v) => { s.size = v; draw(); }),
        s.size && !tileFit(slot, s.size, quick().slots || 24,
          (x) => quick().buttons.some((o) => o.page === page && o.slot === x && !(btn && o.id === btn.id)))
          ? h("span.qe-note.warn", s.size === "tall" ? "The space below is taken: it will show at normal size until it is free."
            : "The space next to it is taken: it will show at normal size until it is free.")
          : null)),
      row("Icon", h("div.qe-icons",
        h("button.qe-icon" + (!s.icon ? ".on" : ""), { title: "No icon", onclick: () => { s.icon = ""; draw(); } }, "None"),
        ...Object.keys(ICONS).map((k) => h("button.qe-icon" + (s.icon === k ? ".on" : ""),
          { title: k, onclick: () => { s.icon = k; draw(); } }, icon(k))))),
      row("Does", chips(DOES.map(([k, l, tt]) => [k, l, tt]), d, (v) => { s.does = v; draw(); })),
    ];
    if (!NO_TARGET.has(d)) {
      const who = fxKind
        ? [["all", "All effects"], ["sel", `Selected (${selected.length})`], ...FX_TYPES.map(([k, l]) => ["t" + k, l])]
        : [["all", "All lights"], ["sel", `Selected (${selected.length})`], ...groups().map((g) => ["g" + g.n, g.name]),
          ...autoGroups().map((g) => ["a" + g.key, `${g.name} (${g.heads.length})`])];
      if (t.heads && tv === "sel") who[1][1] = `${t.heads.length} chosen light${t.heads.length === 1 ? "" : "s"}`;
      kids.push(row("Which lights", chips(who, tv, setT),
        fxKind ? null : chips(SPLITS, t.split || "", (v) => { if (v) s.target.split = v; else delete s.target.split; draw(); })));
    }
    // what it does, in detail
    if (d === "flash") {
      kids.push(row("Level", slider(s.level, 5, 100, 5, (v) => { s.level = v; })));
      kids.push(row("Colour", swatches(SWATCH, s.colour, (c) => { s.colour = c; draw(); }, { none: "Keep their colour" })));
    }
    if (d === "dim") kids.push(row("Dim to", slider(s.dim, 0, 100, 5, (v) => { s.dim = v; })));
    if (d === "colour") kids.push(row("Colour", swatches(SWATCH, s.colour || "#ffffff", (c) => { s.colour = c; draw(); })));
    if (d === "strobe") {
      kids.push(row("Speed", chips([[2, "Slow"], [5, "Medium"], [10, "Fast"], [18, "Very fast"]], s.hz,
        (v) => { s.hz = v; draw(); }), slider(s.hz, 1, 20, 0.5, (v) => { s.hz = v; }, " Hz")));
      kids.push(row("Colour", swatches(SWATCH, s.colour, (c) => { s.colour = c; draw(); }, { none: "Keep their colour" })));
    }
    if (d === "fx") {
      kids.push(row("Effect", chips(FX, s.fx, (v) => { s.fx = v; s.params = {}; draw(); })));
      if (MOVES.has(s.fx)) {
        kids.push(row("Direction", chips([[1, "↻ Clockwise"], [-1, "↺ Counter-clockwise"]], s.params.direction ?? 1,
          (v) => { s.params.direction = v; draw(); })));
        kids.push(row("Arc", chips([[90, "90°"], [180, "180°"], [270, "270°"], [360, "Full"]], s.params.arc ?? 360,
          (v) => { s.params.arc = v; draw(); })));
        kids.push(row("Size", chips([[10, "S"], [20, "M"], [40, "L"]], s.params.size ?? 20, (v) => { s.params.size = v; draw(); })));
        const secs = s.params.speed ? Math.round(1 / s.params.speed) : 8;
        kids.push(row("Speed", slider(secs, 2, 30, 1, (v) => { s.params.speed = +(1 / v).toFixed(4); }, " s per turn")));
      } else {
        kids.push(row("Speed", slider(s.params.speed ?? 1, 0.1, 4, 0.1, (v) => { s.params.speed = v; }, "×")));
      }
    }
    if (d === "move") {
      const mv = (state.snap && state.snap.moves) || [];
      if (!s.move && mv.length) s.move = mv[0].id;
      kids.push(row("Move", mv.length ? chips(mv.map((m) => [m.id, m.name]), s.move, (v) => { s.move = v; draw(); })
        : h("div.qe-note", "No saved moves yet - make one on the Move tab and press “Save this as my move”.")));
    }
    if (d === "custom" || d === "capture") {
      if (d === "capture") {
        const n = s.values ? Object.keys(s.values).length : 0;
        kids.push(row("From the stage", h("div.qe-note",
          s.recapture || !btn
            ? "Saves the programmer's values (colour, position, gobo…) and the effects running on these lights when you press Save."
            : `Holds ${n} light${n === 1 ? "" : "s"} and ${s.fxList.length} effect${s.fxList.length === 1 ? "" : "s"}. `,
          btn ? h("button.chip" + (s.recapture ? ".on" : ""), { type: "button",
            onclick: () => { s.recapture = !s.recapture; draw(); } }, s.recapture ? "Will re-capture on Save" : "Re-capture now") : null)));
      }
      const tog = (k, label) => h("button.chip" + (s.mix[k] ? ".on" : ""), { type: "button",
        onclick: () => { s.mix[k] = !s.mix[k]; draw(); } }, label);
      kids.push(row(d === "capture" ? "Also" : "Mix", h("div.chip-row", tog("level", "Level"), tog("dim", "Dim"),
        tog("colour", "Colour"), tog("strobe", "Strobe"), tog("kill", "Blackout"))));
      if (s.mix.level) kids.push(row("Level", slider(s.level, 5, 100, 5, (v) => { s.level = v; })));
      if (s.mix.dim) kids.push(row("Dim to", slider(s.dim, 0, 100, 5, (v) => { s.dim = v; })));
      if (s.mix.colour) kids.push(row("Colour", swatches(SWATCH, s.colour || "#ff0000", (c) => { s.colour = c; draw(); })));
      if (s.mix.strobe) kids.push(row("Strobe", slider(s.hz, 1, 20, 0.5, (v) => { s.hz = v; }, " Hz")));
      if (d === "custom" || !s.recapture) {
        kids.push(row("Effects (up to 4)", chips(FX, s.fxList, (v) => {
          s.fxList = s.fxList.includes(v) ? s.fxList.filter((x) => x !== v) : [...s.fxList, v].slice(-4);
          draw();
        }, { multi: true })));
      }
    }
    if (d === "go" || d === "release") {
      kids.push(row("Playback", chips([...Array(10)].map((_, i) => [i + 1, String(i + 1)]), +s.playback,
        (v) => { s.playback = v; draw(); })));
      if (d === "go") kids.push(row("Cue", h("input.qe-num", { type: "number", min: 1, value: s.cue, placeholder: "next",
        oninput: (e) => { s.cue = e.target.value; } })));
    }
    if (d === "preset") {
      const presets = (state.snap && state.snap.presets) || [];
      kids.push(row("Preset", presets.length ? chips(presets.map((p) => [p.n, p.name]), +s.preset, (v) => { s.preset = v; draw(); })
        : h("div.qe-note", "No presets yet - record one on the Looks tab.")));
    }
    if (d === "fog") kids.push(row("Output", slider(s.fogLevel, 5, 100, 5, (v) => { s.fogLevel = v; })));
    if (FADES.has(d)) {
      const fades = [[0, "None"], [0.5, "0.5 s"], [1, "1 s"], [2, "2 s"], [5, "5 s"]];
      kids.push(row("Fade in", chips(fades, s.fadeIn, (v) => { s.fadeIn = v; draw(); })));
      kids.push(row("Fade out", chips(fades, s.fadeOut, (v) => { s.fadeOut = v; draw(); })));
    }
    const taken = quick().buttons.filter((x) => x.key && !(btn && x.id === btn.id)).map((x) => x.key);
    const keyIn = h("input.qe-num.qe-key", { type: "text", maxlength: 1, value: s.key, placeholder: "none",
      oninput: (e) => {
        const k = e.target.value.toLowerCase();
        s.key = k && /^[a-z0-9]$/.test(k) && !RESERVED.has(k) ? k : "";
        e.target.value = s.key;
        keyNote.textContent = k && !s.key ? `“${k}” is one of the console's own keys` : s.key && taken.includes(s.key)
          ? `“${s.key}” is already on another button (both will play)` : "Press the key to play this button (hold keys hold it).";
      } });
    const keyNote = h("span.qe-note", "A letter or digit on the keyboard that plays this button.");
    kids.push(row("Keyboard key", h("div.chip-row", keyIn, keyNote)));
    // behaviour
    if (!ONE_SHOT.has(d)) {
      const modes = [["hold", "Hold", "On while pressed"], ["latch", "On / off", "Press on, press again off"],
        ["tap", "Timed shot", "One press runs it for the seconds below"]];
      kids.push(row("Press", chips(modes, s.mode, (v) => {
        s.mode = v;
        if (v === "tap" && s.seconds === "") s.seconds = fxKind ? 1 : 2;
        draw();
      })));
      kids.push(row(s.mode === "tap" ? "Runs for" : "Turn off after",
        chips([["", "Never"], [1, "1 s"], [3, "3 s"], [10, "10 s"], [30, "30 s"], [60, "1 min"]],
          s.seconds === "" ? "" : +s.seconds, (v) => { s.seconds = v; draw(); }),
        h("input.qe-num", { type: "number", min: 0.1, max: 3600, step: 0.1, value: s.seconds, placeholder: "seconds",
          oninput: (e) => { s.seconds = e.target.value; } })));
      if (!fxKind) {
        kids.push(row("One at a time with", h("div.qe-body",
          chips([["", "No"], ...tags.map((x) => [x, x])], s.exclusive, (v) => { s.exclusive = v; draw(); }),
          h("input.qe-tag", { type: "text", maxlength: 20, value: tags.includes(s.exclusive) ? "" : s.exclusive,
            placeholder: "new radio group, e.g. colours", oninput: (e) => { s.exclusive = e.target.value; } }),
          h("div.qe-note", "Buttons in the same radio group switch each other off - one colour, one movement at a time."))));
      }
    }
    body.replaceChildren(...kids);
  };
  draw();

  const save = async (toSlot = slot) => {
    const r = await run("quick_set", { page, slot: toSlot, button: toButton(s) });
    return r.ok;
  };
  const close = modal({
    title: `Button ${(quick().names || {})[page] || page}.${slot}`,
    wide: true,
    body,
    foot: [
      btn ? h("button.btn.danger", { onclick: async () => { await run("quick_set", { page, slot, clear: true }); close(); } }, "Clear") : null,
      btn ? h("button.btn", { title: "Copy this button to the next empty slot", onclick: async () => {
        const taken = new Set(quick().buttons.filter((b) => b.page === page).map((b) => b.slot));
        let to = 1;
        while (taken.has(to) && to <= (quick().slots || 24)) to++;
        if (to > (quick().slots || 24)) { toast("This page is full", "bad"); return; }
        await run("quick_move", { page, slot, to_slot: to, copy: true });
        close();
      } }, "Duplicate") : null,
      h("span.grow"),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => { if (await save()) close(); } }, "Save"),
    ],
  });
}

// A button's keyboard key: press plays it, and for a hold button letting
// go of the key lets go of it.  Runs before the console's own keys.
function keyTarget(e) {
  if (e.ctrlKey || e.metaKey || e.altKey || editing) return [];
  const t = e.target;
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return [];
  if (document.querySelector(".modal-scrim")) return [];
  const k = (e.key || "").toLowerCase();
  if (k.length !== 1) return [];
  return quick().buttons.filter((b) => b.key === k);
}

export function initQuickButtons() {
  const box = $("#qb");
  if (!box) return;
  const down = new Set();
  document.addEventListener("keydown", (e) => {
    const hits = keyTarget(e);
    if (!hits.length) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (e.repeat || down.has(e.key.toLowerCase())) return;
    down.add(e.key.toLowerCase());
    for (const b of hits) press(b, true);
  }, true);
  document.addEventListener("keyup", (e) => {
    const k = (e.key || "").toLowerCase();
    if (!down.has(k)) return;
    down.delete(k);
    for (const b of quick().buttons.filter((x) => x.key === k)) {
      if (b.mode === "hold") press(b, false); else held.delete(b.id);
    }
  }, true);
  $("#qb-edit").addEventListener("click", () => { editing = !editing; render(true); });
  $("#qb-suggest").addEventListener("click", async () => {
    if (!patch().length) { toast("Add some lights first"); return; }
    const fxOnly = patch().length && patch().every((x) => (x.map || []).some((r) => r.startsWith("fx_") || r.startsWith("laser_") || r === "fog"));
    await run(fxOnly ? "quick_fx_defaults" : "quick_defaults", { page }, { toast: true });
  });
  $("#qb-release").addEventListener("click", () => run("quick_release_all"));
  on("snapshot", () => render());
  on("lite", () => {
    const active = activeIds();
    $$("#qb-grid .qbtn[data-id]").forEach((el) => el.classList.toggle("on", active.has(el.dataset.id)));
  });
  // a held button must never stay stuck on if the window loses focus
  window.addEventListener("blur", () => {
    for (const id of [...held]) {
      const b = quick().buttons.find((x) => x.id === id);
      if (b && b.mode === "hold") press(b, false);
    }
  });
  render(true);
}
