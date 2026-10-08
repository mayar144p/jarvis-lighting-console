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
import { post } from "./api.js";
import { onNote, webMidiOn, setWebMidi, webMidiSupported, webMidiInputs } from "./webmidi.js";
import { $, $$, h, modal, toast, promptBox, menu, throttle } from "./ui.js";

// what a button does, as the editor offers it (engine kinds + two made of
// "custom": dim, and the look captured from the stage)
const DOES = [
  ["flash", "Flash", "Full on while held - keeps moving"],
  ["dim", "Dim", "Holds the lights down to a level"],
  ["colour", "Colour", "Changes their colour"],
  ["strobe", "Strobe", "Strobes them"],
  ["kill", "Blackout lights", "Turns these lights off"],
  ["fx", "Effect", "Runs one effect (circle, rainbow, chase…)"],
  ["move", "My move", "Plays one of your saved moves (Position tab)"],
  ["custom", "Mix", "Any mix of level, dim, colour, strobe and effects"],
  ["capture", "From the stage", "Whatever is on stage now: colours, positions, effects"],
  ["go", "GO", "GO on a playback"],
  ["release", "Release", "Releases a playback"],
  ["preset", "Preset", "Applies a recorded preset"],
  ["fader", "Fader", "A fader tile: the master, the Speed master, a playback or a group"],
  ["xy", "XY pad", "Drag to point these lights (pan / tilt)"],
  ["tempo", "Tempo", "Shows the BPM and the beat; tap it on the beat"],
  ["cuelist", "Cue list", "A playback's cue now and next; tap = GO"],
  ["estop", "E-stop", "Stops every special effect and laser at once, disarms"],
  ["blackout", "Blackout all", "Everything off while held"],
  ["sfx", "Fire SFX", "Confetti / CO2 / flame / sparks (needs ARM)"],
  ["fog", "Fog / haze", "Fog or haze output"],
  ["laser", "Laser", "Laser output (needs ARM)"],
  ["arm", "ARM FX", "Arms fire and lasers"],
  ["fxkill", "KILL FX", "Stops every effect, disarms"],
];
const KIND_COLOUR = { flash: "#f8fafc", strobe: "#fde047", colour: null, kill: "#64748b", fx: "#a78bfa",
  custom: "#38bdf8", move: "#a78bfa", go: "#22c55e", release: "#f97316", preset: "#38bdf8", blackout: "#ef4444",
  sfx: "#f97316", fog: "#cbd5e1", laser: "#22d3ee", arm: "#ef4444", fxkill: "#ef4444",
  fader: "#38bdf8", xy: "#a78bfa", tempo: "#fde047", cuelist: "#22c55e", estop: "#ef4444" };
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
const NO_TARGET = new Set(["go", "release", "preset", "blackout", "arm", "fxkill", "fader", "tempo", "cuelist", "estop"]);
const ONE_SHOT = new Set(["go", "release", "preset", "arm", "fxkill", "fader", "xy", "tempo", "cuelist", "estop"]);
// tiles that are a control, not a button (drawn live, see controlTile)
const CONTROL = new Set(["fader", "xy", "tempo", "cuelist"]);
let locked = false;
try { locked = localStorage.getItem("jarvis.qb.locked") === "1"; } catch { /* private window */ }
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
  play: "M7 4v16l13-8z",
  pause: "M7 4h3v16H7zM14 4h3v16h-3z",
  wave: "M2 12c2.5-5 5-5 7.5 0s5 5 7.5 0 3.5-3 5-2",
  circle: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  beam: "M9 3h6l-1 4h-4zM10 7 5 21M14 7l5 14M12 7v14",
  smoke: "M4 15a4 4 0 0 1 4-4 5 5 0 0 1 9.6 1.4A3.5 3.5 0 0 1 17 19H7a3 3 0 0 1-3-4zM8 7c0-2 2-2 2-4M13 7c0-2 2-2 2-4",
  laser: "M3 12h4M7 9v6l4-3zM11 12l10-7M11 12h10M11 12l10 7",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z",
  palette: "M12 3a9 9 0 1 0 0 18c1.1 0 1.6-.8 1.6-1.6 0-1.3-1.2-1.6-1.2-2.8 0-1 .8-1.6 1.8-1.6H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8zM7.5 11.5h.01M10 7.5h.01M15 7.5h.01",
  target: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9zM12 11.5v1",
  crown: "M3 8l4.5 4L12 5l4.5 7L21 8l-2 11H5z",
  diamond: "M6 3h12l4 6-10 12L2 9zM2 9h20M9 3l3 18M15 3l-3 18",
  zap: "M4 14 14 2l-2 8h8L10 22l2-8z",
  rocket: "M5 19c1-3 3-5 6-6M14.5 4.5C18 3 21 3 21 3s0 3-1.5 6.5L13 16l-5-5zM9 15l-3 6 3-1 1-3M8 11l-5 1 4-4h3",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  party: "M3 21l5-14 9 9zM14 3l1 2M19 5l-2 2M21 10l-2-.5M11 4.5c1 1 1 2.5 0 3.5M16.5 9c1-1 2.5-1 3.5 0",
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

// kinds that run effects, so have a speed of their own
const PACED = new Set(["fx", "move", "custom", "capture"]);
const RATES = [[0.25, "¼×"], [0.5, "½×"], [0.75, "¾×"], [1, "1×"], [1.5, "1½×"], [2, "2×"], [3, "3×"], [4, "4×"]];
const rateText = (r) => (RATES.find(([v]) => v === r) || [0, `${r}×`])[1];

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
const pendingIds = () => new Set((state.lite && state.lite.quick_pending) || quick().pending || []);
// buttons that act the moment they're touched, whatever the beat setting
const NO_QUANT = new Set(["fxkill", "arm", "estop", "tempo", "fader", "xy"]);
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

// A page's grid (Edit -> Layout): buttons across, rows, row height.
function layoutOf(p) {
  const q = quick();
  return { cols: 8, rows: 3, height: "m", ...(q.layout_default || {}), ...((q.layout || {})[p] || {}) };
}
const pageSlots = (p) => { const l = layoutOf(p); return l.cols * l.rows; };
// a tile's size in grid cells: w x h (old shows: wide / tall / big)
const SIZE_WH = { wide: [2, 1], tall: [1, 2], big: [2, 2] };
const spanOf = (b) => (b.w || b.h ? [b.w || 1, b.h || 1] : SIZE_WH[b.size] || [1, 1]);

// The slots a big tile takes besides its own, or null when one of them
// is used (then it shows at normal size).  It never runs past the row's
// end or the page's last row.
function tileFit(slot, [wantW, wantH], cols, total, used) {
  const col = (slot - 1) % cols;
  const w = Math.max(1, Math.min(wantW, cols - col));
  let t = 1;
  while (t < wantH && slot + t * cols <= total) t++;
  const take = [];
  for (let r = 0; r < t; r++) for (let c = 0; c < w; c++) if (r || c) take.push(slot + r * cols + c);
  return take.some(used) ? null : { w, t, take };
}

// How a tile looks, from the button: shape, fill, text size, details,
// blinking while on, icon only (the classes are in app.css)
function lookClasses(b) {
  return [b.shape && b.shape !== "rounded" ? "qs-" + b.shape : "", b.fill && b.fill !== "outline" ? "qf-" + b.fill : "",
    b.text && b.text !== "m" ? "qt-" + b.text : "", b.plain ? "qplain" : "", b.blink ? "qblink" : "",
    b.icon_only && b.icon ? "qicononly" : ""].filter(Boolean);
}
function tileContent(b) {
  const name = b.icon_only && b.icon ? null : h("b", b.label);
  return [h("span.qline", b.icon ? icon(b.icon) : null, name),
    b.plain || (b.icon_only && b.icon) ? null
      : h("small", NO_TARGET.has(b.kind) ? modeText(b) : `${targetText(b.target)} · ${modeText(b)}`)];
}

function render(force = false) {
  const box = $("#qb-grid");
  if (!box) return;
  const q = quick();
  const key = JSON.stringify([q.buttons, q.names, q.layout, page, editing, groups().map((g) => [g.n, g.name]),
    autoGroups().map((g) => [g.key, g.name])]);
  if (!force && key === lastKey) return;        // only redraw on a change: a click needs the same element
  lastKey = key;
  const active = activeIds();
  const byslot = new Map(q.buttons.filter((b) => b.page === page).map((b) => [b.slot, b]));
  const cells = [];
  // this page's grid: buttons past its last row are kept, not shown
  const lay = layoutOf(page), COLS = lay.cols;
  const total = COLS * lay.rows, covered = new Set(), span = new Map();
  for (const [slot] of byslot) if (slot > total) byslot.delete(slot);
  // big tiles take the empty slots next to / under them
  for (const [slot, b] of [...byslot].sort((a, c) => a[0] - c[0])) {
    const want = spanOf(b);
    if ((want[0] === 1 && want[1] === 1) || covered.has(slot)) continue;
    const fit = tileFit(slot, want, COLS, total, (x) => byslot.has(x) || covered.has(x));
    if (!fit) continue;                         // no room: stays normal size
    fit.take.forEach((x) => covered.add(x));
    span.set(slot, [fit.w, fit.t]);
  }
  // playing: the empty rows under the last button go, so the rows that
  // are used get the height (Edit shows every slot)
  let shown = total;
  if (!editing) {
    let last = 0;
    for (const [slot] of byslot) last = Math.max(last, Math.floor((slot - 1) / COLS) + (span.get(slot) || [1, 1])[1]);
    // (never fewer than three rows: two buttons don't fill the screen)
    shown = Math.min(total, Math.max(Math.min(3, lay.rows), last) * COLS);
  }
  box.style.setProperty("--qb-rows", String(Math.ceil(shown / COLS)));
  box.style.setProperty("--qb-cols", String(COLS));
  box.dataset.height = lay.height;
  const place = (el, slot) => {
    const sp = span.get(slot);
    if (!sp) return;
    el.style.gridColumn = `${(slot - 1) % COLS + 1} / span ${sp[0]}`;
    el.style.gridRow = `${Math.floor((slot - 1) / COLS) + 1} / span ${sp[1]}`;
    el.classList.add(sp[1] > 1 ? "qbig" : "qwide");
  };
  for (let slot = 1; slot <= shown; slot++) {
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
    if (CONTROL.has(b.kind) && !editing) {
      const el = controlTile(b);
      el.style.setProperty("--tint", tint);
      place(el, slot);
      cells.push(el);
      continue;
    }
    const el = h("button.qbtn" + (b.kind === "estop" ? ".qestop" : "") + (active.has(b.id) ? ".on" : ""), {
      title: `${b.label} · ${targetText(b.target)} · ${modeText(b)}${editing ? " (click to edit, drag to move, Alt-drag to copy, right-click: copy / paste the look)" : ""}`,
      "aria-label": b.label,
      dataset: { id: b.id },
      draggable: editing ? "true" : null,
    }, ...tileContent(b),
    b.keeps ? h("small.qkeeps", { title: "What this button holds: " + b.keeps }, b.keeps) : null,
    b.key || b.midi !== undefined ? h("kbd.qkey", [b.key ? b.key.toUpperCase() : "", b.midi !== undefined ? `♪${b.midi}` : ""].filter(Boolean).join(" ")) : null,
    PACED.has(b.kind) ? h("span.qrate" + (b.rate || b.free ? "" : ".one"), { title: "Speed (tap to change)" },
      (b.rate ? rateText(b.rate) : "1×") + (b.free ? " ⏵" : "")) : null);
    el.style.setProperty("--tint", tint);
    el.classList.add(...lookClasses(b));
    place(el, slot);
    if (editing) {
      el.addEventListener("click", () => editButton(slot, b));
      el.addEventListener("contextmenu", (e) => { e.preventDefault(); lookMenu(el, b); });
      el.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", JSON.stringify({ page, slot })));
      drop(el);
    } else {
      if (PACED.has(b.kind)) {
        // live speed: scroll on the tile steps through the speeds, right-click picks one
        el.addEventListener("wheel", (e) => {
          e.preventDefault();
          const cur = b.rate || 1;
          const i = RATES.findIndex(([v]) => v >= cur);
          const next = RATES[Math.max(0, Math.min(RATES.length - 1, (i < 0 ? RATES.length - 1 : i) + (e.deltaY < 0 ? 1 : -1)))][0];
          if (next !== cur) run("quick_rate", { id: b.id, rate: next });
        }, { passive: false });
        el.addEventListener("contextmenu", (e) => { e.preventDefault(); speedMenu(el, b); });
      }
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;             // right-click is the speed menu
        e.preventDefault();
        if (PACED.has(b.kind) && e.target.closest(".qrate")) { speedMenu(el, b); return; }   // the badge: speed by touch
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
  if (!editing && !byslot.size) {
    // an empty page: one calm invitation, not a wall of empty boxes
    box.style.setProperty("--qb-rows", "1");
    box.replaceChildren(h("div.qb-empty",
      h("b", `Page ${page} has no buttons yet`),
      h("span.muted.small", "Flash, strobe, colour bumps, effects and faders - one tap each, live."),
      h("div.row-btns",
        h("button.btn.primary", { onclick: () => $("#qb-suggest").click() }, "Suggest buttons for this rig"),
        h("button.btn", { onclick: () => { editing = true; render(true); } }, "Set up buttons"))));
  } else box.replaceChildren(...cells);
  renderPages();
  $("#qb-edit").classList.toggle("on", editing);
  $("#qb-edit").textContent = editing ? "Done" : "Edit";
  $("#qb-edit").hidden = locked;
  $("#qb-layout").hidden = !editing;
  $("#qb-lock").classList.toggle("on", locked);
  $("#qb-lock").textContent = locked ? "🔒 Locked" : "Lock";
  updateControls();
  const empty = !q.buttons.some((b) => b.page === page);
  $("#qb-suggest").hidden = !empty;
  const qs = $("#qb-quant");
  if (qs && document.activeElement !== qs) qs.value = String(q.quant || 0);
}

// ---------------------------------------------------------------- control tiles
// A fader, an XY pad, the tempo and a cue list live on the page as tiles:
// drawn once, their numbers kept current from the live feed.
const snapOrLite = (k) => (state.lite && state.lite[k] !== undefined ? state.lite[k] : state.snap && state.snap[k]);
function faderValue(c) {
  if (c.what === "master") return snapOrLite("master") ?? 100;
  if (c.what === "speed") return Math.round(Math.min(100, (snapOrLite("speed_master") || 1) * 50));
  if (c.what === "playback") {
    const pb = ((state.snap && state.snap.playbacks) || []).find((x) => x.n === c.n);
    return pb ? pb.level : 0;
  }
  const g = groups().find((x) => x.n === c.n);
  return g ? (g.master ?? 100) : 100;
}
const faderText = (c, v) => c.what === "speed" ? `${(v / 50).toFixed(2).replace(/0$/, "")}×` : `${Math.round(v)}%`;

function controlTile(b) {
  const el = h("div.qbtn.qctl.q" + b.kind, { dataset: { id: b.id } });
  const head = h("span.qline", b.icon ? icon(b.icon) : null, h("b", b.label));
  if (b.kind === "fader") {
    const c = b.control || { what: "master" };
    const v = faderValue(c);
    const out = h("output.qval", faderText(c, v));
    const inp = h("input.qfader-in", { type: "range", min: 0, max: 100, value: v, "aria-label": b.label });
    const send = throttle((x) => run("quick_fader", { id: b.id, level: x }, { silentError: true }), 60);
    inp.addEventListener("input", () => { out.textContent = faderText(c, +inp.value); inp.dataset.busy = "1"; send(+inp.value); });
    inp.addEventListener("change", () => { setTimeout(() => { delete inp.dataset.busy; }, 400); });
    el.append(head, out, inp);
  } else if (b.kind === "xy") {
    const dot = h("i.qxy-dot");
    const pad = h("div.qxy", dot);
    const send = throttle((pan, tilt) => run("quick_xy", { id: b.id, pan, tilt }, { silentError: true }), 70);
    let down = false;
    const at = (e) => {
      const r = pad.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
      dot.style.left = x * 100 + "%";
      dot.style.top = y * 100 + "%";
      send(Math.round(x * 255), Math.round((1 - y) * 255));
    };
    pad.addEventListener("pointerdown", (e) => { down = true; pad.setPointerCapture(e.pointerId); at(e); });
    pad.addEventListener("pointermove", (e) => { if (down) at(e); });
    pad.addEventListener("pointerup", () => { down = false; });
    el.append(head, pad);
  } else if (b.kind === "tempo") {
    el.append(head, h("output.qval.qbpm", "–"), h("small.qsub", "tap on the beat"));
    el.addEventListener("pointerdown", (e) => { e.preventDefault(); el.classList.add("down"); press(b, true); setTimeout(() => el.classList.remove("down"), 90); });
  } else if (b.kind === "cuelist") {
    const back = h("button.chip.qback", { title: "Back a cue", onclick: (e) => { e.stopPropagation(); run("cue_back", { playback: b.playback }); } }, "◀");
    el.append(head, h("output.qval.qcue", "–"), h("small.qsub.qnext", ""), back);
    el.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".qback")) return;
      e.preventDefault(); el.classList.add("down"); press(b, true); setTimeout(() => el.classList.remove("down"), 120);
    });
  }
  return el;
}

function updateControls() {
  for (const el of $$("#qb-grid .qctl")) {
    const b = quick().buttons.find((x) => x.id === el.dataset.id);
    if (!b) continue;
    if (b.kind === "fader") {
      const inp = el.querySelector(".qfader-in");
      if (inp.dataset.busy) continue;
      const v = faderValue(b.control || { what: "master" });
      inp.value = v;
      el.querySelector(".qval").textContent = faderText(b.control || { what: "master" }, v);
    } else if (b.kind === "tempo") {
      const t = snapOrLite("tempo") || {};
      el.querySelector(".qbpm").textContent = t.bpm ? `${t.bpm.toFixed(t.bpm % 1 ? 1 : 0)} BPM` : "–";
    } else if (b.kind === "cuelist") {
      const pb = ((state.snap && state.snap.playbacks) || []).find((x) => x.n === b.playback) || { stack: [] };
      const cur = pb.index >= 0 ? pb.stack[pb.index] : null, nxt = pb.stack[(pb.index ?? -1) + 1];
      el.querySelector(".qcue").textContent = cur && pb.active ? `${cur.n} · ${cur.name}` : pb.stack.length ? "ready" : "no cues";
      el.querySelector(".qnext").textContent = nxt ? `next: ${nxt.n} · ${nxt.name}` : pb.stack.length ? "last cue" : "";
    }
  }
}

// ------------------------------------------------------------- full screen
function setFull(on) {
  const box = $("#qb");
  box.classList.toggle("qb-full", on);
  document.body.classList.toggle("qb-fullscreen", on);
  $("#qb-full").textContent = on ? "Exit full screen" : "⛶ Full screen";
  if (on && document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
  if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
  render(true);
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
    quant: b && b.quant !== undefined ? b.quant : "",
    target: b ? { ...(b.target || { all: true }) } : { all: true },
    colour: b ? b.colour || null : null, level: b && b.level !== undefined ? b.level : 100,
    dim: b && b.dim !== undefined ? b.dim : 30, hz: b ? b.hz || 10 : 10,
    fx: b && b.fx ? b.fx : "rainbow", params: b && b.params ? { ...b.params } : {},
    mix: { level: b && b.level !== undefined, dim: b && b.dim !== undefined, colour: !!(b && b.colour),
      strobe: !!(b && b.hz), kill: !!(b && b.kill) },
    fxList: b && b.fx_list ? b.fx_list.map((f) => f.name) : [], fxListParams: b && b.fx_list ? b.fx_list : [],
    values: b ? b.values || null : null, attrs: b ? b.attrs || null : null, recapture: !b,
    move: b ? b.move || "" : "", rate: b && b.rate ? b.rate : 1, free: !!(b && b.free), icon: b ? b.icon || "" : "",
    w: b ? spanOf(b)[0] : 1, h: b ? spanOf(b)[1] : 1, shape: (b && b.shape) || "rounded", fill: (b && b.fill) || "outline",
    text: (b && b.text) || "m", plain: !!(b && b.plain), blink: !!(b && b.blink), iconOnly: !!(b && b.icon_only),
    midi: b && b.midi !== undefined ? b.midi : "",
    fadeIn: b && b.fade_in ? b.fade_in : 0, fadeOut: b && b.fade_out ? b.fade_out : 0, key: b ? b.key || "" : "",
    playback: b ? b.playback || 1 : 1, cue: b ? b.cue || "" : "", preset: b ? b.preset || "" : "",
    fogLevel: b && b.kind === "fog" ? b.level || 100 : 100,
    control: b && b.control ? { ...b.control } : { what: "master" },
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
  if (s.w !== 1 || s.h !== 1) { b.w = s.w; b.h = s.h; }
  if (s.shape !== "rounded") b.shape = s.shape;
  if (s.fill !== "outline") b.fill = s.fill;
  if (s.text !== "m") b.text = s.text;
  if (s.plain) b.plain = true;
  if (s.blink) b.blink = true;
  if (s.iconOnly && s.icon) b.icon_only = true;
  if (PACED.has(d)) { if (s.rate !== 1) b.rate = s.rate; if (s.free) b.free = true; }
  if (s.icon) b.icon = s.icon;
  if (s.quant !== "" && !NO_QUANT.has(kind)) b.quant = +s.quant;
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
  if (s.midi !== "" && s.midi !== null) b.midi = +s.midi;
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
  if (d === "fader") b.control = s.control.what === "playback" || s.control.what === "group" ? { what: s.control.what, n: +s.control.n || 1 } : { what: s.control.what };
  if (d === "cuelist") b.playback = +s.playback || 1;
  if (["sfx", "fog", "laser"].includes(d) && s.seconds !== "") b.seconds = +s.seconds;
  if (d === "fog") b.level = s.fogLevel;
  return b;
}

// The Look part of the editor: a live preview of the tile, then its
// colour, size, shape, fill, text, details, blinking and icon.
const SHAPES = [["rounded", "Rounded"], ["square", "Square"], ["pill", "Pill"], ["circle", "Circle"]];
const FILLS = [["outline", "Outline", "Dark with a coloured edge; fills when on"], ["solid", "Solid", "Always filled with its colour"],
  ["glow", "Glow", "A soft glow in its colour all the time"]];
const TEXTS = [["s", "S"], ["m", "M"], ["l", "L"], ["xl", "XL"]];
function lookSection(s, slot, btn, draw) {
  const lay = layoutOf(page);
  const b = { ...toButton(s), id: "preview" };
  const tint = b.tint || b.colour || KIND_COLOUR[b.kind] || "#94a3b8";
  const prev = (on) => {
    const el = h("div.qbtn" + (on ? ".on" : ""), { "aria-hidden": "true" }, ...tileContent(b));
    el.style.setProperty("--tint", tint);
    el.classList.add(...lookClasses(b), ...(s.h > 1 ? ["qbig"] : s.w > 1 ? ["qwide"] : []));
    el.style.width = `${Math.min(s.w, 3) * 92 + (Math.min(s.w, 3) - 1) * 6}px`;
    el.style.height = `${s.h * ({ s: 44, m: 56, l: 76, xl: 100 }[lay.height] || 56) + (s.h - 1) * 6}px`;
    return el;
  };
  const used = (x) => quick().buttons.some((o) => o.page === page && o.slot === x && !(btn && o.id === btn.id));
  const fit = tileFit(slot, [s.w, s.h], lay.cols, lay.cols * lay.rows, used);
  const warn = (s.w > 1 || s.h > 1) && (!fit || fit.w < s.w || fit.t < s.h)
    ? h("span.qe-note.warn", !fit ? "The space it needs is taken: it shows at normal size until it is free."
      : "It reaches the edge of the page: it shows as big as fits.")
    : null;
  const toggle = (k, label, title) => h("button.chip" + (s[k] ? ".on" : ""), { type: "button", title,
    "aria-pressed": String(!!s[k]), onclick: () => { s[k] = !s[k]; draw(); } }, label);
  return h("div.qe-look",
    h("div.qe-preview", h("div.qe-prev-pair", prev(false), h("span.muted.small", "off")),
      h("div.qe-prev-pair", prev(true), h("span.muted.small", "on"))),
    row("Colour", swatches(TINTS, s.tint, (c) => { s.tint = c; draw(); }, { none: "Auto" })),
    row("Size", h("div.qe-body",
      h("div.chip-row", h("span.qe-mini", "Across"), chips([[1, "1"], [2, "2"], [3, "3"], [4, "4"]], s.w, (v) => { s.w = v; draw(); })),
      h("div.chip-row", h("span.qe-mini", "Down"), chips([[1, "1"], [2, "2"], [3, "3"]], s.h, (v) => { s.h = v; draw(); })),
      warn)),
    row("Shape", chips(SHAPES, s.shape, (v) => { s.shape = v; draw(); })),
    row("Fill", chips(FILLS, s.fill, (v) => { s.fill = v; draw(); })),
    row("Text", h("div.chip-row", chips(TEXTS, s.text, (v) => { s.text = v; draw(); }),
      toggle("plain", "Name only", "Hide the small line under the name (what it does, to which lights)"),
      toggle("blink", "Blink while on", "Blinks slowly while it is on, so it can't be missed"))),
    row("Icon", h("div.qe-body",
      h("div.qe-icons",
        h("button.qe-icon" + (!s.icon ? ".on" : ""), { type: "button", title: "No icon", onclick: () => { s.icon = ""; s.iconOnly = false; draw(); } }, "None"),
        ...Object.keys(ICONS).map((k) => h("button.qe-icon" + (s.icon === k ? ".on" : ""),
          { type: "button", title: k, "aria-label": k, onclick: () => { s.icon = k; draw(); } }, icon(k)))),
      s.icon ? h("div.chip-row", toggle("iconOnly", "Icon only", "Show the icon big, without the name (the name shows on hover)")) : null)));
}

// Edit: right-click a button to copy its look onto others
let lookClip = null;
const lookOf = (b) => Object.fromEntries(["tint", "shape", "fill", "text", "plain", "blink", "icon_only", "icon", "w", "h", "size"]
  .map((k) => [k, b[k] ?? null]));
function lookMenu(el, b) {
  menu(el, [
    { label: "Copy look", run: () => { lookClip = lookOf(b); toast(`Look of “${b.label}” copied`, "ok"); } },
    { label: "Paste look", disabled: !lookClip, run: () => run("quick_style", { ids: [b.id], style: lookClip }) },
    { label: "Paste look on every button of this page", disabled: !lookClip,
      run: () => run("quick_style", { page, style: lookClip }) },
  ]);
}

// Edit -> Layout: this page's grid
function editLayout() {
  const cur = layoutOf(page);
  const s = { ...cur, all: false };
  const body = h("div.qe");
  const q = quick();
  const draw = () => {
    const maxRows = Math.floor((q.slots || 48) / s.cols);
    if (s.rows > maxRows) s.rows = maxRows;
    const past = q.buttons.filter((b) => b.page === page && b.slot > s.cols * s.rows).length;
    body.replaceChildren(
      row("Buttons across", chips([4, 5, 6, 8, 10, 12].map((n) => [n, String(n)]), s.cols, (v) => { s.cols = v; draw(); })),
      row("Rows", chips(Array.from({ length: Math.min(8, maxRows) }, (_, i) => [i + 1, String(i + 1)]), s.rows, (v) => { s.rows = v; draw(); })),
      row("Row height", chips([["s", "Small"], ["m", "Medium"], ["l", "Large"], ["xl", "Extra large"]], s.height, (v) => { s.height = v; draw(); })),
      row("", h("label.check.small", h("input", { type: "checkbox", checked: s.all, onchange: (e) => { s.all = e.target.checked; } }), " Every page")),
      h("div.qe-note", `${s.cols * s.rows} buttons on the page. Fewer across = bigger buttons. Buttons keep their numbers and flow into the new grid in order.`),
      past ? h("div.qe-note.warn", `${past} button(s) past the last row are kept but not shown - add a row to see them.`) : null);
  };
  draw();
  const close = modal({
    title: `Layout of page ${(q.names || {})[page] || page}`,
    body,
    foot: [h("span.grow"), h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => {
        const r = await run("quick_layout", { page, cols: s.cols, rows: s.rows, height: s.height, all_pages: s.all });
        if (r.ok) close();
      } }, "Save")],
  });
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
        oninput: (e) => { s.label = e.target.value; body.querySelectorAll(".qe-preview .qline b").forEach((x) => { x.textContent = s.label || "Button"; }); } })),
      lookSection(s, slot, btn, draw),
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
    if (d === "fader") {
      const c = s.control;
      kids.push(row("Moves", chips([["master", "Grand master"], ["speed", "Speed master"], ["playback", "A playback"], ["group", "A group"]], c.what,
        (v) => { s.control = { what: v, n: v === "group" ? (groups()[0] || { n: 1 }).n : 1 }; draw(); })));
      if (c.what === "playback") kids.push(row("Playback", chips(((state.snap && state.snap.playbacks) || []).slice(0, 10).map((pb) => [pb.n, pb.name || `PB${pb.n}`]), c.n, (v) => { c.n = v; draw(); })));
      if (c.what === "group") kids.push(row("Group", groups().length ? chips(groups().map((g) => [g.n, g.name]), c.n, (v) => { c.n = v; draw(); })
        : h("div.qe-note", "No groups yet - make one from a selection (Group).")));
    }
    if (d === "cuelist") kids.push(row("Playback", chips(((state.snap && state.snap.playbacks) || []).slice(0, 10).map((pb) => [pb.n, pb.name || `PB${pb.n}`]), s.playback, (v) => { s.playback = v; draw(); })));
    if (d === "colour") kids.push(row("Colour", swatches(SWATCH, s.colour || "#ffffff", (c) => { s.colour = c; draw(); })));
    if (d === "strobe") {
      kids.push(row("Speed", chips([[2, "Slow"], [5, "Medium"], [10, "Fast"], [18, "Very fast"]], s.hz,
        (v) => { s.hz = v; draw(); }), slider(s.hz, 1, 20, 0.5, (v) => { s.hz = v; }, " Hz")));
      kids.push(row("Colour", swatches(SWATCH, s.colour, (c) => { s.colour = c; draw(); }, { none: "Keep their colour" })));
    }
    if (d === "fx") {
      kids.push(row("Effect", chips(FX, s.fx, (v) => { s.fx = v; s.params = {}; draw(); })));
      // the show's own step effects (FX tab -> Step effects)
      const steps = (state.snap && state.snap.step_fx) || [];
      if (steps.length) {
        kids.push(row("Or a step effect", chips(steps.map((f) => [`step:${f.id}`, f.name]), s.fx, (v) => { s.fx = v; s.params = {}; draw(); })));
      }
      if (s.fx === "roam" || s.fx.startsWith("shape:")) {
        const p = s.params || {};
        const what = s.fx === "roam" ? `a roam over ${(p.zones || []).length} zone(s)` : "a shape";
        kids.push(row("Movement", h("div.qe-note", `Made on the Position tab: ${what}, its speed and size${p.beats ? `, a round per ${p.beats} beat(s)` : ""}. `
          + "To change it, set it up again on the Position tab and press Make a button - or pick another effect above.")));
      } else if (s.fx.startsWith("step:")) {
        kids.push(row("On the beat", chips([[0, "Its own times"], [1, "A round a beat"], [2, "2 beats"], [4, "A bar"], [8, "2 bars"]],
          s.params.beats ?? 0, (v) => { s.params = v ? { beats: v } : {}; draw(); })));
      } else if (MOVES.has(s.fx)) {
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
        : h("div.qe-note", "No saved moves yet - make one on the Position tab and press “Save this as my move”.")));
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
    if (PACED.has(d)) {
      kids.push(row("Button speed", h("div.chip-row", chips(RATES, s.rate, (v) => { s.rate = v; draw(); }),
        h("button.chip" + (s.free ? ".on" : ""), { title: "Ignore the Speed master: this button keeps its own speed",
          onclick: () => { s.free = !s.free; draw(); } }, "Own speed"),
        h("span.qe-note", "Multiplies its effects' speed. Live: scroll on the tile, or right-click it."))));
    }
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
    const midiIn = h("input.qe-num.qe-key", { type: "number", min: 0, max: 127, value: s.midi, placeholder: "—",
      oninput: (e) => { const v = e.target.value; s.midi = v === "" ? "" : Math.max(0, Math.min(127, Math.round(+v))); } });
    const midiNote = h("span.qe-note", "A pad or key on a MIDI controller. Press Learn, then hit the pad.");
    const learn = h("button.chip", { onclick: () => learnMidi(learn, midiIn, midiNote, (n) => { s.midi = n; }) }, "Learn");
    kids.push(row("MIDI note", h("div.chip-row", midiIn, learn, midiNote)));
    if (!NO_QUANT.has(d)) {
      kids.push(row("Fires", chips([["", "Like the page"], [0, "As pressed"], [1, "On the beat"], [4, "On the bar"]],
        s.quant, (v) => { s.quant = v; draw(); }),
      h("span.qe-note", "On the beat: a press waits for the next beat (or bar) of the tempo, so a strobe hit or a GO lands on it.")));
    }
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
        while (taken.has(to) && to <= pageSlots(page)) to++;
        if (to > pageSlots(page)) { toast("This page is full", "bad"); return; }
        await run("quick_move", { page, slot, to_slot: to, copy: true });
        close();
      } }, "Duplicate") : null,
      h("span.grow"),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => { if (await save()) close(); } }, "Save"),
    ],
  });
}

function speedMenu(el, b) {
  menu(el, [...RATES.map(([v, t]) => ({ label: `${t} speed${(b.rate || 1) === v ? " ✓" : ""}`,
    run: () => run("quick_rate", { id: b.id, rate: v }) })), "-",
  { label: b.free ? "Follow the Speed master" : "Own speed (ignore the Speed master)",
    run: () => run("quick_rate", { id: b.id, free: !b.free }) }]);
}

// MIDI learn: wait up to 10 s for a note, from the desk's MIDI input or
// from a controller on this device (browser MIDI).
async function midiStatus() {
  const d = await post("/api/console/midi", {});
  return (d.result && d.result.midi) || d.midi || {};
}
async function learnMidi(btn, input, note, set) {
  const st = await midiStatus().catch(() => ({}));
  const desk = st.enabled && st.open;
  if (!desk && !webMidiOn() && webMidiSupported()) await setWebMidi(true);   // a controller on this device
  if (!desk && !webMidiOn()) {
    note.textContent = st.enabled ? `${st.error || "No MIDI device found"} - plug one in, then pick it in Settings.`
      : "MIDI is off on this desk (MIDI_ENABLED in .env), and this browser has no MIDI.";
    note.classList.add("warn");
    return;
  }
  const since = (st.last_note && st.last_note.at) || 0;
  let heard = null;
  const off = onNote((ev) => { if (ev.on) heard = ev; });
  btn.textContent = "Hit a pad…";
  btn.classList.add("on");
  const end = Date.now() + 10000;
  while (Date.now() < end && btn.isConnected && !heard) {
    await new Promise((r) => setTimeout(r, 250));
    if (desk && !heard) {
      const cur = await midiStatus().catch(() => ({}));
      if (cur.last_note && cur.last_note.at > since) heard = cur.last_note;
    }
  }
  off();
  if (heard) {
    set(heard.number);
    input.value = heard.number;
    note.textContent = `Note ${heard.number} (channel ${heard.channel})`;
    note.classList.remove("warn");
  } else if (!desk && !webMidiInputs().length) {
    note.textContent = "No MIDI controller found on this device.";
    note.classList.add("warn");
  }
  btn.textContent = "Learn";
  btn.classList.remove("on");
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
  // a controller on this device plays the buttons given its notes
  onNote((ev) => {
    if (editing) return;
    for (const b of quick().buttons.filter((x) => x.midi === ev.number)) {
      if (ev.on) press(b, true);
      else if (b.mode === "hold") press(b, false);
      else held.delete(b.id);
    }
  });
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
  $("#qb-layout").addEventListener("click", editLayout);
  $("#qb-full").addEventListener("click", () => setFull(!$("#qb").classList.contains("qb-full")));
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && $("#qb").classList.contains("qb-full")) setFull(false); });
  $("#qb-lock").addEventListener("click", () => {
    locked = !locked;
    if (locked) editing = false;
    try { localStorage.setItem("jarvis.qb.locked", locked ? "1" : "0"); } catch { /* private window */ }
    toast(locked ? "Layout locked on this device - buttons play, nothing can be moved or edited" : "Layout unlocked", "ok");
    render(true);
  });
  $("#qb-suggest").addEventListener("click", async () => {
    if (!patch().length) { toast("Add some lights first"); return; }
    const fxOnly = patch().length && patch().every((x) => (x.map || []).some((r) => r.startsWith("fx_") || r.startsWith("laser_") || r === "fog"));
    await run(fxOnly ? "quick_fx_defaults" : "quick_defaults", { page }, { toast: true });
  });
  $("#qb-release").addEventListener("click", () => run("quick_release_all"));
  $("#qb-quant").addEventListener("change", (e) => run("quick_quant", { beats: +e.target.value }, { toast: true }));
  on("snapshot", () => render());
  on("lite", () => {
    const active = activeIds();
    const waiting = pendingIds();
    $$("#qb-grid .qbtn[data-id]").forEach((el) => {
      el.classList.toggle("on", active.has(el.dataset.id));
      el.classList.toggle("pending", waiting.has(el.dataset.id));     // waiting for its beat
    });
    updateControls();
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
