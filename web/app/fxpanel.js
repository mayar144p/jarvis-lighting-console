// Lasers and special effects: the ARM switch, KILL FX, and the Laser and
// SFX programmer tabs.  An effect's OUTPUT (fire, fog, laser power) only
// ever moves from here or from its own FX buttons - never from a light's
// Flash, Full, a cue or the copilot (the engine enforces that; this is
// just the controls).  Everything else about an effect (pattern, size,
// a CO2 jet's tilt, fan speed...) is programmable like any attribute.
import { state, on, patch } from "./store.js";
import { run } from "./actions.js";
import { $, h, confirmBox } from "./ui.js";

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
  arm.textContent = st.armed ? `ARMED ${mmss(st.armed_left || 0)}` : "ARM FX";
  $("#fx-kill").classList.toggle("hot", (st.runs || []).length > 0);
}

async function toggleArm() {
  const st = sfxState();
  if (st.armed) { run("fx_arm", { state: false }, { toast: true }); return; }
  const ok = await confirmBox("Arm lasers and special effects",
    "Fire, CO2, confetti, sparks and laser output can now be triggered from their FX buttons.\n\n"
    + "Check that the area in front of every effect is clear, and never aim lasers into the audience "
    + "unless the show is licensed for it. Arming switches itself off after 10 minutes; KILL FX stops everything.",
    { ok: "ARM", danger: true });
  if (ok) run("fx_arm", { state: true }, { toast: true });
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
const holding = new Set();
function holdButton(label, cls, start, stop, title) {
  const el = h("button.btn.fx-hold" + (cls ? "." + cls : ""), { title }, label);
  let release = null;
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    el.classList.add("down");
    start();
    release = () => { el.classList.remove("down"); holding.delete(release); stop(); release = null; };
    holding.add(release);
  });
  const up = () => { if (release) release(); };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
  el.addEventListener("lostpointercapture", up);
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
    st.armed ? `ARMED · switches off in ${mmss(st.armed_left || 0)}` : "Disarmed: fire and laser output need ARM FX (top bar).");
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
    chooser("Pattern", e.laser_pattern),
    chooser("Colour", e.laser_colour),
    slider("Size", e.laser_size),
    slider("Rotation", e.laser_rot),
    slider("X position", e.laser_x),
    slider("Y position", e.laser_y),
    slider("Scan speed", e.laser_speed),
    chooser("Mode", e.fx_mode),
    slider("Setting", e.fx_param),
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
