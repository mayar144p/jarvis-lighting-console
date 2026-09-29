// Lasers and special effects: the ARM switch, KILL FX, and the Laser and
// SFX programmer tabs.  An effect's OUTPUT (fire, fog, laser power) only
// ever moves from here or from its own FX buttons - never from a light's
// Flash, Full, a cue or the copilot (the engine enforces that; this is
// just the controls).  Everything else about an effect (pattern, size,
// a CO2 jet's tilt, fan speed...) is programmable like any attribute.
import { state, on, patch } from "./store.js";
import { run } from "./actions.js";
import { $, h, modal } from "./ui.js";

const sfxState = () => (state.lite && state.lite.sfx) || (state.snap && state.snap.sfx) || { armed: false, runs: [], loads: {}, heads: {} };
const selected = () => new Set(((state.snap && state.snap.selected) || []).map(Number));
const selHeads = () => patch().filter((x) => selected().has(x.head_no));
const isLaser = (x) => (x.map || []).some((r) => r.startsWith("laser_"));
const isSfx = (x) => !isLaser(x) && (x.map || []).some((r) => r.startsWith("fx_") || r === "fog");

function mmss(s) {
  s = Math.max(0, Math.round(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ------------------------------------------------------------- top bar
function renderBar() {
  const box = $("#fxctl");
  if (!box) return;
  const st = sfxState();
  const any = Object.keys(st.heads || {}).length > 0;
  box.hidden = !any;
  document.body.classList.toggle("has-fx", any);
  if (!any) return;
  const arm = $("#fx-arm");
  arm.classList.toggle("on", !!st.armed);
  arm.textContent = !st.armed ? "ARM FX" : st.armed_forever ? "ARMED · on" : `ARMED ${mmss(st.armed_left || 0)}`;
  $("#fx-kill").classList.toggle("hot", (st.runs || []).length > 0);
}

// how long ARM lasts: remembered for the next time
let armFor = (() => { try { return localStorage.getItem("jarvis.armfor") || "10"; } catch { return "10"; } })();

function toggleArm() {
  const st = sfxState();
  if (st.armed) { run("fx_arm", { state: false }, { toast: true }); return; }
  const choices = [["10", "10 minutes"], ["60", "1 hour"], ["until", "Until I disarm"]];
  const pick = h("div.chip-row", ...choices.map(([v, label]) => h("button.chip" + (armFor === v ? ".on" : ""), {
    type: "button",
    onclick: (e) => {
      armFor = v;
      pick.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === e.currentTarget));
    },
  }, label)));
  const close = modal({
    title: "Arm lasers and special effects",
    body: h("div",
      h("p", { style: { marginTop: 0 } }, "Fire, CO2, confetti, sparks and laser output can now be triggered from their FX buttons. "
        + "Check that the area in front of every effect is clear, and never aim lasers into the audience "
        + "unless the show is licensed for it."),
      h("p.muted.small", "Stay armed for:"), pick,
      h("p.muted.small", "A laser you switch on stays on while effects are armed. Disarm or KILL FX stops everything at once.")),
    foot: [
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.danger", { onclick: () => {
        try { localStorage.setItem("jarvis.armfor", armFor); } catch { /* ignore */ }
        run("fx_arm", { state: true, minutes: armFor }, { toast: true });
        close();
      } }, "ARM"),
    ],
  });
}

export function initFxPanel() {
  const arm = $("#fx-arm");
  if (!arm) return;
  arm.addEventListener("click", toggleArm);
  $("#fx-kill").addEventListener("click", () => run("fx_kill", {}, { toast: true }));
  on("lite", renderBar);
  on("snapshot", renderBar);
  renderBar();
  // hold-to-fire must never stick if the window loses focus
  window.addEventListener("blur", () => { for (const stop of [...holding]) stop(); });
}

// ------------------------------------------------------ hold-to-fire
// A held button must ALWAYS let go when the finger does.  Two ways it
// didn't: (1) the tab re-renders while it is held, the button under the
// finger is replaced, and the browser sends the release to the page, not
// the old button - so "stop" was never sent (a laser stayed on, a CO2 jet
// kept firing to its cap); (2) "start" and "stop" are separate requests to
// a threaded server and could be handled out of order, leaving it on.  So:
// any pointer release anywhere stops every hold, and "stop" is only sent
// once "start" has been answered.
const holding = new Set();
function releaseAll() { for (const stop of [...holding]) stop(); }
window.addEventListener("pointerup", releaseAll, true);
window.addEventListener("pointercancel", releaseAll, true);

function holdButton(label, cls, start, stop, title) {
  const el = h("button.btn.fx-hold" + (cls ? "." + cls : ""), { title }, label);
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    el.classList.add("down");
    const started = Promise.resolve(start()).catch(() => {});
    const release = () => {
      if (!holding.has(release)) return;
      holding.delete(release);
      el.classList.remove("down");
      started.then(() => stop());
    };
    holding.add(release);
  });
  return el;
}

// --------------------------------------------------- attribute rows
function entries(attrState) {
  const out = {};
  for (const p of (attrState && attrState.pages) || []) for (const a of p.attrs || []) out[a.role] = a;
  return out;
}

function setAttr(role, value) {
  return run("set_attribute", { attribute: role, value }, { silentError: false });
}

function chooser(title, e) {
  if (!e) return null;
  if (e.slots && e.slots.length) {
    return h("div.fx-row", h("span.k", title), h("div.chip-row",
      ...e.slots.map((s) => h("button.chip" + (e.value !== null && e.value >= s.from && e.value <= s.to ? ".on" : ""), {
        title: `${s.name} - DMX ${s.from}-${s.to}`, onclick: () => setAttr(e.role, s.value),
      }, s.hex ? h("i.slot-dot", { style: { background: s.hex } }) : null, s.name))));
  }
  return slider(title, e);
}

function slider(title, e) {
  if (!e) return null;
  const full = e.full || 255;
  const input = h("input", { type: "range", min: 0, max: full, value: e.value ?? 0 });
  const out = h("span.mono.small", String(e.value ?? "–"));
  let t = 0;
  input.addEventListener("input", () => {
    out.textContent = input.value;
    clearTimeout(t);
    t = setTimeout(() => setAttr(e.role, +input.value), 60);
  });
  return h("div.fx-row", h("span.k", title), input, out);
}

function armedLine(st) {
  return h(st.armed ? "div.fx-armed" : "div.fx-disarmed",
    !st.armed ? "Disarmed: fire and laser output need ARM FX (top bar)."
      : st.armed_forever ? "ARMED until you disarm - lasers stay on until you stop them"
        : `ARMED · switches off in ${mmss(st.armed_left || 0)}`);
}

// A beam bar's diodes: tap one to switch it in the look, or lay a
// pattern across all of them.  Recorded in cues like any attribute; the
// real beams still only light while the laser output is on (armed).
const BEAM_PATTERNS = {
  All: () => true, None: () => false, Odd: (i) => i % 2 === 0, Even: (i) => i % 2 === 1,
  Left: (i, n) => i < n / 2, Right: (i, n) => i >= n / 2,
  Centre: (i, n) => Math.abs(i - (n - 1) / 2) < n / 4, Ends: (i, n) => Math.abs(i - (n - 1) / 2) >= n / 4,
};

// one slider for how bright the lit beams are (a diode's brightness)
function beamLevel(beams) {
  const lit = beams.filter((a) => a.value > 0);
  const start = lit.length ? Math.max(...lit.map((a) => a.value)) : 255;
  const input = h("input", { type: "range", min: 1, max: 255, value: start });
  const out = h("span.mono.small", String(start));
  let t = 0;
  input.addEventListener("input", () => {
    out.textContent = input.value;
    clearTimeout(t);
    t = setTimeout(() => {
      for (const a of beams.filter((b) => b.value > 0)) setAttr(a.role, +input.value);
    }, 80);
  });
  return h("div.fx-row", h("span.k", "Beam level"), input, out);
}

function beamsBlock(e) {
  const beams = Object.values(e).filter((a) => /^laser_beam\d+$/.test(a.role))
    .sort((a, b) => +a.role.slice(10) - +b.role.slice(10));
  if (!beams.length) return [];
  const lit = (a) => a.value !== null && a.value !== undefined && a.value > 0;
  const setBeams = async (fn) => {
    for (let i = 0; i < beams.length; i++) {
      const a = beams[i];
      await run("set_attribute", { attribute: a.role, value: fn(i, beams.length) ? (a.on || 255) : 0 }, { silentError: true });
    }
  };
  return [
    h("h3", "Beams"),
    h("div.beam-row", ...beams.map((a, i) => h("button.beam" + (lit(a) ? ".on" : ""), {
      title: `Beam ${i + 1} (${a.role}) - tap to switch it in the look`,
      onclick: () => setAttr(a.role, lit(a) ? 0 : (a.on || 255)),
    }, String(+a.role.slice(10))))),
    h("div.chip-row", h("span.k", "Pattern"),
      ...Object.entries(BEAM_PATTERNS).map(([name, fn]) => h("button.chip", { onclick: () => setBeams(fn) }, name))),
    beamLevel(beams),
    h("p.muted.small", "Record a few patterns as cues on one playback to make a beam chase. "
      + "With no beam set, Laser ON lights them all."),
  ];
}

// What the laser does when fired: its own beams, its built-in programs,
// auto or sound - its output channel's ranges, never the "off" one.
// Programmable (recorded in cues); the output still only goes on from the
// armed laser buttons.
function modeBlock(e) {
  const out = e.laser_on;
  const slots = ((out && out.slots) || []).filter((sl) => !/\b(off|blackout|disabled?|stop)\b/i.test(sl.name));
  if (slots.length < 2) return [];
  const cur = out.value;
  const set = cur !== null && cur !== undefined;
  return [h("div.fx-row", h("span.k", "Output mode"), h("div.chip-row",
    ...slots.map((sl) => h("button.chip" + (set && cur >= sl.from && cur <= sl.to ? ".on" : ""), {
      title: `${sl.name} - DMX ${sl.from}-${sl.to}. Recorded in cues; used when the laser is fired (armed).`,
      onclick: () => setAttr("laser_on", sl.value),
    }, sl.name)),
    set ? h("button.chip", { title: "Back to the fixture's default mode",
      onclick: () => run("set_attr_range", { attribute: "laser_on", clear: true }) }, "Default") : null))];
}

// Every other channel the laser has, whatever it is called: nothing it
// can do is left without a control.
const LASER_SHOWN = new Set(["laser_on", "laser_pattern", "laser_colour", "laser_size", "laser_rot", "laser_x",
  "laser_y", "laser_speed", "fx_mode", "fx_param"]);
function otherBlock(e) {
  const rest = Object.values(e).filter((a) => !LASER_SHOWN.has(a.role) && !/^laser_beam\d+$/.test(a.role)
    && !a.role.endsWith("_fine") && !["raw", "unused"].includes(a.role));
  if (!rest.length) return [];
  const name = (r) => r.replace(/^fx_param(\d)$/, "Setting $1").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  return [h("h3", "Other channels"), ...rest.map((a) => chooser(name(a.role), a))];
}

// ------------------------------------------------------------ Laser tab
function renderLaser(attrState) {
  const box = $("#laser-pane");
  const lasers = selHeads().filter(isLaser);
  if (!lasers.length) { box.replaceChildren(h("p.muted.small", "Select a laser.")); return; }
  const st = sfxState();
  const heads = lasers.map((x) => x.head_no);
  const e = entries(attrState);
  const onNow = (st.runs || []).some((r) => r.kind === "laser" && r.heads.some((n) => heads.includes(n)));
  box.replaceChildren(...[
    armedLine(st),
    h("div.fx-fire-row",
      holdButton("Laser output (hold)", "laser", () => run("fx_laser", { heads, down: true, owner: "prog-hold" }),
        () => run("fx_laser", { heads, down: false, owner: "prog-hold" }, { silentError: true }),
        "Laser on while held"),
      h("button.btn" + (onNow ? ".on" : ""), {
        onclick: () => run("fx_laser", { heads, down: !onNow, owner: "prog-latch" }),
      }, onNow ? "Laser OFF" : "Laser ON (latch)")),
    ...modeBlock(e),
    ...beamsBlock(e),
    chooser("Pattern", e.laser_pattern),
    chooser("Colour", e.laser_colour),
    slider("Size", e.laser_size),
    slider("Rotation", e.laser_rot),
    slider("X position", e.laser_x),
    slider("Y position / tilt", e.laser_y),
    slider("Scan speed", e.laser_speed),
    chooser("Mode", e.fx_mode),
    slider("Setting", e.fx_param),
    ...otherBlock(e),
    h("p.muted.small", "Pattern, colour, size and movement are recorded in cues like any attribute. The laser's output only comes on from these buttons or an FX button, and only while armed."),
  ].filter(Boolean));
}

// -------------------------------------------------------------- SFX tab
function renderSfx(attrState) {
  const box = $("#sfx-pane");
  const fxs = selHeads().filter(isSfx);
  if (!fxs.length) { box.replaceChildren(h("p.muted.small", "Select a special effect (confetti, CO2, fog…).")); return; }
  const st = sfxState();
  const e = entries(attrState);
  const fire = fxs.filter((x) => (x.map || []).includes("fx_fire")).map((x) => x.head_no);
  const fog = fxs.filter((x) => (x.map || []).includes("fog")).map((x) => x.head_no);
  const parts = [armedLine(st)];
  if (fire.length) {
    parts.push(h("h3", "Fire"),
      h("div.fx-fire-row",
        holdButton("FIRE (hold)", "fire", () => run("fx_fire", { heads: fire, down: true, owner: "prog-hold" }),
          () => run("fx_fire", { heads: fire, down: false, owner: "prog-hold" }, { silentError: true }),
          "Fires while held, capped at each machine's safe maximum"),
        h("button.btn", { onclick: () => run("fx_fire", { heads: fire, seconds: 1, owner: "prog-shot" }) }, "1 s shot")));
    const loads = st.loads || {};
    const conf = fire.filter((n) => loads[n]);
    if (conf.length) {
      parts.push(h("div.fx-tanks", ...conf.map((n) => {
        const l = loads[n];
        const pct = Math.round(100 * l.left / (l.full || 1));
        const hd = patch().find((x) => x.head_no === n) || {};
        return h("div.fx-tank", h("span.small", `#${n} ${hd.name || hd.model || ""}`),
          h("div.fx-bar", h("i", { style: { width: pct + "%" } })),
          h("span.mono.small", pct ? `${l.left} s` : "EMPTY"));
      }), h("button.btn.small", { onclick: () => run("fx_reload", { heads: conf }, { toast: true }) }, "Refilled: reload")));
    }
    parts.push(chooser("Height / level", e.fx_fire && e.fx_fire.slots ? e.fx_fire : null));
  }
  if (fog.length) {
    const level = h("input", { type: "range", min: 1, max: 100, value: 60 });
    const secs = h("input", { type: "number", min: 1, max: 600, value: 10, style: { width: "70px" } });
    const lv = h("span.mono.small", "60%");
    level.addEventListener("input", () => { lv.textContent = level.value + "%"; });
    const fogOn = (st.runs || []).some((r) => r.kind === "fog" && r.heads.some((n) => fog.includes(n)));
    parts.push(h("h3", "Fog / haze"),
      h("div.fx-row", h("span.k", "Output"), level, lv),
      h("div.fx-fire-row",
        h("button.btn.primary", { onclick: () => run("fx_fog", { heads: fog, level: +level.value, seconds: +secs.value || 10, owner: "prog" }) }, "Run for"),
        secs, h("span.small", "s"),
        holdButton("Hold", "", () => run("fx_fog", { heads: fog, level: +level.value, seconds: 120, owner: "prog-hold" }),
          () => run("fx_fog", { heads: fog, down: false, owner: "prog-hold" }, { silentError: true })),
        h("button.btn" + (fogOn ? ".on" : ""), { onclick: () => { run("fx_fog", { heads: fog, down: false, owner: "prog" }); run("fx_fog", { heads: fog, down: false, owner: "prog-hold" }, { silentError: true }); } }, "Stop")));
  }
  const extra = [chooser("Mode", e.fx_mode), slider("Fan", e.fx_fan), slider("Height", e.fx_height),
    slider("Tilt", e.tilt), slider("Speed", e.speed), slider("Setting", e.fx_param)].filter(Boolean);
  if (extra.length) parts.push(h("h3", "Settings"), ...extra);
  parts.push(h("p.muted.small", "Fire needs ARM FX and always stops at the machine's limit, even if a button sticks. Fog works without arming. Blackout and KILL FX stop everything."));
  box.replaceChildren(...parts.filter(Boolean));
}

export function renderFxPane(tab, attrState) {
  if (tab === "laser") renderLaser(attrState);
  else if (tab === "sfx") renderSfx(attrState);
}

// keep the armed line and tank bars live while the tab is open
on("lite", () => {
  const laser = $("#laser-pane"), sfx = $("#sfx-pane");
  const armed = sfxState().armed;
  for (const box of [laser, sfx]) {
    if (!box || box.closest(".tab-pane").hidden) continue;
    const line = box.querySelector(".fx-armed, .fx-disarmed");
    if (line) {
      const fresh = armedLine(sfxState());
      if (line.className !== fresh.className || line.textContent !== fresh.textContent) line.replaceWith(fresh);
    }
    if (box === sfx && box.querySelector(".fx-tanks") && armed !== undefined) {
      const st = sfxState();
      box.querySelectorAll(".fx-tank").forEach((row) => {
        const n = +(row.firstChild.textContent.match(/#(\d+)/) || [])[1];
        const l = (st.loads || {})[n];
        if (!l) return;
        const pct = Math.round(100 * l.left / (l.full || 1));
        row.querySelector(".fx-bar i").style.width = pct + "%";
        row.lastChild.textContent = pct ? `${l.left} s` : "EMPTY";
      });
    }
  }
});
