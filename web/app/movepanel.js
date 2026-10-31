// The Move tab: point lights with one tap, nudge them while watching the
// real beam, run a movement that stays where they are aimed, slow
// everything with the Speed master, and give one light its own range.
//
// Built from the venue (spots come from its dance floor and zones) and the
// engine's movement effects (app/motion.py): a shape of a given size around
// the aim, fitted inside each light's range, never faster than its motor.
import { beatSelect, spaceSelect, fxKnobs } from "./programmer.js";
import { knob, TURN_PRESETS } from "./speedpick.js";
import { tap as tapTempo, tempo } from "./tempo.js";
import { get } from "./api.js";
import { state, on, selectionHeads } from "./store.js";
import { run } from "./actions.js";
import { $, h, toast, promptBox, confirmBox, menu } from "./ui.js";
import { openCueDialog } from "./dialogs.js";
import { focusedPlayback } from "./playbacks.js";
import { aimBlock, followGlide } from "./aimfollow.js";
import { shapesRow, setShapeKnobs, shapes } from "./shapeeditor.js";

const MOVES = [
  ["circle", "Circle", "↻"], ["pan_sweep", "Sweep", "↔"], ["tilt_bounce", "Bounce", "↕"],
  ["figure_eight", "Figure 8", "∞"], ["fan_pan", "Fan", "⋔"], ["range", "A → B", "⇄"],
];
const MOVE_KINDS = new Set([...MOVES.map((m) => m[0]), "shape"]);
const SIZES = [["S", 10], ["M", 20], ["L", 40]];

// the knobs, remembered while the page is open
const knobs = { direction: 1, arc: 360, size: 20, secs: 8, spread: 0, lock: 0, across: false, zone: "",
  // A → B: degrees from the middle of each light's travel; bounce / loop / once
  panA: -45, panB: 45, tiltA: 0, tiltB: 0, mode: 0, fullPan: false, fullTilt: false, cells: {}, tiltSecs: 0, panSecs: 0, mult: 1 };
// EACH LIGHT'S OWN: selecting another light shows ITS settings (from the
// movement running on it, else the ones last used for it, else the
// defaults) - one set for the whole desk showed the last light's numbers
const KNOB_DEFAULTS = JSON.parse(JSON.stringify(knobs));
const PER_LIGHT = ["direction", "arc", "size", "secs", "spread", "lock", "across", "panA", "panB", "tiltA", "tiltB",
  "mode", "fullPan", "fullTilt", "cells", "tiltSecs", "panSecs", "mult"];
let perLight = (() => { try { return JSON.parse(localStorage.getItem("haze.moveKnobs") || "{}"); } catch { return {}; } })();
let knobsFor = "";
function rememberKnobs() {
  const saved = {};
  for (const k of PER_LIGHT) saved[k] = JSON.parse(JSON.stringify(knobs[k]));
  for (const x of movers()) perLight[x.head_no] = saved;
  try { localStorage.setItem("haze.moveKnobs", JSON.stringify(perLight)); } catch { /* private window */ }
}
function loadKnobs(sel) {
  const first = sel[0];
  const run = first && running().find((f) => f.heads.includes(first.head_no) && f.lib === "range");
  const mem = first && perLight[first.head_no];
  for (const k of PER_LIGHT) knobs[k] = JSON.parse(JSON.stringify(mem && mem[k] !== undefined ? mem[k] : KNOB_DEFAULTS[k]));
  if (run && !mem) {
    // a movement already running on it (a button's, an older desk's): its own numbers
    const p = run.params || {};
    Object.assign(knobs, { panA: p.pan_a ?? knobs.panA, panB: p.pan_b ?? knobs.panB, tiltA: p.tilt_a ?? knobs.tiltA,
      tiltB: p.tilt_b ?? knobs.tiltB, mode: p.mode ?? knobs.mode, fullPan: !!p.full_pan, fullTilt: !!p.full_tilt,
      secs: p.speed ? Math.round(10 / p.speed) / 10 : knobs.secs, mult: 1,
      tiltSecs: p.tilt_speed ? Math.round(10 / p.tilt_speed) / 10 : 0, panSecs: p.pan_speed ? Math.round(10 / p.pan_speed) / 10 : 0,
      spread: p.spread ?? knobs.spread, cells: p.cells || {}, across: !!p.across });
  }
}
let fine = false;
let lastKey = "";
let masterHeldUntil = 0;
let own = { key: "", attrs: [] };         // the selection's own motor channels (Wave spin...)

// ---------------------------------------------------- a light's own moves
// A light's own motor channels (a Wave 360's continuous pan rotation, its
// built-in tilt programs, their speeds) arrive as ranges like "1-127
// counter-clockwise with decreasing speed".  Here they become ↺ ■ ↻ and a
// slow-to-fast slider; Haze works out the value inside the right range.
const OWN_RE = /rotat|spin|auto ?tilt|built.?in|program|macro|movement|moves?\b/i;
const slowFirst = (text) => !/decreas|fast\s*(to|->|→|-)\s*slow/i.test(text || "");

function inRange(slot, speed01) {
  const s = Math.max(0, Math.min(1, speed01));
  const f = slowFirst(slot.name) ? s : 1 - s;
  return Math.round(slot.from + f * (slot.to - slot.from));
}

async function loadOwn() {
  const heads = movers().map((x) => x.head_no);
  const key = heads.join(",");
  if (!heads.length) { own = { key, attrs: [] }; return; }
  if (own.key === key) return;
  try {
    const d = await get("/api/console/attributes?heads=" + key);
    const attrs = (d.pages || []).flatMap((p) => p.attrs || [])
      .filter((a) => /^aux\d+$/.test(a.role) && OWN_RE.test(a.name || ""));
    own = { key, attrs };
  } catch {
    own = { key, attrs: [] };
  }
  render(true);
}

// send a light's own channel and remember it, so the control shows it at once
// (redraw false while a slider is being dragged - rebuilding it would drop the drag)
function setOwn(a, value, redraw = true) {
  a.value = value;
  if (redraw) { own.rev = (own.rev || 0) + 1; render(true); }
  return run("set_attribute", { attribute: a.role, value });
}

function spinControl(a) {
  const slots = a.slots || [];
  const ccw = slots.find((s) => /counter|anti|ccw|left/i.test(s.name));
  const cw = slots.find((s) => s !== ccw && /clockwise|\bcw\b|right/i.test(s.name));
  const stop = slots.find((s) => /no function|stop|off|^0$/i.test(s.name));
  if (!ccw || !cw) return null;
  const cur = a.value;
  const dir = cur == null ? 0 : cur >= ccw.from && cur <= ccw.to ? -1 : cur >= cw.from && cur <= cw.to ? 1 : 0;
  const slot = dir < 0 ? ccw : dir > 0 ? cw : null;
  const speed0 = slot && cur != null ? (slowFirst(slot.name) ? (cur - slot.from) : (slot.to - cur)) / ((slot.to - slot.from) || 1) : 0.3;
  const speed = h("input", { type: "range", min: 0, max: 100, value: Math.round(speed0 * 100), title: "Spin speed" });
  const send = (d, redraw = true) => {
    if (d === 0) return setOwn(a, stop ? stop.from : 0);
    return setOwn(a, inRange(d < 0 ? ccw : cw, +speed.value / 100), redraw);
  };
  let t = 0;
  speed.addEventListener("input", () => {
    clearTimeout(t);
    const d = dir;
    if (d) t = setTimeout(() => send(d, false), 60);
  });
  const b = (label, d, title) => h("button.chip" + (dir === d ? ".on" : ""), { title, onclick: () => send(d) }, label);
  return h("div.mv-row", h("span.k", "Spin"),
    h("span.chip-row", b("↺", -1, "Spin counter-clockwise"), b("■ stop", 0, "Stop spinning"), b("↻", 1, "Spin clockwise")),
    h("span.muted.small", "slow"), speed, h("span.muted.small", "fast"));
}

function speedControl(a) {
  const full = a.full || 255;
  const input = h("input", { type: "range", min: 0, max: full, value: a.value ?? 0 });
  let t = 0;
  input.addEventListener("input", () => {
    clearTimeout(t);
    const v = +input.value;
    t = setTimeout(() => { a.value = v; run("set_attribute", { attribute: a.role, value: v }, { silentError: true }); }, 60);
  });
  return h("div.mv-row", h("span.k", (a.name || "").replace(/speed/i, "").trim() || "Speed"),
    h("span.muted.small", "slow"), input, h("span.muted.small", "fast"));
}

function ownBlock() {
  if (!own.attrs.length) return null;
  const rows = [];
  for (const a of own.attrs) {
    const spin = /rotat|spin/i.test(a.name || "") ? spinControl(a) : null;
    if (spin) { rows.push(spin); continue; }
    if (/speed/i.test(a.name || "")) { rows.push(speedControl(a)); continue; }
    if ((a.slots || []).length) {
      rows.push(h("div.mv-row", h("span.k", a.name), h("select.select", {
        onchange: (e) => setOwn(a, +e.target.value),
      }, ...(a.slots || []).map((s) => h("option", { value: s.value, selected: a.value != null && a.value >= s.from && a.value <= s.to }, s.name)))));
    }
  }
  return rows.length ? section("This light's own moves", ...rows,
    h("p.muted.small", "The light's own motor programs. With 'Movement stays on the floor', keep its tilt pointing down so a spin stays on the floor.")) : null;
}

const movers = () => selectionHeads().filter((x) => (x.map || []).includes("pan") || (x.map || []).includes("tilt"));
// what the selected lights can do: some only tilt (a CO2 jet, a bar), some only pan
const axes = () => {
  const ms = movers();
  return { pan: ms.some((x) => x.map.includes("pan")), tilt: ms.some((x) => x.map.includes("tilt")),
    both: ms.some((x) => x.map.includes("pan") && x.map.includes("tilt")) };
};
// a movement needs these axes
const NEEDS = { circle: "both", figure_eight: "both", pan_sweep: "pan", fan_pan: "pan", tilt_bounce: "tilt", range: null };
const canRun = (name, ax = axes()) => { const n = NEEDS[name]; return !n || (n === "both" ? ax.both : ax[n]); };
const lasers = () => selectionHeads().filter((x) => (x.map || []).some((r) => r === "laser_on" || r === "laser_y" || r.startsWith("laser_beam")));
const EYE_SAFE_M = 3;                      // beams stay at least this high over a crowd
// the programmer's own movements - the only ones the Move tab changes: a
// button's (or a cue's, the timeline's) is that button's, and a knob turned
// here must never replace or retune it
const running = () => ((state.snap && state.snap.fx) || []).filter((f) => MOVE_KINDS.has(f.lib) && (f.from || "programmer") === "programmer");
const othersOn = (heads) => ((state.snap && state.snap.fx) || []).filter((f) => MOVE_KINDS.has(f.lib)
  && (f.from || "programmer") !== "programmer" && f.heads.some((n) => heads.has(n)));
const params = () => ({
  speed: +(knobs.mult / knobs.secs).toFixed(4), size: knobs.size, arc: knobs.arc,
  direction: knobs.direction, spread: knobs.spread, lock: knobs.lock,
  pan_a: knobs.panA, pan_b: knobs.panB, tilt_a: knobs.tiltA, tilt_b: knobs.tiltB, mode: knobs.mode,
  full_pan: knobs.fullPan ? 1 : 0, full_tilt: knobs.fullTilt ? 1 : 0,
  tilt_speed: knobs.tiltSecs ? +(knobs.mult / knobs.tiltSecs).toFixed(4) : 0,
  pan_speed: knobs.panSecs ? +(knobs.mult / knobs.panSecs).toFixed(4) : 0,
  ...(Object.keys(knobs.cells).length ? { cells: knobs.cells } : {}),
  ...(knobs.zone && zoneKinds().some(([k]) => k === knobs.zone) ? { zone: knobs.zone } : {}),
});
// the room's zones (dance floor, DJ booth...) a movement can be kept on
export const zoneKinds = () => [...new Map((((state.snap && state.snap.venue) || {}).zones || [])
  .map((z) => [z.kind, z.name || z.kind])).entries()];
const zoneName = () => ((zoneKinds().find(([k]) => k === knobs.zone) || [])[1] || "zone").toLowerCase();
// "· on the dance floor" for a running movement kept on a zone (by its id)
const onZone = (f) => {
  const z = f.params && f.params.zone && ((((state.snap && state.snap.venue) || {}).zones || []).find((x) => x.id === f.params.zone));
  return z ? ` · on the ${(z.name || z.kind).toLowerCase()}` : "";
};

let lastLib = "circle";
setShapeKnobs(() => ({ ...params(), zoneName: knobs.zone ? zoneName() : "" }));

async function startMove(name) {
  lastLib = name;
  knobs.showAB = name === "range";
  rememberKnobs();
  const heads = movers().map((x) => x.head_no);
  if (!heads.length) { toast("Select a moving light first"); return; }
  for (const f of running()) {
    if (f.heads.some((n) => heads.includes(n)) && (f.lib === "shape" || f.lib === "roam")) await run("stop_fx", { id: f.id }, { silentError: true });
  }
  // replace: the desk swaps out the movement already on these lights (never two at once)
  run("run_fx", { name, params: params(), heads, across: knobs.across && multiTilt(), replace: true }, { toast: true });
}

// a Wave 360 has four tilts: a movement can run across them (a tilt wave)
const multiTilt = () => movers().some((x) => (x.map || []).filter((r) => r === "tilt").length > 1);

// a knob changed while a movement runs on these lights: restart it with the new knobs
// (one at a time: two quick changes must not both start one)
let reapplying = Promise.resolve();
function reapply() { rememberKnobs(); reapplying = reapplying.then(reapplyNow, reapplyNow); return reapplying; }
async function reapplyNow() {
  const heads = movers().map((x) => x.head_no);
  const mine = running().filter((f) => f.heads.some((n) => heads.includes(n)));
  for (const f of mine) {
    await run("stop_fx", { id: f.id }, { silentError: true });
    if (f.lib === "shape") {
      const p = params();
      await run("run_shape", { id: f.params.shape, heads: f.heads, speed: p.speed, size: p.size, spread: p.spread, direction: p.direction, zone: p.zone }, { silentError: true });
      continue;
    }
    await run("run_fx", { name: f.lib, params: params(), heads: f.heads, across: knobs.across && multiTilt(), replace: true }, { silentError: true });
  }
}

function chips(items, isOn, pick, cls = "") {
  return items.map(([label, value, title]) => h("button.chip" + cls + (isOn(value) ? ".on" : ""), {
    title: title || "", onclick: () => { pick(value); render(true); reapply(); },
  }, label));
}

function section(title, ...kids) {
  return h("div.mv-sec", h("h3", title), ...kids.filter(Boolean));
}

function spotsBlock() {
  const spots = (state.snap && state.snap.move_spots) || [];
  if (!spots.length) {
    return h("p.muted.small", "Draw a dance floor (and DJ / stage / bar zones) in the Venue tab and they appear here as one-tap spots.");
  }
  const floor = spots.some((s) => s.key === "floor");
  return h("div.mv-spotbox",
    h("div.mv-spots", ...spots.map((s, i) => {
      // two zones with the same name (two DJ zones): numbered, not "DJ DJ"
      const same = spots.filter((x) => x.label === s.label);
      const label = same.length > 1 ? `${s.label} ${same.indexOf(s) + 1}` : s.label;
      return h("button.btn.mv-spot", {
        title: `Every selected mover points at ${label}, each from where it hangs`,
        onclick: () => run("aim_spot", { spot: s.key, glide: followGlide() }, { toast: true }),
      }, label);
    })),
    floor ? h("div.mv-row", h("span.k", "Formation"),
      h("button.chip", { title: "Spread the lights across the dance floor", onclick: () => run("aim_spot", { formation: "fan", glide: followGlide() }, { toast: true }) }, "Fan out"),
      h("button.chip", { title: "Left lights to the right side, right lights to the left", onclick: () => run("aim_spot", { formation: "cross", glide: followGlide() }, { toast: true }) }, "Cross"),
      h("button.chip", { title: "Left half to the left, right half to the right", onclick: () => run("aim_spot", { formation: "split", glide: followGlide() }, { toast: true }) }, "Split")) : null);
}

// Roam: the selected movers wander inside the zones picked (each light on
// its own path, aimed from where it hangs) - "hover round the dance floor
// and the DJ booth".  Several zones share the lights out.
const roamPick = new Set(["dancefloor"]);
let roamSpeed = 1;
// how fast they wander: a slider, any speed from a crawl to a race
const roamWord = (v) => (v < 0.4 ? "Crawl" : v < 0.8 ? "Slow" : v < 1.5 ? "Medium" : v < 2.5 ? "Fast" : "Very fast");
function roamSlider() {
  const out = h("span.mv-roam-v", roamWord(roamSpeed));
  const input = h("input.mv-roam-speed", { type: "range", min: 0.1, max: 4, step: 0.05, value: roamSpeed,
    title: "How fast the lights wander (Start roaming again to use a new speed)", "aria-label": "Roam speed",
    oninput: (e) => { roamSpeed = +e.target.value; out.textContent = roamWord(roamSpeed); } });
  return h("label.mv-roam-sp", h("span.muted.small", "Speed"), input, out);
}
function roamBlock() {
  const zones = (((state.snap && state.snap.venue) || {}).zones || []);
  if (!zones.length) return null;
  // (never over the DJ, the bar, the stage, the operator or a keep-out: the
  // desk refuses those, app/roomsense.py NO_ROAM)
  const NO_ROAM = new Set(["dj", "bar", "stage", "foh", "backstage", "keepout"]);
  const kinds = [...new Map(zones.filter((z) => !NO_ROAM.has(z.kind)).map((z) => [z.kind, z.name || z.kind])).entries()];
  if (!kinds.length) return null;
  for (const k of [...roamPick]) if (!kinds.some(([x]) => x === k)) roamPick.delete(k);
  if (!roamPick.size) roamPick.add(kinds[0][0]);
  const roaming = ((state.snap && state.snap.fx) || []).filter((f) => f.lib === "roam");
  return h("div.mv-row.mv-roam", h("span.k", "Roam"),
    h("span.chip-row", ...kinds.map(([k, name]) => h("button.chip" + (roamPick.has(k) ? ".on" : ""), {
      title: `Wander inside the ${name}`, onclick: () => { if (roamPick.has(k) && roamPick.size > 1) roamPick.delete(k); else roamPick.add(k); render(true); },
    }, name))),
    roamSlider(),
    h("button.btn.small.primary", { title: "The selected moving lights wander inside these zones",
      onclick: () => run("roam", { zones: [...roamPick], speed: roamSpeed }, { toast: true }) }, roaming.length ? "Roam (again)" : "Start roaming"),
    roaming.length ? h("button.btn.small", { title: "A button that plays this roam (these zones, this speed) on these lights only, or their group", onclick: () => import("./programmer.js").then((m) => m.makeHoldButton()) }, "Make a button…") : null,
    roaming.length ? h("button.btn.small.ghost", { onclick: () => Promise.all(roaming.map((f) => run("stop_fx", { id: f.id }))) }, "Stop") : null);
}

// Where the beams may go: the room's keep-out zones and the lights told to
// stay in a zone (app/engine_zones.py) - every cue, palette, effect and roam
// is held by them.  Taught by pointing a light (roomteach.js).
function zonesBlock() {
  const snap = state.snap || {};
  const zs = (snap.venue && snap.venue.zones) || [];
  const keep = zs.filter((z) => z.kind === "keepout");
  const staying = (snap.patch || []).filter((x) => (x.stay || []).length);
  const name = (id) => (zs.find((z) => z.id === id) || {}).name || "a zone";
  const teach = (tab, label, title) => h("button.btn.small", { title, onclick: () => import("./roomteach.js").then((m) => m.openRoomTeach(tab)) }, label);
  return section("Where the beams may go",
    h("p.muted.small", (keep.length ? `Kept out: ${keep.map((z) => z.name || "keep-out").join(", ")}.` : "Nothing kept out yet.") + " "
      + (staying.length ? `${staying.length} light(s) stay in ${[...new Set(staying.flatMap((x) => x.stay.map(name)))].join(" / ")}.` : "No light is held in a zone.")
      + " Everything they play is held: cues, palettes, effects, roams."),
    h("div.row-btns",
      teach("keepout", "Not here (keep out)…", "Point a light at a wall, the bar staff, the DJ: no beam goes there"),
      teach("stay", "Keep lights in a zone…", "These lights' beams stay inside the zones you pick"),
      teach("zone", "Mark out a zone…", "Point a light at the corners of the dance floor, the bar...")));
}

// Movements fit the dance floor: every mover's own pan/tilt range for the
// floor, worked out from where it hangs - a circle or a sweep is shrunk to
// fit, keeping its shape (a zone only stops a beam at its edge).  "Cues &
// aims too" is the old way of holding everything on the floor (Keep lights
// in a zone does it exactly): shown only while a show has it on.
function floorBlock() {
  const snap = state.snap || {};
  const n = snap.floor_movers || 0;
  if (!n) {
    return section("Movements fit the dance floor",
      h("p.muted.small", "Mark out a dance floor (above, or draw it in Arrange) - then circles and sweeps are sized to stay on it."));
  }
  const toggle = (label, key, on, title) => h("button.chip" + (on ? ".on" : ""), {
    title, onclick: () => run("floor_safe", { [key]: !on }, { toast: true }),
  }, (on ? "✓ " : "") + label);
  let checking = false;
  return section("Movements fit the dance floor",
    h("div.chip-row",
      toggle("Circles and sweeps fit on the floor", "movement", snap.floor_safe !== false,
        "Circles, sweeps and the rest are sized to stay on the dance floor for every light, keeping their shape"),
      snap.floor_lock ? toggle("Cues & aims too (old)", "everything", true,
        "This show holds everything on the floor the old way - Keep lights in a zone (above) does it exactly; tap to turn this off") : null),
    h("div.mv-row",
      h("span.muted.small", `${n} moving light(s) know where the floor is.`),
      h("button.btn.small", {
        title: "Point the selection at the front, right, back and left of the floor in turn - check each real beam lands on it",
        onclick: async () => {
          if (checking) return;
          checking = true;
          for (const spot of ["front", "right", "back", "left", "floor"]) {
            await run("aim_spot", { spot }, { silentError: true });
            toast(`Check: ${spot === "floor" ? "centre" : spot}`, "ok");
            await new Promise((r) => setTimeout(r, 1600));
          }
          checking = false;
        },
      }, "Check the floor")));
}

function nudgeBlock() {
  const step = () => (fine ? 0.002 : 0.02);
  const btn = (label, axis, sign, title) => h("button.btn.mv-arrow", {
    title, onclick: () => run("nudge", { axis, step: sign * step() }, { silentError: false }),
  }, label);
  // nudge while watching the real beam; a position worth keeping is saved
  // with the palette strip's + Save (there was a second Save spot here)
  return h("div.mv-nudgebox", { title: "Nudge: a small step while you watch the real beam" },
    h("div.mv-nudge",
      h("div.mv-pad",
        h("span"), btn("↑", "tilt", 1, "Tilt up"), h("span"),
        btn("←", "pan", -1, "Pan left"), h("button.btn.mv-fine" + (fine ? ".on" : ""), {
          title: "Fine: tiny steps for exact focus", onclick: () => { fine = !fine; render(true); },
        }, fine ? "fine" : "coarse"), btn("→", "pan", 1, "Pan right"),
        h("span"), btn("↓", "tilt", -1, "Tilt down"), h("span"))));
}

function bpmText() {
  const t = tempo();
  return t && t.source !== "manual" ? `${Math.round(t.bpm)} BPM` : "Tap";
}

// Who a button made here plays on: the group, when the selection is
// exactly a group (lights added to the group later follow it), else just
// the selected moving lights.
function buttonTarget() {
  const sel = new Set(((state.snap && state.snap.selected) || []).map(Number));
  const g = ((state.snap && state.snap.groups) || []).find((x) =>
    x.heads.length && x.heads.length === sel.size && x.heads.every((n) => sel.has(n)));
  if (g) return { target: { group: g.n }, who: `group ${g.name}` };
  const heads = movers().map((x) => x.head_no);
  return { target: { heads }, who: `${heads.length} light(s)` };
}

// A button that plays this, made at once (rename it in Edit any time); a
// saved move follows the move when it's updated
async function makeButton(button, label) {
  if (!movers().length) { toast("Select moving lights first"); return; }
  const { target, who } = buttonTarget();
  const r = await run("quick_set", { page: 1, slot: "free", button: { ...button, label: label.slice(0, 24), mode: "latch", target } });
  if (r.ok) toast(`Button “${label}” made for ${who} - ${r.summary.replace(/^button /, "Buttons page ").replace(/:.*$/, "")}`, "ok", 4000);
}

// Move speed: THIS movement's speed - the whole move, its pan and tilt
// speeds together (their balance kept).  It changes the movement running on
// the selected lights without a restart, and a button made from it keeps it.
// Other buttons and effects are not touched (that is the desk's Speed
// master, below: every effect at once).
function moveSpeed() {
  const input = h("input.mv-mult", { type: "range", min: 10, max: 400, step: 5, value: Math.round(knobs.mult * 100) });
  const out = h("span.mono.small", `${Math.round(knobs.mult * 100)}%`);
  let t = 0;
  input.addEventListener("input", () => {
    knobs.mult = +input.value / 100;
    out.textContent = input.value + "%";
    clearTimeout(t);
    t = setTimeout(() => {
      const heads = movers().map((x) => x.head_no), p = params();
      for (const f of running().filter((x) => x.heads.some((n) => heads.includes(n)) && x.lib !== "shape" && x.lib !== "roam")) {
        const want = { speed: p.speed };
        if (f.lib === "range") { want.pan_speed = p.pan_speed; want.tilt_speed = p.tilt_speed; }
        run("fx_tweak", { id: f.id, params: want }, { silentError: true });
      }
    }, 80);
  });
  const set = (v) => () => { input.value = v; input.dispatchEvent(new Event("input")); };
  return h("div.mv-row", { title: "This movement's speed: the whole move, pan and tilt together. Saved with a button made from it; other buttons keep theirs." },
    h("span.k", "Move speed"), input, out,
    h("span.chip-row", h("button.chip", { onclick: set(50) }, "½×"), h("button.chip", { onclick: set(100) }, "1×"),
      h("button.chip", { onclick: set(200) }, "2×")));
}

// the desk's Speed master: EVERY running effect and button at once - kept
// small, and said plainly, so it is not mistaken for this movement's speed
function speedMaster() {
  const cur = (state.lite && state.lite.speed_master) || (state.snap && state.snap.speed_master) || 1;
  const out = h("span.mono.small.mv-master-v", `${Math.round(cur * 100)}%`);
  const set = (pct) => () => { out.textContent = pct + "%"; masterHeldUntil = Date.now() + 1500; run("speed_master", { pct }, { silentError: true }); };
  // Tap tempo: the desk's beat clock (the pill in the top bar); the Speed
  // master follows it, 120 BPM = 1x, so 60 BPM halves and 240 doubles
  const tap = h("button.chip.mv-tap", { title: "Tap on the beat (or T). 120 BPM = 1×",
    onpointerdown: (e) => {
      e.preventDefault();
      tapTempo().then((r) => { if (r && r.tempo) tap.textContent = `${Math.round(r.tempo.bpm)} BPM`; });
    } }, bpmText());
  return h("div.mv-row.mv-desk", { title: "The desk's Speed master: every effect and button at once (the Speed fader on the buttons page too)" },
    h("span.k.small", "All effects"), out, h("span.small.muted", cur !== 1 ? "every button and effect runs at this speed" : "the desk's Speed master"),
    h("span.chip-row", cur !== 1 ? h("button.chip", { onclick: set(100) }, "back to 1×") : null, tap));
}

function movementBlock() {
  // the movements on the SELECTED lights only (one a button runs on another
  // light is that light's, and showed up here whichever light was picked)
  const mine = new Set(movers().map((x) => x.head_no));
  const act = running().filter((f) => f.heads.some((n) => mine.has(n)));
  const theirs = othersOn(mine);
  // tap or type the seconds for one turn; what runs changes as it runs
  const secs = knob({ label: "Speed", unit: "s / turn", value: knobs.secs, min: 0.5, max: 200, presets: TURN_PRESETS, invert: true,
    minusTitle: "Faster (fewer seconds)", plusTitle: "Slower (more seconds)",
    onSet: (v) => {
      knobs.secs = v;
      const heads = movers().map((x) => x.head_no);
      for (const f of running().filter((x) => x.heads.some((n) => heads.includes(n)))) {
        run("fx_tweak", { id: f.id, speed: +(1 / v).toFixed(4) }, { silentError: true });
      }
    } });
  const abOn = knobs.showAB || act.some((f) => f.lib === "range");
  // three columns across the panel (it was one long column that scrolled):
  // the movements and my moves | how the movement goes | A -> B, the speeds,
  // what is running
  return h("div.mv-sec.mv-move", { title: "Movements run around where the lights point now. They fit the dance floor - or the zone a light is kept in - and no beam crosses a keep-out zone (Zones & limits)." },
    h("div.mv-col.mv-col-shapes",
      h("div.mv-tiles", ...MOVES.filter(([name]) => canRun(name)).map(([name, label, icon]) => h("button.mv-tile" + (act.some((f) => f.lib === name) || (name === "range" && knobs.showAB) ? ".on" : ""), {
        title: params().zone ? `${label} across the ${zoneName()}` : `${label} around where the lights point now`, onclick: () => startMove(name),
      }, h("b", icon), h("span", label)))),
      axes().both ? shapesRow() : h("p.muted.small", axes().tilt ? "These lights only tilt: Bounce is the movement they can do."
        : "These lights only pan: Sweep and Fan are the movements they can do."),
      myMoves(act)),
    h("div.mv-col.mv-col-knobs",
      zoneKinds().length ? h("div.mv-row", h("span.k", "Where"), h("span.chip-row",
        ...chips([["around the aim", "", "The movement goes round wherever the lights point now"],
          ...zoneKinds().map(([k, name]) => [name, k, `The movement is drawn on the ${name}: every beam stays on it, wherever the light hangs`])],
        (v) => (params().zone || "") === v, (v) => { knobs.zone = v; }))) : null,
      h("div.mv-row", h("span.k", "Size"), h("span.chip-row",
        ...chips(SIZES.map(([l, v], i) => [l, v, params().zone ? `${["a quarter", "half", "all"][i]} of the ${zoneName()}` : `${v}° around the aim`]),
          (v) => knobs.size === v, (v) => { knobs.size = v; }))),
      secs,
      h("div.mv-row", h("span.k", "Lights"), h("span.chip-row",
        ...chips([["together", 0, "Every light at the same point"], ["½ wave", 180, "Half a cycle between the first light and the last"],
          ["wave", 360, "A smooth wave: each light a step behind the one before"], ["2 waves", 720]], (v) => knobs.spread === v, (v) => { knobs.spread = v; }),
        degIn(knobs.spread, 0, 720, (v) => { knobs.spread = v; }, "The wave's spread, in degrees of the cycle"))),
      multiTilt() ? h("div.mv-row", h("span.k", "Heads"), h("span.chip-row",
        ...chips([["all together", false], ["a wave through the heads", true, "Each head of a multi-head light moves in turn"]],
          (v) => knobs.across === v, (v) => { knobs.across = v; }))) : null),
    h("div.mv-col.mv-col-ab",
      abOn ? rangeBlock2() : null,
      h("div.mv-row", h("span.k", "Direction"), h("span.chip-row",
        ...chips([["↻", 1, "Clockwise"], ["↺", -1, "Counter-clockwise"]], (v) => knobs.direction === v, (v) => { knobs.direction = v; }))),
      h("div.mv-row", h("span.k", "Arc"), h("span.chip-row",
        ...chips([["90°", 90], ["180°", 180], ["270°", 270], ["full", 360]], (v) => knobs.arc === v, (v) => { knobs.arc = v; }),
        degIn(knobs.arc, 10, 360, (v) => { knobs.arc = v; }, "Any arc, in degrees"))),
      h("div.mv-row", h("span.k", "Lock"), h("span.chip-row",
        ...chips([["none", 0], ["keep tilt", 1, "Only pan moves"], ["keep pan", 2, "Only tilt moves"]],
          (v) => knobs.lock === v, (v) => { knobs.lock = v; }))),
      moveSpeed(),
      speedMaster(),
      theirs.length ? h("p.small.muted.mv-theirs", theirs.map((f) => `${(MOVES.find((m) => m[0] === f.lib) || [0, f.lib])[1]} on ${f.heads.length} light(s) is played by a ${f.from}`
        + ` - change it in that ${f.from}${f.from === "button" ? " (Buttons -> Edit)" : ""}, not here`).join(" · ")) : null,
      act.length ? h("div.mv-running", ...act.map((f) => h("div.mv-run",
        h("span", `${f.lib === "shape" ? f.label : (MOVES.find((m) => m[0] === f.lib) || [0, f.lib])[1]}${onZone(f)} · ${f.heads.length} light(s)`),
        spaceSelect(f), beatSelect(f),
        h("button.btn.small", { onclick: () => run("stop_fx", { id: f.id }) }, "Stop"),
        fxKnobs(f))),
      h("button.btn.small.ghost", { onclick: () => { for (const f of act) run("stop_fx", { id: f.id }, { silentError: true }); } }, "Stop all")) : null));
}

// a number of degrees, typed (no fixed stops); Enter or leaving the box applies it
function degIn(value, min, max, set, title) {
  return h("label.mv-deg", { title }, h("input", { type: "number", min, max, step: 5, value,
    onchange: (e) => { const v = Math.max(min, Math.min(max, +e.target.value || 0)); set(v); render(true); reapply(); } }), "°");
}

// A → B: from one angle to another, in the lights' own degrees (0 = the
// middle of the travel); the preview says what will happen, and the live
// line where the first light points now
function rangeBlock2() {
  const ms = movers();
  if (!ms.length) return null;
  const ax = axes();
  const span = (ms[0].span || [540, 270]);
  const sgn = (v) => `${v > 0 ? "+" : ""}${Math.round(v)}°`;
  const pans = knobs.fullPan ? `pan ${sgn(-span[0] / 2)} → ${sgn(span[0] / 2)} (whole travel)` : knobs.panA !== knobs.panB ? `pan ${sgn(knobs.panA)} → ${sgn(knobs.panB)}` : "";
  const tilts = knobs.fullTilt ? `tilt ${sgn(-span[1] / 2)} → ${sgn(span[1] / 2)} (whole travel)` : knobs.tiltA !== knobs.tiltB ? `tilt ${sgn(knobs.tiltA)} → ${sgn(knobs.tiltB)}` : "";
  const what = [pans, tilts].filter(Boolean).join(", ") || "nothing moves yet - set a from and a to";
  const modeWord = ["bounce (there and back)", "loop (A → B, again from A)", "once (A → B, then stays)"][knobs.mode];
  const tilt = ms.find((x) => (x.map || []).filter((r) => r === "tilt").length > 1);
  const heads = tilt ? (tilt.map || []).filter((r) => r === "tilt").length : 0;
  const ps = knobs.panSecs || knobs.secs, ts = knobs.tiltSecs || knobs.secs;
  const trip = pans && tilts && ps !== ts ? `pan ${ps} s, tilt ${ts} s a trip` : `${pans ? ps : tilts ? ts : knobs.secs} s a trip`;
  return h("div.mv-range",
    h("div.mv-row", h("span.k", "A → B"), h("span.chip-row",
      ax.pan ? h("span.mv-deg", "Pan ", degIn(knobs.panA, -360, 360, (v) => { knobs.panA = v; knobs.fullPan = false; }, "Pan from, degrees (- is left of centre)"), " → ",
        degIn(knobs.panB, -360, 360, (v) => { knobs.panB = v; knobs.fullPan = false; }, "Pan to, degrees")) : null,
      ax.pan ? h("button.chip" + (knobs.fullPan ? ".on" : ""), { title: `The light's whole pan, end to end (${Math.round(span[0])}°)`,
        onclick: () => { knobs.fullPan = !knobs.fullPan; render(true); reapply(); } }, "whole pan") : null,
      // the pan at its own speed (empty: at the Speed above)
      ax.pan ? secsIn("Pan speed ", "panSecs", "Seconds for one pan trip - empty: the Speed above") : null)),
    ax.tilt ? h("div.mv-row", h("span.k", ""), h("span.chip-row",
      h("span.mv-deg", "Tilt ", degIn(knobs.tiltA, -180, 180, (v) => { knobs.tiltA = v; knobs.fullTilt = false; }, "Tilt from, degrees (- is down from straight out)"), " → ",
        degIn(knobs.tiltB, -180, 180, (v) => { knobs.tiltB = v; knobs.fullTilt = false; }, "Tilt to, degrees")),
      h("button.chip" + (knobs.fullTilt ? ".on" : ""), { title: `The light's whole tilt (${Math.round(span[1])}°)`,
        onclick: () => { knobs.fullTilt = !knobs.fullTilt; render(true); reapply(); } }, "whole tilt"),
      // the tilt at its own speed (empty: with the pan, at the Speed above)
      secsIn("Tilt speed ", "tiltSecs", "Seconds for one tilt trip - empty: the Speed above"))) : null,
    h("div.mv-row", h("span.k", ""), h("span.chip-row",
      ...chips([["bounce", 0, "A → B → A…"], ["loop", 1, "A → B, then from A again"], ["once", 2, "A → B, then it stays at B"]],
        (v) => knobs.mode === v, (v) => { knobs.mode = v; }))),
    heads > 1 ? h("div.mv-row", h("span.k", "Per head"), h("span.chip-row",
      ...Array.from({ length: heads }, (_, i) => {
        const c = knobs.cells[i + 1] || {};
        const setC = (key) => (v) => { knobs.cells = { ...knobs.cells, [i + 1]: { ...c, [key]: v } }; };
        return h("span.mv-deg", { title: `Head ${i + 1}'s own tilt, from → to (empty: as above)` }, `${i + 1}: `,
          degIn(c.tilt_a ?? knobs.tiltA, -180, 180, setC("tilt_a")), "→", degIn(c.tilt_b ?? knobs.tiltB, -180, 180, setC("tilt_b")));
      }),
      Object.keys(knobs.cells).length ? h("button.chip", { onclick: () => { knobs.cells = {}; render(true); reapply(); } }, "all the same") : null)) : null,
    h("p.small.mv-preview", `A → B: ${what} · ${modeWord} · ${trip}${knobs.mult !== 1 ? ` (at ${Math.round(knobs.mult * 100)}%)` : ""}${knobs.spread ? ` · a wave across the lights (${knobs.spread}°)` : ""}.`),
    h("p.small.muted.mv-now", { id: "mv-now" }, nowText()));
}

// seconds a trip for one axis of A -> B (empty: the Speed above)
function secsIn(label, key, title) {
  return h("label.mv-deg.mv-tsecs", { title }, label,
    h("input", { type: "number", min: 0.5, max: 200, step: 0.5, value: knobs[key] || "", placeholder: String(knobs.secs),
      onchange: (e) => { const v = +e.target.value; knobs[key] = v > 0 ? Math.max(0.5, Math.min(200, v)) : 0; render(true); reapply(); } }),
    " s a trip");
}

// where the first selected mover points now, in its own degrees (live)
function nowText() {
  const ms = movers();
  if (!ms.length) return "";
  const hd = ms[0];
  const lk = (state.looks || {})[hd.head_no];
  if (!lk || lk.pan === undefined) return "";
  const span = hd.span || [540, 270];
  const d = (f, s) => `${f >= 0.5 ? "+" : ""}${Math.round((f - 0.5) * s)}°`;
  return `Now: #${hd.head_no} pan ${d(lk.pan, span[0])}` + (lk.tilt !== undefined ? `, tilt ${d(lk.tilt, span[1])}` : "");
}

// ------------------------------------------------------------- My moves
// A movement you made and named ("Slow half-turn CCW"): tap to play it on
// the selected lights, and it loops until stopped - not a cue.
const moves = () => (state.snap && state.snap.moves) || [];

function moveText(m) {
  const p = m.params || {};
  const shape = (MOVES.find((x) => x[0] === m.lib) || [0, m.lib])[1];
  const bits = [shape];
  if (p.arc && p.arc < 360) bits.push(`${Math.round(p.arc)}°`);
  if (p.direction < 0) bits.push("CCW");
  if (p.speed) bits.push(`${Math.round(1 / p.speed)} s`);
  return bits.join(" · ");
}

async function saveMove(existing = null) {
  const name = existing ? existing.name : await promptBox("Save as my move", "Name", "", { ok: "Save", placeholder: "e.g. Slow half-turn CCW" });
  if (!name) return;
  // buttons that play this move change with it: never without saying so
  const users = existing ? (((state.snap && state.snap.quick) || {}).buttons || []).filter((b) => b.kind === "move" && b.move === existing.id) : [];
  if (users.length && !await confirmBox(`Update “${name}”?`,
    `${users.length} button(s) play this move (${users.slice(0, 4).map((b) => b.label).join(", ")}${users.length > 4 ? "…" : ""}) - they will play the new one too.`,
    { ok: "Update it and its buttons" })) return;
  const heads = movers().map((x) => x.head_no);
  const mine = running().find((f) => f.heads.some((n) => heads.includes(n)));
  const lib = mine ? mine.lib : lastLib;
  run("move_save", { name, lib, params: params(), id: existing ? existing.id : undefined }, { toast: true });
}

function myMoves(act) {
  const list = moves();
  const heads = movers().map((x) => x.head_no);
  const playing = (m) => act.some((f) => f.move === m.id && f.heads.some((n) => heads.includes(n)));
  return h("div.mv-mine",
    h("div.mv-row", h("span.k", "My moves"),
      h("button.btn.small", { title: "Save the movement and knobs as a named move", onclick: () => saveMove() }, "+ Save this as my move"),
      h("button.btn.small", { title: "A button (on / off) right now that plays what these lights are doing - the roam, shape or movement with its knobs - on these lights only (or their group)",
        onclick: () => import("./programmer.js").then((m) => m.makeHoldButton()) }, "Make a button…"),
      h("button.btn.small", { title: "Record the movement (and the rest of the programmer) as a cue",
        onclick: () => openCueDialog(focusedPlayback()) }, "Record as a cue…")),
    list.length ? h("div.mv-mytiles", ...list.map((m) => {
      const more = h("button.mv-opts", { "aria-label": `${m.name} options`, title: "Rename, update, delete",
        onclick: (e) => {
          e.stopPropagation();
          menu(e.currentTarget, [
            { label: "Update to the knobs now", run: () => saveMove(m) },
            { label: "Make a button", run: () => makeButton({ kind: "move", move: m.id }, m.name) },
            { label: "Rename…", run: async () => {
              const name = await promptBox("Rename move", "Name", m.name, { ok: "Rename" });
              if (name) run("move_rename", { id: m.id, name });
            } },
            { label: "Delete", danger: true, run: async () => {
              if (await confirmBox("Delete move", `Delete “${m.name}”?`, { ok: "Delete", danger: true })) run("move_delete", { id: m.id });
            } },
          ]);
        } }, "⋯");
      return h("div.mv-my" + (playing(m) ? ".on" : ""),
        h("button.mv-play", { title: playing(m) ? `Stop ${m.name}` : `Play ${m.name} on the selected lights`,
          onclick: () => {
            if (playing(m)) { for (const f of act.filter((x) => x.move === m.id)) run("stop_fx", { id: f.id }); }
            else run("move_play", { id: m.id, heads }, { toast: true });
          } },
        h("b", m.name), h("small", moveText(m))), more);
    })) : null);
}

function rangeBlock(sel) {
  if (sel.length !== 1 && sel.length > 0) {
    return section("This light's range", h("p.muted.small", `Applies to each of the ${sel.length} selected lights, at where each points now.`), rangeButtons());
  }
  if (!sel.length) return null;
  const lim = sel[0].limits || {};
  const marks = sel[0].range_marks || {};
  const show = (role) => {
    const m = marks[role];
    if (m && Object.keys(m).length) {
      const done = Object.keys(m)[0];
      const todo = { top: "bottom", bottom: "top", left: "right", right: "left" }[done];
      return `${done} marked - now point it at the ${todo} and set that`;
    }
    const r = lim[role];
    return r ? "limited" : "full travel";
  };
  return section("This light's range",
    h("p.muted.small", `Tilt: ${show("tilt")} · Pan: ${show("pan")}. Point it at one edge and set it, then the other. Everything stays between them - cues, movements, spots, buttons. For what floor zones can't see: the ceiling, a balcony, the truss next to it.`),
    rangeButtons());
}

function rangeButtons() {
  const b = (label, axis, edge, title) => h("button.chip", { title, onclick: () => run("move_range", { axis, edge }, { toast: true }) }, label);
  return h("div",
    h("div.chip-row", h("span.k", "Tilt"), b("Set top", "tilt", "top", "The highest this light may point"),
      b("Set bottom", "tilt", "bottom", "The lowest this light may point"), b("Clear", "tilt", "clear")),
    h("div.chip-row", h("span.k", "Pan"), b("Set left", "pan", "left"), b("Set right", "pan", "right"), b("Clear", "pan", "clear")));
}

// ------------------------------------------------------ laser safe zone
// A laser has no pan/tilt: its beams go where its Y (height) and size say.
// Mark the lowest and highest safe Y, and the largest safe size, with the
// laser running and watched; every cue and button then stays inside them.
function laserBlock(ls) {
  if (!ls.length) return null;
  const one = ls.length === 1 ? ls[0] : null;
  const has = (r) => ls.some((x) => (x.map || []).includes(r));
  const low = ls.filter((x) => x.y != null && +x.y < EYE_SAFE_M);
  const warn = low.length ? h("p.mv-warn.small",
    `${low.map((x) => `#${x.head_no} is at ${(+x.y).toFixed(1)} m`).join(", ")} in the venue. If it really hangs that low, ` +
    `beams may reach eye level: hang lasers ${EYE_SAFE_M} m or more above the crowd, or aim them above it. ` +
    `(Not placed yet? Set its height in the venue editor.)`) : null;
  const state1 = (role) => {
    if (!one) return "";
    const m = (one.range_marks || {})[role];
    if (m && Object.keys(m).length) return `${Object.keys(m)[0]} marked - now move it to the other edge and set that`;
    const r = (one.limits || {})[role];
    return r ? `${r[0]}-${r[1]}` : "not set";
  };
  const b = (label, axis, edge, title) => h("button.chip", { title, onclick: () => run("move_range", { axis, edge }, { toast: true }) }, label);
  const rows = [];
  if (has("laser_y")) {
    rows.push(h("div.chip-row", h("span.k", "Beam height"),
      b("Set lowest", "laser_y", "low", "The lowest the beams may go - above everyone's eyes"),
      b("Set highest", "laser_y", "high", "The highest the beams may go"), b("Clear", "laser_y", "clear"),
      one ? h("span.muted.small", state1("laser_y")) : null));
  }
  if (has("laser_size")) {
    rows.push(h("div.chip-row", h("span.k", "Size"),
      b("Set largest", "laser_size", "max", "The biggest the pattern may open"), b("Clear", "laser_size", "clear"),
      one ? h("span.muted.small", state1("laser_size")) : null));
  }
  const fixed = !has("laser_y") && !has("laser_size");
  return section("Laser safe zone", warn,
    h("p.muted.small", fixed
      ? "These beams are fixed: they go where the laser is hung and pointed. Keep them above the crowd by how it is rigged."
      : "With the laser running, move its Y on the Laser tab until the beams just clear the tallest head, and Set lowest; then the top, Set highest. Cues and buttons then stay inside."),
    ...rows);
}

function render(force = false) {
  const box = $("#move-panel");
  if (!box) return;
  const sel = movers();
  // another light (or lights) selected: show its own settings
  const selKey = sel.map((x) => x.head_no).join(",");
  if (selKey !== knobsFor) { knobsFor = selKey; if (sel.length) { loadKnobs(sel); force = true; } }
  // redraw only when what's shown changes: a panel rebuilt on every live
  // update swallows clicks and flickers
  const ls = lasers();
  const key = JSON.stringify([sel.map((x) => [x.head_no, x.limits || null, x.range_marks || null]),
    ls.map((x) => [x.head_no, x.y, x.limits || null, x.range_marks || null]),
    ((state.snap && state.snap.move_spots) || []).map((s) => s.key),
    running().map((f) => [f.id, f.lib, f.move, f.params && f.params.speed, f.params && f.params.size, f.params && f.params.beats]), othersOn(new Set(sel.map((x) => x.head_no))).map((f) => [f.id, f.from]), ((state.snap && state.snap.fx) || []).filter((f) => f.lib === "roam").map((f) => f.id),
    moves(), shapes(), knobs, fine, own.key, own.attrs.length, own.rev,
    state.snap && [state.snap.floor_safe, state.snap.floor_lock, state.snap.floor_movers],
    state.snap && state.snap.venue && [state.snap.venue.seq, state.snap.venue.room, state.snap.venue.zones,
      state.snap.venue.objects, state.snap.venue.rigging, state.snap.venue.stage, state.snap.venue_orient],
    ((state.snap && state.snap.patch) || []).map((x) => [x.head_no, x.x, x.z, x.yaw])]);
  if (!force && key === lastKey) {
    const cur = (state.lite && state.lite.speed_master) || (state.snap && state.snap.speed_master);
    if (cur && Date.now() > masterHeldUntil) {
      const v = box.querySelector(".mv-master-v");
      if (v) v.textContent = `${Math.round(cur * 100)}%`;
    }
    return;
  }
  lastKey = key;
  const aim = $("#move-aim"), pt = $("#mv-pt"), nudge = $("#move-nudge");
  const show = (on) => { if (aim) aim.hidden = !on; if (pt) pt.hidden = !on; };
  if (!selectionHeads().length) {
    show(false);
    box.replaceChildren(h("p.muted.small.mv-empty", "Select moving lights to aim and move them."));
    return;
  }
  if (!sel.length) {
    show(false);
    box.replaceChildren(...[ls.length ? null : h("p.muted.small", "The selected lights can't move (no pan or tilt). Colour, brightness and effects are on the other tabs."),
      laserBlock(ls)].filter(Boolean));
    return;
  }
  show(true);
  // aiming at a spot (follow me, the floor map, spots, roam) needs pan and tilt
  if (aim) aim.replaceChildren(axes().both ? aimBlock(spotsBlock(), roamBlock())
    : h("div.mv-sec", h("h3", "Aim"), h("p.muted.small", `The selected lights only ${axes().tilt ? "tilt" : "pan"}, so they can't be aimed at a spot: use the pad below.`)));
  if (nudge) nudge.replaceChildren(nudgeBlock());
  // each block on its sub-tab (Move / Limits; Point is the aim map and the pad)
  const tag = (el, k) => { if (el) el.dataset.in = k; return el; };
  box.replaceChildren(...[tag(movementBlock(), "move"), tag(ownBlock(), "move"), tag(zonesBlock(), "limits"), tag(floorBlock(), "limits"),
    tag(rangeBlock(sel), "limits"), tag(laserBlock(ls), "limits")].filter(Boolean));
  loadOwn();
}

export function initMovePanel() {
  // Point sizes its map and pad from the height there is (app.css --ph)
  const pane = document.querySelector('.tab-pane[data-pane="position"]');
  if (pane && window.ResizeObserver) new ResizeObserver(() => pane.style.setProperty("--ph", `${pane.clientHeight}px`)).observe(pane);
  on("snapshot", () => render());
  on("lite", () => render());
  on("selection", () => render());
  // the live degrees line, without redrawing the tab
  on("looks", () => { const el = document.getElementById("mv-now"); if (el) { const t = nowText(); if (el.textContent !== t) el.textContent = t; } });
  render(true);
}
