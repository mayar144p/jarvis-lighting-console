// The Move tab: point lights with one tap, nudge them while watching the
// real beam, run a movement that stays where they are aimed, slow
// everything with the Speed master, and give one light its own range.
//
// Built from the venue (spots come from its dance floor and zones) and the
// engine's movement effects (app/motion.py): a shape of a given size around
// the aim, fitted inside each light's range, never faster than its motor.
import { state, on, selectionHeads } from "./store.js";
import { run } from "./actions.js";
import { $, h, toast, promptBox } from "./ui.js";

const MOVES = [
  ["circle", "Circle", "↻"], ["pan_sweep", "Sweep", "↔"], ["tilt_bounce", "Bounce", "↕"],
  ["figure_eight", "Figure 8", "∞"], ["fan_pan", "Fan", "⋔"],
];
const MOVE_KINDS = new Set(MOVES.map((m) => m[0]));
const SIZES = [["S", 10], ["M", 20], ["L", 40]];

// the knobs, remembered while the page is open
const knobs = { direction: 1, arc: 360, size: 20, secs: 8, wave: false, lock: 0 };
let fine = false;
let lastKey = "";
let masterHeldUntil = 0;

const movers = () => selectionHeads().filter((x) => (x.map || []).includes("pan") || (x.map || []).includes("tilt"));
const running = () => ((state.snap && state.snap.fx) || []).filter((f) => MOVE_KINDS.has(f.lib));
const params = () => ({
  speed: +(1 / knobs.secs).toFixed(4), size: knobs.size, arc: knobs.arc,
  direction: knobs.direction, spread: knobs.wave ? 360 : 0, lock: knobs.lock,
});

async function startMove(name) {
  const heads = movers().map((x) => x.head_no);
  if (!heads.length) { toast("Select a moving light first"); return; }
  for (const f of running()) {
    if (f.heads.some((n) => heads.includes(n))) await run("stop_fx", { id: f.id }, { silentError: true });
  }
  run("run_fx", { name, params: params(), heads }, { toast: true });
}

// a knob changed while a movement runs on these lights: restart it with the new knobs
async function reapply() {
  const heads = movers().map((x) => x.head_no);
  const mine = running().filter((f) => f.heads.some((n) => heads.includes(n)));
  for (const f of mine) {
    await run("stop_fx", { id: f.id }, { silentError: true });
    await run("run_fx", { name: f.lib, params: params(), heads: f.heads }, { silentError: true });
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
    return section("Point at", h("p.muted.small", "Draw a dance floor (and DJ / stage / bar zones) in the Venue tab and they appear here as one-tap spots."));
  }
  const floor = spots.some((s) => s.key === "floor");
  return section("Point at",
    h("div.mv-spots", ...spots.map((s) => h("button.btn.mv-spot", {
      title: `Every selected mover points at ${s.label}, each from where it hangs`,
      onclick: () => run("aim_spot", { spot: s.key }, { toast: true }),
    }, s.label))),
    floor ? h("div.mv-row", h("span.k", "Formation"),
      h("button.chip", { title: "Spread the lights across the dance floor", onclick: () => run("aim_spot", { formation: "fan" }, { toast: true }) }, "Fan out"),
      h("button.chip", { title: "Left lights to the right side, right lights to the left", onclick: () => run("aim_spot", { formation: "cross" }, { toast: true }) }, "Cross"),
      h("button.chip", { title: "Left half to the left, right half to the right", onclick: () => run("aim_spot", { formation: "split" }, { toast: true }) }, "Split")) : null);
}

function nudgeBlock() {
  const step = () => (fine ? 0.002 : 0.02);
  const btn = (label, axis, sign, title) => h("button.btn.mv-arrow", {
    title, onclick: () => run("nudge", { axis, step: sign * step() }, { silentError: false }),
  }, label);
  return section("Nudge",
    h("div.mv-nudge",
      h("div.mv-pad",
        h("span"), btn("↑", "tilt", 1, "Tilt up"), h("span"),
        btn("←", "pan", -1, "Pan left"), h("button.btn.mv-fine" + (fine ? ".on" : ""), {
          title: "Fine: tiny steps for exact focus", onclick: () => { fine = !fine; render(true); },
        }, fine ? "fine" : "coarse"), btn("→", "pan", 1, "Pan right"),
        h("span"), btn("↓", "tilt", -1, "Tilt down"), h("span")),
      h("div.mv-nudge-side",
        h("p.muted.small", "Watch the real beam and nudge it into place."),
        h("button.btn", {
          title: "Keep this position as a spot you can tap again (a position look)",
          onclick: async () => {
            const name = await promptBox("Save spot", "Name", "", { ok: "Save" });
            if (name) run("record_palette", { kind: "position", name }, { toast: true });
          },
        }, "Save spot"))));
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
  return h("div.mv-row", h("span.k", "Speed master"), input, out,
    h("span.chip-row", h("button.chip", { onclick: set(50) }, "½×"), h("button.chip", { onclick: set(100) }, "1×"),
      h("button.chip", { onclick: set(200) }, "2×")));
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
    h("div.mv-tiles", ...MOVES.map(([name, label, icon]) => h("button.mv-tile" + (act.some((f) => f.lib === name) ? ".on" : ""), {
      title: `${label} around where the lights point now`, onclick: () => startMove(name),
    }, h("b", icon), h("span", label)))),
    h("div.mv-row", h("span.k", "Direction"), h("span.chip-row",
      ...chips([["↻ clockwise", 1], ["↺ counter-clockwise", -1]], (v) => knobs.direction === v, (v) => { knobs.direction = v; }))),
    h("div.mv-row", h("span.k", "Arc"), h("span.chip-row",
      ...chips([["90°", 90], ["180°", 180], ["full", 360]], (v) => knobs.arc === v, (v) => { knobs.arc = v; }))),
    h("div.mv-row", h("span.k", "Size"), h("span.chip-row",
      ...chips(SIZES.map(([l, v]) => [l, v, `${v}° around the aim`]), (v) => knobs.size === v, (v) => { knobs.size = v; }))),
    h("div.mv-row", h("span.k", "Speed"), secs, secsOut, h("span.muted.small", "per turn")),
    h("div.mv-row", h("span.k", "Lights"), h("span.chip-row",
      ...chips([["together", false], ["wave", true]], (v) => knobs.wave === v, (v) => { knobs.wave = v; }))),
    h("div.mv-row", h("span.k", "Lock"), h("span.chip-row",
      ...chips([["none", 0], ["keep tilt", 1, "Only pan moves"], ["keep pan", 2, "Only tilt moves"]],
        (v) => knobs.lock === v, (v) => { knobs.lock = v; }))),
    speedMaster(),
    act.length ? h("div.mv-running", ...act.map((f) => h("div.mv-run",
      h("span", `${(MOVES.find((m) => m[0] === f.lib) || [0, f.lib])[1]} · ${f.heads.length} light(s)`),
      h("button.btn.small", { onclick: () => run("stop_fx", { id: f.id }) }, "Stop"))),
    h("button.btn.small.ghost", { onclick: () => { for (const f of act) run("stop_fx", { id: f.id }, { silentError: true }); } }, "Stop all")) : null,
    h("p.muted.small", "Movements run around where the lights point now and stay inside each light's range. Point them first, then pick a movement."));
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

export function renderMovePanel(force = false) {
  render(force);
}

function render(force = false) {
  const box = $("#move-panel");
  if (!box) return;
  const sel = movers();
  // redraw only when what's shown changes: a panel rebuilt on every live
  // update swallows clicks and flickers
  const key = JSON.stringify([sel.map((x) => [x.head_no, x.limits || null, x.range_marks || null]),
    ((state.snap && state.snap.move_spots) || []).map((s) => s.key),
    running().map((f) => [f.id, f.lib]), knobs, fine]);
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
  if (!selectionHeads().length) {
    box.replaceChildren(spotsBlock(), h("p.muted.small", "Select lights to move them."));
    return;
  }
  if (!sel.length) {
    box.replaceChildren(h("p.muted.small", "The selected lights can't move (no pan or tilt). Colour, brightness and effects are on the other tabs."));
    return;
  }
  box.replaceChildren(spotsBlock(), nudgeBlock(), movementBlock(), rangeBlock(sel));
}

export function initMovePanel() {
  on("snapshot", () => render());
  on("lite", () => render());
  on("selection", () => render());
  render(true);
}
