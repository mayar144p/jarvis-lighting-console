// The Move tab: point lights with one tap, nudge them while watching the
// real beam, run a movement that stays where they are aimed, slow
// everything with the Speed master, and give one light its own range.
//
// Built from the venue (spots come from its dance floor and zones) and the
// engine's movement effects (app/motion.py): a shape of a given size around
// the aim, fitted inside each light's range, never faster than its motor.
import { beatSelect, spaceSelect } from "./programmer.js";
import { tap as tapTempo, tempo } from "./tempo.js";
import { get } from "./api.js";
import { state, on, selectionHeads } from "./store.js";
import { run } from "./actions.js";
import { $, h, toast, promptBox, confirmBox, menu } from "./ui.js";
import { openCueDialog } from "./dialogs.js";
import { focusedPlayback } from "./playbacks.js";
import { aimBlock } from "./aimfollow.js";

const MOVES = [
  ["circle", "Circle", "↻"], ["pan_sweep", "Sweep", "↔"], ["tilt_bounce", "Bounce", "↕"],
  ["figure_eight", "Figure 8", "∞"], ["fan_pan", "Fan", "⋔"],
];
const MOVE_KINDS = new Set(MOVES.map((m) => m[0]));
const SIZES = [["S", 10], ["M", 20], ["L", 40]];

// the knobs, remembered while the page is open
const knobs = { direction: 1, arc: 360, size: 20, secs: 8, wave: false, lock: 0, across: false };
let fine = false;
let lastKey = "";
let masterHeldUntil = 0;
let own = { key: "", attrs: [] };         // the selection's own motor channels (Wave spin...)

// ---------------------------------------------------- a light's own moves
// A light's own motor channels (a Wave 360's continuous pan rotation, its
// built-in tilt programs, their speeds) arrive as ranges like "1-127
// counter-clockwise with decreasing speed".  Here they become ↺ ■ ↻ and a
// slow-to-fast slider; Jarvis works out the value inside the right range.
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
const lasers = () => selectionHeads().filter((x) => (x.map || []).some((r) => r === "laser_on" || r === "laser_y" || r.startsWith("laser_beam")));
const EYE_SAFE_M = 3;                      // beams stay at least this high over a crowd
const running = () => ((state.snap && state.snap.fx) || []).filter((f) => MOVE_KINDS.has(f.lib));
const params = () => ({
  speed: +(1 / knobs.secs).toFixed(4), size: knobs.size, arc: knobs.arc,
  direction: knobs.direction, spread: knobs.wave ? 360 : 0, lock: knobs.lock,
});

let lastLib = "circle";

async function startMove(name) {
  lastLib = name;
  const heads = movers().map((x) => x.head_no);
  if (!heads.length) { toast("Select a moving light first"); return; }
  for (const f of running()) {
    if (f.heads.some((n) => heads.includes(n))) await run("stop_fx", { id: f.id }, { silentError: true });
  }
  run("run_fx", { name, params: params(), heads, across: knobs.across && multiTilt() }, { toast: true });
}

// a Wave 360 has four tilts: a movement can run across them (a tilt wave)
const multiTilt = () => movers().some((x) => (x.map || []).filter((r) => r === "tilt").length > 1);

// a knob changed while a movement runs on these lights: restart it with the new knobs
async function reapply() {
  const heads = movers().map((x) => x.head_no);
  const mine = running().filter((f) => f.heads.some((n) => heads.includes(n)));
  for (const f of mine) {
    await run("stop_fx", { id: f.id }, { silentError: true });
    await run("run_fx", { name: f.lib, params: params(), heads: f.heads, across: knobs.across && multiTilt() }, { silentError: true });
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
    h("div.mv-spots", ...spots.map((s) => h("button.btn.mv-spot", {
      title: `Every selected mover points at ${s.label}, each from where it hangs`,
      onclick: () => run("aim_spot", { spot: s.key }, { toast: true }),
    }, s.label))),
    floor ? h("div.mv-row", h("span.k", "Formation"),
      h("button.chip", { title: "Spread the lights across the dance floor", onclick: () => run("aim_spot", { formation: "fan" }, { toast: true }) }, "Fan out"),
      h("button.chip", { title: "Left lights to the right side, right lights to the left", onclick: () => run("aim_spot", { formation: "cross" }, { toast: true }) }, "Cross"),
      h("button.chip", { title: "Left half to the left, right half to the right", onclick: () => run("aim_spot", { formation: "split" }, { toast: true }) }, "Split")) : null);
}

// Stay on the dance floor: every mover's own pan/tilt range for the floor,
// worked out from where it hangs.  Movement always fits inside it (on by
// default); the second switch holds cues and aims to it too.
function floorBlock() {
  const snap = state.snap || {};
  const n = snap.floor_movers || 0;
  if (!n) {
    return section("Stay on the dance floor",
      h("p.muted.small", "Draw a dance floor zone in the Venue tab (and place your lights roughly where they hang) - then every moving light keeps its beam on the floor."));
  }
  const toggle = (label, key, on, title) => h("button.chip" + (on ? ".on" : ""), {
    title, onclick: () => run("floor_safe", { [key]: !on }, { toast: true }),
  }, (on ? "✓ " : "") + label);
  let checking = false;
  return section("Stay on the dance floor",
    h("div.chip-row",
      toggle("Movement stays on the floor", "movement", snap.floor_safe !== false,
        "Circles, sweeps and the rest fit inside the dance floor for every light"),
      toggle("Cues & aims too", "everything", !!snap.floor_lock,
        "Nothing may point off the dance floor - also cues, spots and the programmer")),
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
  return h("div.mv-nudgebox",
    h("div.mv-nudge",
      h("div.mv-pad",
        h("span"), btn("↑", "tilt", 1, "Tilt up"), h("span"),
        btn("←", "pan", -1, "Pan left"), h("button.btn.mv-fine" + (fine ? ".on" : ""), {
          title: "Fine: tiny steps for exact focus", onclick: () => { fine = !fine; render(true); },
        }, fine ? "fine" : "coarse"), btn("→", "pan", 1, "Pan right"),
        h("span"), btn("↓", "tilt", -1, "Tilt down"), h("span")),
      h("div.mv-nudge-side",
        h("span.muted.small", "Nudge: watch the real beam"),
        h("button.btn.small", {
          title: "Keep this position as a spot you can tap again (a position look)",
          onclick: async () => {
            const name = await promptBox("Save spot", "Name", "", { ok: "Save" });
            if (name) run("record_palette", { kind: "position", name }, { toast: true });
          },
        }, "Save spot"))));
}

function bpmText() {
  const t = tempo();
  return t && t.source !== "manual" ? `${Math.round(t.bpm)} BPM` : "Tap";
}

// A button that plays this (a saved move follows the move when it's updated)
async function makeButton(button, label) {
  const name = await promptBox("Make a button", "Name", label, { ok: "Make button" });
  if (name === null) return;
  const heads = movers().map((x) => x.head_no);
  run("quick_set", { page: 1, slot: "free", button: { ...button, label: name || label, mode: "latch",
    target: heads.length ? { heads } : { all: true } } }, { toast: true });
}

function speedMaster() {
  const cur = (state.lite && state.lite.speed_master) || (state.snap && state.snap.speed_master) || 1;
  const input = h("input", { type: "range", min: 10, max: 200, step: 5, value: Math.round(cur * 100) });
  input.className = "mv-master";
  const out = h("span.mono.small.mv-master-v", `${Math.round(cur * 100)}%`);
  let t = 0;
  input.addEventListener("input", () => {
    const pct = +input.value;              // the value NOW, not after a live tick moved the slider
    out.textContent = pct + "%";
    masterHeldUntil = Date.now() + 1500;   // live updates wait until the server has it
    clearTimeout(t);
    t = setTimeout(() => run("speed_master", { pct }, { silentError: true }), 60);
  });
  const set = (pct) => () => { input.value = pct; input.dispatchEvent(new Event("input")); };
  // Tap tempo: the desk's beat clock (the pill in the top bar); the Speed
  // master follows it, 120 BPM = 1x, so 60 BPM halves and 240 doubles
  const tap = h("button.chip.mv-tap", { title: "Tap on the beat (or T). 120 BPM = 1×",
    onpointerdown: (e) => {
      e.preventDefault();
      tapTempo().then((r) => { if (r && r.tempo) tap.textContent = `${Math.round(r.tempo.bpm)} BPM`; });
    } }, bpmText());
  return h("div.mv-row", h("span.k", "Speed master"), input, out,
    h("span.chip-row", h("button.chip", { onclick: set(50) }, "½×"), h("button.chip", { onclick: set(100) }, "1×"),
      h("button.chip", { onclick: set(200) }, "2×"), tap));
}

function movementBlock() {
  const act = running();
  const secs = h("input", { type: "range", min: 2, max: 30, step: 1, value: knobs.secs, title: "Seconds for one turn" });
  const secsOut = h("span.mono.small", `${knobs.secs} s`);
  let t = 0;
  secs.addEventListener("input", () => {
    knobs.secs = +secs.value;
    secsOut.textContent = `${knobs.secs} s`;
    clearTimeout(t);
    t = setTimeout(reapply, 250);
  });
  return section("Movement",
    myMoves(act),
    h("div.mv-tiles", ...MOVES.map(([name, label, icon]) => h("button.mv-tile" + (act.some((f) => f.lib === name) ? ".on" : ""), {
      title: `${label} around where the lights point now`, onclick: () => startMove(name),
    }, h("b", icon), h("span", label)))),
    h("div.mv-row", h("span.k", "Direction"), h("span.chip-row",
      ...chips([["↻ clockwise", 1], ["↺ counter-clockwise", -1]], (v) => knobs.direction === v, (v) => { knobs.direction = v; }))),
    h("div.mv-row", h("span.k", "Arc"), h("span.chip-row",
      ...chips([["90°", 90], ["180°", 180], ["270°", 270], ["full", 360]], (v) => knobs.arc === v, (v) => { knobs.arc = v; }))),
    h("div.mv-row", h("span.k", "Size"), h("span.chip-row",
      ...chips(SIZES.map(([l, v]) => [l, v, `${v}° around the aim`]), (v) => knobs.size === v, (v) => { knobs.size = v; }))),
    h("div.mv-row", h("span.k", "Speed"), secs, secsOut, h("span.muted.small", "per turn")),
    h("div.mv-row", h("span.k", "Lights"), h("span.chip-row",
      ...chips([["together", false], ["wave", true]], (v) => knobs.wave === v, (v) => { knobs.wave = v; }))),
    multiTilt() ? h("div.mv-row", h("span.k", "Heads"), h("span.chip-row",
      ...chips([["all together", false], ["a wave through the heads", true, "Each head of a multi-head light moves in turn"]],
        (v) => knobs.across === v, (v) => { knobs.across = v; }))) : null,
    h("div.mv-row", h("span.k", "Lock"), h("span.chip-row",
      ...chips([["none", 0], ["keep tilt", 1, "Only pan moves"], ["keep pan", 2, "Only tilt moves"]],
        (v) => knobs.lock === v, (v) => { knobs.lock = v; }))),
    speedMaster(),
    act.length ? h("div.mv-running", ...act.map((f) => h("div.mv-run",
      h("span", `${(MOVES.find((m) => m[0] === f.lib) || [0, f.lib])[1]} · ${f.heads.length} light(s)`),
      spaceSelect(f), beatSelect(f),
      h("button.btn.small", { onclick: () => run("stop_fx", { id: f.id }) }, "Stop"))),
    h("button.btn.small.ghost", { onclick: () => { for (const f of act) run("stop_fx", { id: f.id }, { silentError: true }); } }, "Stop all")) : null,
    h("p.muted.small", "Movements run around where the lights point now and stay inside each light's range. Point them first, then pick a movement."));
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
      h("button.btn.small", { title: "A quick button (on / off) that runs this movement on these lights",
        onclick: () => {
          const heads = movers().map((x) => x.head_no);
          const mine = running().find((f) => f.heads.some((n) => heads.includes(n)));
          const lib = mine ? mine.lib : lastLib;
          makeButton({ kind: "fx", fx: lib, params: params() }, (MOVES.find((x) => x[0] === lib) || [0, "Movement"])[1]);
        } }, "Make a button"),
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
    })) : h("p.muted.small", "Set a movement up below, then save it here with a name - it plays on any lights, any time."));
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
    h("p.muted.small", `Tilt: ${show("tilt")} · Pan: ${show("pan")}. Point it at one edge and set it, then the other. Everything stays between them - cues, movements, spots, buttons.`),
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

export function renderMovePanel(force = false) {
  render(force);
}

function render(force = false) {
  const box = $("#move-panel");
  if (!box) return;
  const sel = movers();
  // redraw only when what's shown changes: a panel rebuilt on every live
  // update swallows clicks and flickers
  const ls = lasers();
  const key = JSON.stringify([sel.map((x) => [x.head_no, x.limits || null, x.range_marks || null]),
    ls.map((x) => [x.head_no, x.y, x.limits || null, x.range_marks || null]),
    ((state.snap && state.snap.move_spots) || []).map((s) => s.key),
    running().map((f) => [f.id, f.lib, f.move]), moves(), knobs, fine, own.key, own.attrs.length, own.rev,
    state.snap && [state.snap.floor_safe, state.snap.floor_lock, state.snap.floor_movers],
    state.snap && state.snap.venue && [state.snap.venue.seq, state.snap.venue.room, state.snap.venue.zones],
    ((state.snap && state.snap.patch) || []).map((x) => [x.head_no, x.x, x.z])]);
  if (!force && key === lastKey) {
    const cur = (state.lite && state.lite.speed_master) || (state.snap && state.snap.speed_master);
    const m = box.querySelector(".mv-master");
    if (cur && m && document.activeElement !== m && Date.now() > masterHeldUntil) {
      m.value = Math.round(cur * 100);
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
  if (aim) aim.replaceChildren(aimBlock(spotsBlock()));
  if (nudge) nudge.replaceChildren(nudgeBlock());
  box.replaceChildren(...[movementBlock(), ownBlock(), floorBlock(), rangeBlock(sel), laserBlock(ls)].filter(Boolean));
  loadOwn();
}

export function initMovePanel() {
  on("snapshot", () => render());
  on("lite", () => render());
  on("selection", () => render());
  render(true);
}
