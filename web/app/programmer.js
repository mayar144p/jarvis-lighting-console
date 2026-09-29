// The programmer: what the selected heads are being told to do.
import { get } from "./api.js";
import { state, on, patch, selected, selectionHeads } from "./store.js";
import { run, select } from "./actions.js";
import { $, $$, h, vfader, throttle, toast, promptBox, confirmBox } from "./ui.js";
import { createPicker, rgbToHex } from "./picker.js";
import { openCueDialog, openLightTest } from "./dialogs.js";

const SWATCHES = [
  ["Red", "#ff2a1f"], ["Orange", "#ff7a00"], ["Amber", "#ffb000"], ["Yellow", "#ffe600"],
  ["Green", "#1aff4a"], ["Cyan", "#00e5ff"], ["Blue", "#1f4bff"], ["Congo", "#5b2bff"],
  ["UV", "#8f00ff"], ["Magenta", "#ff00d4"], ["Pink", "#ff0080"], ["Light pink", "#ff5fa2"], ["Lavender", "#b69cff"],
  ["Warm", "#ffd9a8"], ["Neutral", "#fff1dc"], ["Cool", "#e6f0ff"], ["White", "#ffffff"],
];

let tab = "intensity";
let intFader = null;
let picker = null;
let attrState = null;
let attrTimer = 0;
let fxAvailable = null;
let fxChosen = null;

const sel = () => selected();
const hasSel = () => sel().length > 0;

// What the selection can physically do, so a PAR is never offered a
// pan/tilt pad and a white strobe never a colour wheel.
const COLOUR_ROLES = ["red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "lime", "wheel", "cto"];
const BEAM_ROLES = ["zoom", "focus", "iris", "gobo", "gobo_rot", "prism", "frost", "shutter", "strobe", "prism_rot", "gobo2"];

function capabilities() {
  const roles = new Set();
  for (const hd of selectionHeads()) for (const r of hd.map || []) roles.add(r);
  return {
    roles,
    position: roles.has("pan") || roles.has("tilt"),
    colour: COLOUR_ROLES.some((r) => roles.has(r)),
    mixing: ["red", "cyan"].some((r) => roles.has(r)),
    beam: BEAM_ROLES.some((r) => roles.has(r)),
  };
}

function applyTabVisibility() {
  const any = hasSel();
  const cap = capabilities();
  const show = { position: !any || cap.position, colour: !any || cap.colour, beam: !any || cap.beam,
    laser: any && [...cap.roles].some((r) => r.startsWith("laser_")),
    sfx: any && [...cap.roles].some((r) => r.startsWith("fx_") || r === "fog") && ![...cap.roles].some((r) => r.startsWith("laser_")) };
  $$("#prog-tabs button").forEach((b) => {
    const t = b.dataset.tab;
    const visible = show[t] === undefined ? true : show[t];
    b.hidden = !visible;
    b.title = visible ? (b.dataset.title || b.title) : "";
  });
  if (show[tab] === false) showTab(show.laser ? "laser" : show.sfx ? "sfx" : "intensity");
}

// ------------------------------------------------------------- header
function renderHeader() {
  const heads = selectionHeads();
  const box = $("#prog-sel");
  if (!heads.length) {
    box.textContent = "Select fixtures";
    box.classList.remove("has");
  } else {
    const types = [...new Set(heads.map((x) => (x.body && x.body.label) || x.model))];
    box.textContent = `${heads.length} × ${types.length === 1 ? types[0] : types.length + " types"}`;
    box.classList.add("has");
  }
  $("#prog-body").classList.toggle("disabled", !heads.length && !["looks", "fx"].includes(tab));
  applyTabVisibility();
  const attrs = new Set(((state.snap && state.snap.programmer) || {}).attrs || []);
  const pages = {
    intensity: ["dimmer"], colour: ["red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "wheel"],
    position: ["pan", "tilt"], beam: ["zoom", "focus", "iris", "gobo", "gobo_rot", "prism", "frost", "shutter", "strobe"],
  };
  $$("#prog-tabs button").forEach((b) => {
    const dot = b.querySelector(".dotmark");
    const on = (pages[b.dataset.tab] || []).some((r) => attrs.has(r));
    if (on && !dot) b.append(h("span.dotmark"));
    if (!on && dot) dot.remove();
  });
}

function progValue(role) {
  const vals = ((state.snap && state.snap.programmer) || {}).values || {};
  const out = [];
  for (const n of sel()) {
    const row = vals[n] || vals[String(n)];
    if (row && row[role] !== undefined) out.push(row[role]);
  }
  return out;
}

// ---------------------------------------------------------- intensity
const sendIntensity = throttle((level) => {
  const fade = parseFloat($("#int-fade").value) || 0;
  run("set_intensity", fade ? { level, fade } : { level }, { silentError: false });
}, 60);

// A warning on the Level tab when a selected light's shutter "open"
// value is unknown: the classic "it moves but gives no light".
function renderOpenWarning() {
  const box = $("#open-warn");
  if (!box) return;
  const bad = selectionHeads().filter((x) => x.gate && !x.gate.known && !x.tested);
  box.hidden = !bad.length;
  if (!bad.length) { box.dataset.key = ""; return; }
  const models = [...new Set(bad.map((x) => x.model))];
  if (box.dataset.key === models.join("|")) return;     // unchanged: keep the button clickable
  box.dataset.key = models.join("|");
  box.replaceChildren(h("span", `${models.join(", ")}: Jarvis doesn't know which value opens the shutter, so Full may leave it dark. `),
    h("button.btn.small.primary", { onclick: () => openLightTest(bad[0]) }, "Test this light"), " ",
    h("button.btn.small", { onclick: () => showTab("beam") }, "Find it in Beam"));
}

function renderGate() {
  renderOpenWarning();
  const box = $("#int-gate");
  const heads = selectionHeads().filter((x) => !(x.map || []).includes("dimmer")
    && (x.map || []).some((r) => r === "shutter" || r === "strobe"));
  box.hidden = !heads.length;
  if (!heads.length) return;
  const role = (heads[0].map || []).includes("shutter") ? "shutter" : "strobe";
  const key = heads.map((x) => x.head_no).join(",");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(h("span.muted.small", `${heads.length} light${heads.length === 1 ? " has" : "s have"} no dimmer - open or close the ${role}:`),
    h("button.chip", { onclick: async () => {
      const a = await get("/api/console/attributes?heads=" + heads.map((x) => x.head_no).join(","));
      const at = (a.pages || []).flatMap((p) => p.attrs || []).find((x) => x.role === role);
      run("set_attribute", { attribute: role, value: at && at.open !== undefined ? at.open : 255, heads: heads.map((x) => x.head_no) });
    } }, "Open"),
    h("button.chip", { onclick: () => run("set_attribute", { attribute: role, value: 0, heads: heads.map((x) => x.head_no) }) }, "Closed"));
}

function renderIntensity() {
  renderGate();
  const vals = progValue("dimmer");
  const num = $("#int-num");
  if (!vals.length) {
    intFader.set(0, { idle: true });
    num.textContent = hasSel() ? "–" : "–";
    return;
  }
  const mixed = new Set(vals).size > 1;
  intFader.set(vals[0], { mixed });
  num.textContent = mixed ? "mix" : `${vals[0]}%`;
}

// -------------------------------------------------------------- colour
const sendColour = throttle((hex) => run("set_colour", { hex }), 70);

function currentHex() {
  const r = progValue("red"), g = progValue("green"), b = progValue("blue");
  if (!r.length && !g.length && !b.length) return null;
  return rgbToHex(r[0] || 0, g[0] || 0, b[0] || 0);
}

function renderColour() {
  const hex = currentHex();
  if (hex) {
    picker.set(hex);
    if (document.activeElement !== $("#hex-in")) $("#hex-in").value = hex;
  }
  $$("#swatches button").forEach((b) => b.classList.toggle("on", !!hex && b.dataset.hex === hex));
  renderWheel();
  const heads = selectionHeads();
  const colourable = heads.filter((x) => (x.map || []).some((r) => ["red", "wheel", "cyan", "white"].includes(r))).length;
  $("#colour-reach").textContent = heads.length
    ? (colourable === heads.length ? `reaches all ${heads.length}` : `reaches ${colourable} of ${heads.length} - the rest have no colour mixing`)
    : "";
}

// Black-body colour for a white temperature (Tanner Helland's fit).
export function kelvinHex(k) {
  const t = k / 100;
  let r, g, b;
  if (t <= 66) { r = 255; g = 99.47 * Math.log(t) - 161.12; b = t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04; }
  else { r = 329.7 * Math.pow(t - 60, -0.1332); g = 288.12 * Math.pow(t - 60, -0.0755); b = 255; }
  const c = (v) => Math.max(0, Math.min(255, Math.round(v)));
  return rgbToHex(c(r), c(g), c(b));
}

function renderWheel() {
  const cap = capabilities();
  $("#kelvin-row").hidden = !cap.mixing && hasSel();
  const box = $("#wheel-steps");
  box.hidden = !cap.roles.has("wheel");
  if (box.hidden) return;
  const wheel = attrEntry("wheel");
  const key = wheel && wheel.slots ? JSON.stringify(wheel.slots) : "guess";
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(h("span.muted.small", wheel && wheel.slots ? "Colour wheel:" : "Colour wheel (guessed positions):"), ...slotButtons("wheel", wheel));
}

// The attribute entry for one role, from the last /attributes read.
function attrEntry(role) {
  const attrs = attrState && attrState.pages ? attrState.pages.flatMap((p) => p.attrs || []) : [];
  return attrs.find((a) => a.role === role) || null;
}

// Buttons for a wheel: the fixture's own named slots (each sent to the
// middle of its range) when the file lists them, otherwise 8 even guesses.
function slotButtons(role, entry) {
  const send = (value) => () => run("set_attribute", { attribute: role, value }).then(loadAttributes);
  if (entry && entry.slots && entry.slots.length) {
    return entry.slots.map((s) => h("button.chip.slot", {
      title: `${s.name} - DMX ${s.from}-${s.to}`, onclick: send(s.value),
    }, s.hex ? h("i.slot-dot", { style: { background: s.hex } }) : null, s.name));
  }
  return Array.from({ length: 8 }, (_, i) => h("button.chip", {
    title: `DMX ${i * 16 + 8} - this fixture's file lists no slots, so these are guesses`,
    onclick: send(i * 16 + 8),
  }, i === 0 ? "Open" : String(i)));
}

// ------------------------------------------------------------ position
const sendPad = throttle((pan, tilt) => run("set_position", { pan, tilt, unit: "255" }), 70);

function renderPad() {
  const first = sel()[0];
  const look = first !== undefined ? state.looks[first] : null;
  const dot = $("#pad-dot");
  if (look && typeof look.pan === "number") {
    dot.style.left = look.pan * 100 + "%";
    dot.style.top = (1 - (look.tilt ?? 0.5)) * 100 + "%";
    $("#pad-read").textContent = `pan ${Math.round(look.pan * 255)} · tilt ${Math.round((look.tilt ?? 0) * 255)}`
      + (look.deg && look.deg.pan ? `  (${Math.round(look.deg.pan[0] + look.pan * (look.deg.pan[1] - look.deg.pan[0]))}°, ${Math.round(look.deg.tilt[0] + (look.tilt ?? 0) * (look.deg.tilt[1] - look.deg.tilt[0]))}°)` : "");
  } else {
    $("#pad-read").textContent = hasSel() ? "not driven - drag the pad to aim" : "select moving heads to aim them";
  }
}

function renderMarks() {
  const s = $("#aim-mark");
  const marks = (((state.snap && state.snap.venue) || {}).objects || []).filter((o) => o.kind === "mark");
  const key = marks.map((m) => m.name).join("|");
  if (s.dataset.key === key) return;
  s.dataset.key = key;
  s.replaceChildren(h("option", { value: "" }, marks.length ? "Aim at a mark…" : "No marks (add one in Arrange)"),
    ...marks.map((m) => h("option", { value: m.name }, m.name || "mark")));
}

function wirePad() {
  $("#aim-spot").addEventListener("click", () => {
    if (!hasSel()) { toast("Select the lights to aim first"); return; }
    import("./stagepanel.js").then((m) => {
      const st = m.getStage();
      if (!st) return;
      toast("Click a spot in the 3D view (Esc cancels)", "", 4000);
      st.pickPoint((p) => run("aim_at", { x: +p.x.toFixed(2), y: +p.y.toFixed(2), z: +p.z.toFixed(2) }, { toast: true }));
    });
  });
  $("#aim-mark").addEventListener("change", (e) => {
    const name = e.target.value;
    e.target.value = "";
    if (name) run("aim_at", { mark: name }, { toast: true });
  });
  $("#aim-home").addEventListener("click", () => run("set_position", { pan: 128, tilt: 128, unit: "255" }, { toast: true }));
  const pad = $("#pad");
  let dragging = false;
  const at = (e) => {
    const r = pad.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    $("#pad-dot").style.left = x * 100 + "%";
    $("#pad-dot").style.top = y * 100 + "%";
    sendPad(Math.round(x * 255), Math.round((1 - y) * 255));
  };
  pad.addEventListener("pointerdown", (e) => { dragging = true; pad.setPointerCapture(e.pointerId); at(e); });
  pad.addEventListener("pointermove", (e) => { if (dragging) at(e); });
  pad.addEventListener("pointerup", () => { dragging = false; });
  $("#aim-btn").addEventListener("click", () => {
    const pan = $("#aim-pan").value, tilt = $("#aim-tilt").value;
    const params = { unit: "degree" };
    if (pan !== "") params.pan = +pan;
    if (tilt !== "") params.tilt = +tilt;
    run("set_position", params, { toast: true });
  });
}

// ---------------------------------------------------------- attributes
async function loadAttributes() {
  clearTimeout(attrTimer);
  if (!hasSel()) { attrState = null; renderAttributes(); return; }
  attrTimer = setTimeout(async () => {
    try {
      attrState = await get("/api/console/attributes?heads=" + sel().join(","));
      renderAttributes();
    } catch (e) { /* the grid just stays as it was */ }
  }, 60);
}

const sendAttr = throttle((attribute, value) => run("set_attribute", { attribute, value }, { silentError: true }), 70);

// One-press values for the beam: strobe speeds from the fixture's own
// "open" value, numbered gobo/prism steps, and zoom/iris/frost extremes.
function renderBeamQuick() {
  const box = $("#beam-quick");
  const attrs = attrState && attrState.pages ? attrState.pages.flatMap((p) => p.attrs || []) : [];
  const by = Object.fromEntries(attrs.map((a) => [a.role, a]));
  const rows = [];
  const set = (role, value) => () => run("set_attribute", { attribute: role, value }).then(loadAttributes);
  const strobe = by.strobe || by.shutter;
  if (strobe) {
    const open = strobe.open ?? 0;
    const lo = Math.max(open + 8, 64);
    rows.push(h("div.chip-row", h("span.k", "Strobe"),
      h("button.chip", { title: `DMX ${open}`, onclick: set(strobe.role, open) }, "Off"),
      h("button.chip", { title: `DMX ${lo}`, onclick: set(strobe.role, lo) }, "Slow"),
      h("button.chip", { title: `DMX ${Math.round((lo + 255) / 2)}`, onclick: set(strobe.role, Math.round((lo + 255) / 2)) }, "Medium"),
      h("button.chip", { title: "DMX 250", onclick: set(strobe.role, 250) }, "Fast")));
  }
  // A shutter whose "open" value nobody knows sits at 0 - closed on many
  // movers - so the light tilts but never lights.  Find it on the real
  // light, once, and Jarvis remembers it for every head of that model.
  if (strobe) {
    const first = selectionHeads()[0];
    const openV = strobe.open ?? 0;
    const start = strobe.value ?? openV;
    const find = h("input", { type: "range", min: 0, max: 255, value: start });
    const val = h("span.mono.small", String(start));
    let tmo = 0;
    find.addEventListener("input", () => {
      val.textContent = find.value;
      clearTimeout(tmo);
      tmo = setTimeout(() => run("set_attribute", { attribute: strobe.role, value: +find.value }), 50);
    });
    rows.push(h("div.open-find" + (strobe.open_known ? "" : ".unknown"),
      h("div.small", strobe.open_known
        ? `Opens at ${openV}. If the real light stays dark on Full, find its open value:`
        : "Jarvis doesn't know which value opens this light's shutter, so Full may leave it dark. Put the dimmer up, slide until the REAL light comes on, then press This is open:"),
      h("div.fx-row", h("span.k", strobe.role === "shutter" ? "Shutter" : "Strobe"), find, val),
      h("button.btn.small.primary", {
        onclick: () => first && run("remember_open", { head: first.head_no, value: +find.value }, { toast: true }).then(loadAttributes),
      }, "This is open")));
  }
  for (const role of ["gobo", "gobo2"]) {
    if (!by[role]) continue;
    rows.push(h("div.chip-row", h("span.k", role === "gobo" ? "Gobo" : "Gobo 2"), ...slotButtons(role, by[role])));
  }
  if (by.prism) rows.push(h("div.chip-row", h("span.k", "Prism"), h("button.chip", { onclick: set("prism", 0) }, "Out"), h("button.chip", { onclick: set("prism", 128) }, "In")));
  for (const [role, label, a, b] of [["zoom", "Zoom", "Narrow", "Wide"], ["iris", "Iris", "Open", "Closed"], ["frost", "Frost", "Off", "Full"], ["focus", "Focus", "Near", "Far"]]) {
    if (!by[role]) continue;
    const full = by[role].full || 255;
    rows.push(h("div.chip-row", h("span.k", label), h("button.chip", { onclick: set(role, 0) }, a),
      h("button.chip", { onclick: set(role, Math.round(full / 2)) }, "Half"), h("button.chip", { onclick: set(role, full) }, b)));
  }
  box.replaceChildren(...rows);
}

// While a bar is held, a live update must not rebuild the list: the bar
// under the pointer would be replaced and the drag would stop mid-way.
let attrHeld = false;

function renderAttributes() {
  if (attrHeld) return;
  if (tab === "laser" || tab === "sfx") import("./fxpanel.js").then((m) => m.renderFxPane(tab, attrState));
  renderBeamQuick();
  renderWheel();
  const box = $("#attr-list");
  if (!attrState || !attrState.pages) {
    box.replaceChildren(h("p.muted.small", "Select fixtures to see every attribute they have."));
    return;
  }
  const rows = [];
  // a light's own extra channels (continuous pan, built-in programs...)
  // first: at the bottom of a long list nobody found them
  const pages = [...attrState.pages].sort((x, y) => (y.page === "other") - (x.page === "other"));
  for (const page of pages) {
    if (page.page === "intensity") continue;
    const attrs = (page.attrs || []).filter((a) => !["red", "green", "blue", "pan", "tilt", "pan_fine", "tilt_fine"].includes(a.role));
    if (!attrs.length) continue;
    rows.push(h("div.attr-page", page.page === "other" ? "More channels" : page.page));
    for (const a of attrs) rows.push(attrRow(a));
  }
  box.replaceChildren(...(rows.length ? rows : [h("p.muted.small", "These fixtures have no beam attributes.")]));
}

// What an operator calls each role (the raw role name is the tooltip).
const ATTR_NAMES = {
  wheel: "Colour wheel", wheel2: "Colour wheel 2", gobo: "Gobo", gobo2: "Gobo 2", gobo_rot: "Gobo rotate",
  gobo2_rot: "Gobo 2 rotate", prism: "Prism", prism_rot: "Prism rotate", focus: "Focus", zoom: "Zoom",
  iris: "Iris", frost: "Frost", speed: "Move speed", shutter: "Shutter", strobe: "Strobe", cto: "Colour temp",
  white: "White", amber: "Amber", uv: "UV", lime: "Lime", cyan: "Cyan", magenta: "Magenta", yellow: "Yellow",
  control: "Control", fx_mode: "Mode", fx_param: "Setting", macro: "Macro", animation: "Animation",
};
const attrName = (role) => ATTR_NAMES[role] || role.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const slotAt = (a, v) => (a.slots || []).find((s) => v >= s.from && v <= s.to) || null;

function attrRow(a) {
  const full = a.full || 255;
  const val = a.value === null || a.value === undefined ? null : a.value;
  const pct = (v) => (v / full * 100) + "%";
  const fill = h("div.fill", { style: { width: val === null ? "0%" : pct(val) } });
  const knob = h("div.knob", { style: { left: val === null ? "0%" : pct(val) }, hidden: val === null });
  const bar = h("div.hbar", { tabindex: 0, role: "slider", "aria-label": attrName(a.role), "aria-valuemin": 0,
    "aria-valuemax": full, "aria-valuenow": val ?? 0, title: "Drag, or use the arrow keys (Shift = 10). Double-click to clear." }, fill, knob);
  const show = (v) => {
    const slot = slotAt(a, v);
    return slot ? slot.name : String(Math.round(v));
  };
  const out = h("output", { title: val === null ? "" : `DMX ${Math.round(val)}` },
    val === null ? "–" : a.mixed ? "mix" : show(val));
  const clear = () => run("set_attr_range", { attribute: a.role, clear: true }).then(loadAttributes);
  const row = h("div.attr" + (a.set ? ".set" : "") + (a.partial ? ".partial" : ""),
    h("label", { title: a.partial ? `${a.role}: only ${a.heads} of the selection have it` : a.role }, a.name || attrName(a.role)),
    bar, out,
    h("button.clr", { title: "Remove from the programmer", onclick: clear }, "×"));
  let cur = val ?? 0;
  const apply = (v) => {
    cur = Math.max(0, Math.min(full, Math.round(v)));
    fill.style.width = pct(cur);
    knob.style.left = pct(cur);
    knob.hidden = false;
    out.textContent = show(cur);
    out.title = `DMX ${cur}`;
    bar.setAttribute("aria-valuenow", cur);
    row.classList.add("set");
    sendAttr(a.role, cur);
  };
  const fromPointer = (e) => {
    const r = bar.getBoundingClientRect();
    apply(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * full);
  };
  let dragging = false;
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();                     // no text selection, and keep focus for the arrow keys
    bar.focus({ preventScroll: true });
    dragging = true;
    attrHeld = true;
    bar.setPointerCapture(e.pointerId);
    row.classList.add("drag");
    fromPointer(e);
  });
  bar.addEventListener("pointermove", (e) => { if (dragging) fromPointer(e); });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    attrHeld = false;
    row.classList.remove("drag");
    setTimeout(loadAttributes, 250);
  };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
  bar.addEventListener("lostpointercapture", end);
  bar.addEventListener("dblclick", clear);
  let keyT = 0;
  bar.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 1;
    const d = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step }[e.key];
    if (e.key === "Home") apply(0);
    else if (e.key === "End") apply(full);
    else if (d) apply(cur + d);
    else return;
    e.preventDefault();
    clearTimeout(keyT);
    keyT = setTimeout(loadAttributes, 600);
  });
  if (!a.slots || !a.slots.length || a.mixed) return row;
  // a wheel / gobo: its named slots as one-tap chips under the bar
  const chips = h("div.attr-slots", ...a.slots.map((s) => h("button.chip.slot" + (val !== null && val >= s.from && val <= s.to ? ".on" : ""), {
    title: `${s.name} - DMX ${s.from}-${s.to}`,
    onclick: () => { apply(s.value); setTimeout(loadAttributes, 150); },
  }, s.hex ? h("i.slot-dot", { style: { background: s.hex } }) : null, s.name)));
  return h("div.attr-wrap", row, chips);
}

// ------------------------------------------------------ in the programmer
// What the programmer holds, by kind, each with its own x: clear just the
// colour (or the position...) without losing the rest.
const IN_GROUPS = [["intensity", "Level"], ["colour", "Colour"], ["position", "Move"], ["beam", "Beam"], ["other", "Other"]];
const GROUP_OF = (() => {
  const m = {};
  for (const r of ["dimmer", "zone_dimmer", "shutter", "strobe"]) m[r] = "intensity";
  for (const r of ["red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "wheel", "macro"]) m[r] = "colour";
  for (const r of ["pan", "tilt", "speed"]) m[r] = "position";
  for (const r of ["gobo", "gobo_rot", "prism", "zoom", "focus", "frost", "iris"]) m[r] = "beam";
  return m;
})();
const MOVE_FX = new Set(["circle", "figure_eight", "pan_sweep", "tilt_bounce", "fan_pan"]);
let progInKey = "";
let fxGridKey = "";
let runningKey = "";
let looksKey = "";

function renderProgIn() {
  const box = $("#prog-in");
  const vals = ((state.snap && state.snap.programmer) || {}).values || {};
  const counts = {};
  for (const row of Object.values(vals)) {
    const seen = new Set();
    for (const r of Object.keys(row)) seen.add(GROUP_OF[r.replace(/_fine$/, "")] || "other");
    for (const g of seen) counts[g] = (counts[g] || 0) + 1;
  }
  const moving = ((state.snap && state.snap.fx) || []).some((f) => MOVE_FX.has(f.lib));
  if (moving && !counts.position) counts.position = 0;
  const key = JSON.stringify(counts);
  if (key === progInKey) return;          // redraw only on change: keeps the x clickable
  progInKey = key;
  const groups = IN_GROUPS.filter(([g]) => g in counts);
  box.hidden = !groups.length;
  box.replaceChildren(...(groups.length ? [h("span.muted.small", "In the programmer:"),
    ...groups.map(([g, label]) => h("span.chip.prog-in-chip",
      { title: counts[g] ? `${label} on ${counts[g]} light(s)` : `${label}: a movement is running` },
      label, counts[g] ? h("small", ` ${counts[g]}`) : null,
      h("button.x", { title: `Clear ${label.toLowerCase()} only`, onclick: () => run("clear_attrs", { group: g }) }, "×"))),
    h("button.btn.small.ghost", { title: "Clear everything", onclick: () => run("clear_programmer") }, "Clear all")] : []));
}

// --------------------------------------------------------------- effects
async function loadFx() {
  if (!hasSel()) { fxAvailable = null; renderFx(); return; }
  const r = await run("fx_available", { heads: sel() }, { silentError: true });
  fxAvailable = r.ok ? r : null;
  renderFx();
}

function renderFx() {
  const grid = $("#fx-grid");
  if (!fxAvailable) {
    fxGridKey = "";
    grid.replaceChildren(h("p.muted.small", "Select fixtures to see the effects they can run."));
    $("#fx-params").replaceChildren();
  } else {
    const list = (fxAvailable.available || []).filter((fx) => !MOVE_FX.has(fx.name));
    const key = JSON.stringify([list.map((fx) => fx.name), fxChosen]);
    if (key !== fxGridKey) {               // redraw only on change: keeps the cards clickable
      fxGridKey = key;
      grid.replaceChildren(...list.map((fx) => h("button.fx-card" + (fxChosen === fx.name ? ".on" : ""), {
        title: "Run " + fx.label,
        onclick: () => startFx(fx),
      }, h("b", fx.label), h("small", fx.group))),
      h("p.muted.small.fx-move-note", "Movements (circle, sweep...) are on the ", h("button.linkish", { onclick: () => showTab("position") }, "Move tab"), "."));
    }
  }
  renderRunning();
  const attrs = new Set();
  for (const hd of selectionHeads()) for (const r of hd.map || []) if (!["raw", "unused"].includes(r) && !r.endsWith("_fine")) attrs.add(r);
  const lfo = $("#lfo-attr");
  const cur = lfo.value;
  lfo.replaceChildren(...[...attrs].sort().map((r) => h("option", { value: r }, r)));
  if (cur && attrs.has(cur)) lfo.value = cur;
}

function startFx(fx) {
  fxChosen = fx.name;
  const params = {};
  for (const p of fx.params || []) params[p.key] = p.default;
  run("run_fx", { name: fx.name, params, heads: sel() }, { toast: true }).then(() => renderFx());
  $("#fx-params").replaceChildren(...(fx.params || []).filter((p) => p.key !== "phase").map((p) => {
    const input = h("input", { type: "range", min: p.min, max: p.max, step: (p.max - p.min) / 100, value: p.default });
    const out = h("output.muted.small", String(p.default));
    input.addEventListener("input", () => { out.textContent = (+input.value).toFixed(2); });
    input.addEventListener("change", async () => {
      params[p.key] = +input.value;
      const running = ((state.snap && state.snap.fx) || []).filter((f) => f.lib === fx.name);
      for (const f of running) await run("stop_fx", { id: f.id }, { silentError: true });
      run("run_fx", { name: fx.name, params, heads: sel() });
    });
    return h("label.field", h("span", p.label, " ", out), input);
  }));
}

function renderRunning(force = false) {
  const list = (state.snap && state.snap.fx) || [];
  const box = $("#fx-running");
  // redraw only when the list changes: rebuilt on every live update, Stop
  // flickered and a click could land on a button already replaced
  const key = JSON.stringify(list.map((f) => [f.id, f.label, f.kind, f.role, (f.heads || []).length]));
  if (!force && key === runningKey) return;
  runningKey = key;
  if (!list.length) { box.replaceChildren(h("p.muted.small", "No effects running.")); return; }
  box.replaceChildren(...list.map((f) => h("div.fx-run",
    h("b", f.label || `${f.kind || "wave"} ${f.role || ""}`),
    h("small", `${(f.heads || []).length} heads`),
    h("button.btn.small", { onclick: () => run("stop_fx", { id: f.id }) }, "Stop"))),
  h("button.btn.small.ghost", { onclick: () => run("stop_fx", {}) }, "Stop all"));
}

// ----------------------------------------------------------------- looks
function renderLooks(force = false) {
  const pals = (state.snap && state.snap.palettes) || {};
  const key = JSON.stringify([pals, (state.snap && state.snap.presets) || []]);
  if (!force && key === looksKey) return;  // (same: Record stays put under the pointer)
  looksKey = key;
  const kinds = ["colour", "position", "beam"];
  $("#pal-kinds").replaceChildren(...kinds.map((kind) => h("div.pal-row",
    h("div.pal-head", kind, h("button.btn.small.ghost", {
      title: `Record a ${kind} palette from the programmer`,
      onclick: async () => {
        if (!hasSel()) { toast("Select fixtures and set a look first"); return; }
        const name = await promptBox(`Record ${kind} palette`, "Name", "", { ok: "Record" });
        if (name) run("record_palette", { kind, name }, { toast: true });
      },
    }, "+ Record")),
    h("div.pal-items", ...(pals[kind] || []).map((p) => palButton(kind, p)),
      (pals[kind] || []).length ? null : h("span.muted.small", "none yet")))));
  const strip = $(".palette-strip[data-kind=position]");
  strip.replaceChildren(...(pals.position || []).map((p) => palButton("position", p)));
  const presets = (state.snap && state.snap.presets) || [];
  $("#preset-list").replaceChildren(...(presets.length ? presets.map((p) => h("button.pal-item", {
    title: `${p.heads} heads · click to apply, right-click to delete`,
    onclick: () => run("include_preset", { preset: p.n }),
    oncontextmenu: async (e) => {
      e.preventDefault();
      if (await confirmBox("Delete preset", `Delete preset “${p.name}”?`, { ok: "Delete", danger: true })) run("delete_preset", { preset: p.n });
    },
  }, p.name)) : [h("span.muted.small", "none yet")]));
}

function palButton(kind, p) {
  const v = p.values || {};
  const sw = kind === "colour" && ("red" in v || "green" in v || "blue" in v)
    ? h("i", { style: { background: rgbToHex(v.red || 0, v.green || 0, v.blue || 0) } }) : null;
  return h("button.pal-item", {
    title: `Apply ${p.name} to the selection`,
    onclick: () => {
      if (!hasSel()) { toast("Select fixtures first"); return; }
      run("include_palette", { kind, palette: p.n });
    },
  }, sw, p.name);
}

// ----------------------------------------------------------------- tools
function renderTools() {
  const roles = new Set();
  for (const hd of selectionHeads()) for (const r of hd.map || []) if (!["raw", "unused"].includes(r) && !r.endsWith("_fine")) roles.add(r);
  for (const id of ["#fan-attr", "#lim-role"]) {
    const s = $(id);
    const cur = s.value;
    s.replaceChildren(...[...roles].sort().map((r) => h("option", { value: r }, r)));
    if (cur && roles.has(cur)) s.value = cur;
  }
}

function wireTools() {
  $$("[data-arr]").forEach((b) => b.addEventListener("click", () =>
    run(b.dataset.arr, { heads: sel(), axis: $("#arr-axis").value }, { toast: true })));
  $("#fan-btn").addEventListener("click", () => {
    const lo = $("#fan-lo").value, hi = $("#fan-hi").value;
    if (lo === "" || hi === "") { toast("Give a from and a to value"); return; }
    run("fan", { attribute: $("#fan-attr").value, from_value: +lo, to_value: +hi, mode: $("#fan-mode").value }, { toast: true });
  });
  $("#lim-set").addEventListener("click", () => {
    run("set_limits", { heads: sel(), role: $("#lim-role").value, low: +($("#lim-lo").value || 0), high: +($("#lim-hi").value || 255) }, { toast: true });
  });
  $("#lim-clear").addEventListener("click", () => run("clear_limits", { heads: sel(), role: $("#lim-role").value }, { toast: true }));
  $$("[data-orient]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.orient;
    run("set_orient", k === "clear" ? { heads: sel(), clear: true } : { heads: sel(), [k]: true }, { toast: true });
  }));
  $("#sel-similar").addEventListener("click", () => {
    const first = sel()[0];
    if (first === undefined) { toast("Select one fixture of the type you want"); return; }
    run("select_similar", { head: first });
  });
  $$("[data-query]").forEach((b) => b.addEventListener("click", () => run("select_query", { role: b.dataset.query }, { toast: true })));
}

// ------------------------------------------------------------------ tabs
function showTab(name) {
  tab = name;
  $$("#prog-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$("#prog-body .tab-pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
  renderHeader();
  if (name === "beam" || name === "colour") loadAttributes();
  if (name === "fx") loadFx();
  if (name === "looks") renderLooks(true);
  if (name === "tools") renderTools();
  if (name === "colour") renderColour();
  if (name === "position") { renderPad(); renderMarks(); }
  if (name === "laser" || name === "sfx") {
    loadAttributes();
    import("./fxpanel.js").then((m) => m.renderFxPane(name, attrState));
  }
  try { localStorage.setItem("jarvis.progtab", name); } catch (e) { /* ignore */ }
}

export function focusTab(name) { showTab(name); }

export function initProgrammer() {
  intFader = vfader($("#int-fader"), { min: 0, max: 100, onInput: (v) => { $("#int-num").textContent = v + "%"; sendIntensity(v); } });
  $$("#int-quick button").forEach((b) => b.addEventListener("click", () => {
    if (!hasSel()) { toast("Select fixtures first"); return; }
    sendIntensity(+b.dataset.level);
  }));
  $("#locate-btn").addEventListener("click", () => run("locate"));
  $("#clear-btn").addEventListener("click", () => run("clear_programmer"));
  const rec = $("#highlight-btn");
  rec.textContent = "Record cue…";
  rec.title = "Record the programmer as a cue (R)";
  rec.addEventListener("click", () => openCueDialog());

  picker = createPicker($("#picker-canvas"), (hex, final) => {
    if (!hasSel()) return;
    $("#hex-in").value = hex;
    sendColour(hex);
    void final;
  });
  $("#swatches").replaceChildren(...SWATCHES.map(([name, hex]) => h("button", {
    title: name, dataset: { hex }, style: { background: hex },
    onclick: () => { if (!hasSel()) { toast("Select fixtures first"); return; } picker.set(hex); run("set_colour", { hex }); },
  })));
  $("#hex-in").addEventListener("change", (e) => {
    const v = e.target.value.trim();
    if (/^#?[0-9a-f]{6}$/i.test(v)) run("set_colour", { hex: v.startsWith("#") ? v : "#" + v });
    else toast("Colour as #rrggbb", "bad");
  });
  wirePad();
  wireTools();
  const kel = $("#kelvin");
  kel.addEventListener("input", () => {
    $("#kelvin-out").textContent = kel.value + " K";
    if (!hasSel()) return;
    const hex = kelvinHex(+kel.value);
    picker.set(hex);
    sendColour(hex);
  });
  $("#lfo-run").addEventListener("click", () => run("run_fx", {
    attribute: $("#lfo-attr").value, kind: $("#lfo-wave").value,
    speed: +$("#lfo-speed").value || 1, spread: +$("#lfo-spread").value || 0, heads: sel(),
  }, { toast: true }));
  $("#preset-rec").addEventListener("click", async () => {
    const name = await promptBox("Record preset", "Name for this look", "", { ok: "Record" });
    if (name) run("record_preset", { name }, { toast: true });
  });
  $$("#prog-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  let saved = "intensity";
  try { saved = localStorage.getItem("jarvis.progtab") || saved; } catch (e) { /* ignore */ }
  showTab(saved);

  const refresh = () => {
    renderHeader();
    renderProgIn();
    renderIntensity();
    if (tab === "colour") renderColour();
    if (tab === "fx") renderRunning();
    if (tab === "looks") renderLooks();
  };
  on("snapshot", () => {
    refresh();
    if (["beam", "colour", "laser", "sfx"].includes(tab)) loadAttributes();
    if (tab === "tools") renderTools();
  });
  on("lite", refresh);
  on("selection", () => {
    refresh();
    if (["beam", "colour", "laser", "sfx"].includes(tab)) loadAttributes();
    if (tab === "fx") loadFx();
    if (tab === "tools") renderTools();
    if (tab === "position") renderPad();
  });
  on("looks", () => { if (tab === "position") renderPad(); });
  void patch; void select;
}
