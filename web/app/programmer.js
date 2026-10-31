// The programmer: what the selected heads are being told to do.
import { get } from "./api.js";
import { state, on, patch, selected, selectionHeads } from "./store.js";
import { run, select } from "./actions.js";
import { $, $$, h, vfader, throttle, toast, promptBox, confirmBox, modal, menu } from "./ui.js";
import { paletteOn, paletteFits, paletteSwatch } from "./deskview.js";
import { knob, SPEED_PRESETS, TURN_PRESETS, SIZE_PRESETS } from "./speedpick.js";
import { createPicker, rgbToHex } from "./picker.js";
import { openCueDialog, openLightTest } from "./dialogs.js";
import { icon } from "./icons.js";
import { focusedPlayback } from "./playbacks.js";
import { renderWheels, initWheels } from "./wheels.js";
import { goboIcon, goboRef } from "./goboicon.js";

const SWATCHES = [
  ["Red", "#ff2a1f"], ["Orange", "#ff7a00"], ["Amber", "#ffb000"], ["Yellow", "#ffe600"],
  ["Green", "#1aff4a"], ["Cyan", "#00e5ff"], ["Blue", "#1f4bff"], ["Congo", "#5b2bff"],
  ["UV", "#8f00ff"], ["Magenta", "#ff00d4"], ["Pink", "#ff0080"], ["Light pink", "#ff5fa2"], ["Lavender", "#b69cff"],
  ["Warm", "#ffd9a8"], ["Neutral", "#fff1dc"], ["Cool", "#e6f0ff"], ["White", "#ffffff"],
];

let tab = "desk";
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
const LED_ROLES = new Set(["red", "green", "blue", "white", "amber", "uv", "lime"]);
const BEAM_ROLES = ["zoom", "focus", "iris", "gobo", "gobo_rot", "prism", "frost", "shutter", "strobe", "prism_rot", "gobo2"];

function capabilities(heads = selectionHeads()) {
  // a laser's or an effect machine's own colour / beam channels are in the
  // Laser and SFX tabs (colour and level don't reach them): the light tabs
  // count real lights only.  Moving counts anything that pans or tilts (a
  // CO2 jet that tilts).
  const roles = new Set(), lightRoles = new Set();
  for (const hd of heads) {
    // (an effects machine with LEDs of its own - an LED CO2 jet - counts for colour and level)
    const isLight = !hd.body || !hd.body.class || hd.body.class === "light"
      || (hd.body.class === "sfx" && (hd.map || []).some((r) => LED_ROLES.has(r)));
    for (const r of hd.map || []) { roles.add(r); if (isLight) lightRoles.add(r); }
  }
  return {
    roles, lightRoles,
    position: roles.has("pan") || roles.has("tilt"),
    colour: COLOUR_ROLES.some((r) => lightRoles.has(r)),
    // mixes ANY colour: all three of red / green / blue (or of cyan /
    // magenta / yellow).  A red + green light makes red, green and yellow,
    // and nothing else: it gets its own colours, not the picker.
    mixing: ["red", "green", "blue"].every((r) => lightRoles.has(r)) || ["cyan", "magenta", "yellow"].every((r) => lightRoles.has(r)),
    beam: BEAM_ROLES.some((r) => lightRoles.has(r)),
  };
}

function applyTabVisibility() {
  const any = hasSel();
  // nothing selected: the tabs the rig can use at all (no moving heads in
  // the patch, no Position tab); an empty show shows them all
  const rig = (state.snap && state.snap.patch) || [];
  const cap = any ? capabilities() : capabilities(rig);
  const none = !any && !rig.length;
  // only SFX machines / hazers / lasers selected: no Level or colour-effect
  // tabs (they did nothing for a confetti cannon)
  const LIGHTISH = new Set(["dimmer", "shutter", "strobe", ...COLOUR_ROLES]);
  const light = [...cap.lightRoles].some((r) => LIGHTISH.has(r));
  const show = { intensity: none || light, fx: none || light,
    position: none || cap.position, colour: none || cap.colour, beam: none || cap.beam,
    laser: any && [...cap.roles].some((r) => r.startsWith("laser_")),
    sfx: any && [...cap.roles].some((r) => r.startsWith("fx_") || r === "fog") && ![...cap.roles].some((r) => r.startsWith("laser_")) };
  $$("#prog-tabs button").forEach((b) => {
    const t = b.dataset.tab;
    const visible = show[t] === undefined ? true : show[t];
    if (b.dataset.title === undefined) b.dataset.title = b.title;   // kept while hidden
    b.hidden = !visible;
    b.title = visible ? b.dataset.title : "";
  });
  if (show[tab] === false) showTab(show.laser ? "laser" : show.sfx ? "sfx" : show.intensity ? "intensity" : "looks");
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
  $("#prog-body").classList.toggle("disabled", !heads.length && !["looks", "fx", "desk"].includes(tab));
  applyTabVisibility();
  const attrs = new Set(((state.snap && state.snap.programmer) || {}).attrs || []);
  // the same kinds as the "In the programmer" bar (GROUP_OF): a shutter is
  // Level in both, so the dot and the bar never disagree
  const pages = { intensity: "intensity", colour: "colour", position: "position", beam: "beam" };
  $$("#prog-tabs button").forEach((b) => {
    const dot = b.querySelector(".dotmark");
    const want = pages[b.dataset.tab];
    const on = !!want && [...attrs].some((r) => (GROUP_OF[r.replace(/@\d+$/, "").replace(/_fine$/, "")] || "other") === want);
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
  box.replaceChildren(h("span", `${models.join(", ")}: the desk doesn't know which value opens the shutter, so Full may leave it dark. `),
    h("button.btn.small.primary", { onclick: () => openLightTest(bad[0]) }, "Test this light"), " ",
    h("button.btn.small", { onclick: () => showTab("beam") }, "Find it in Beam"));
}

function renderGate() {
  renderOpenWarning();
  const box = $("#int-gate");
  const sel = selectionHeads();
  const heads = sel.filter((x) => !(x.map || []).includes("dimmer")
    && (x.map || []).some((r) => r === "shutter" || r === "strobe"));
  // every selected light is one whose brightness is only its shutter (no
  // dimmer, no colour mixing to dim with): the fader would do nothing, so
  // the shutter leads, as big buttons
  const gateOnly = heads.length > 0 && heads.length === sel.length
    && sel.every((x) => !(x.map || []).some((r) => ["red", "green", "blue", "white", "zone_dimmer"].includes(r)));
  $(".big-fader-row").hidden = gateOnly;
  box.classList.toggle("gate-only", gateOnly);
  box.hidden = !heads.length;
  if (!heads.length) { box.dataset.key = ""; return; }
  const role = (heads[0].map || []).includes("shutter") ? "shutter" : "strobe";
  const vals = heads.map((x) => (((state.snap && state.snap.programmer) || {}).values || {})[x.head_no]).map((r) => r && r[role]);
  const now = vals.every((v) => v === undefined) ? "" : vals.every((v) => v === 0) ? "closed" : vals.every((v) => v > 0) ? "open" : "mixed";
  const key = [heads.map((x) => x.head_no).join(","), gateOnly, now].join("|");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  const nums = heads.map((x) => x.head_no);
  const cls = gateOnly ? "button.btn.gate-btn" : "button.chip";
  const what = role === "shutter" ? "shutter" : "strobe channel";
  box.replaceChildren(h("span.muted.small", gateOnly
    ? `${heads.length === 1 ? "This light has" : "These lights have"} no dimmer: its ${what} turns it on and off.`
    : `${heads.length === 1 ? "1 light has" : `${heads.length} lights have`} no dimmer - the fader dims ${heads.length === 1 ? "its" : "their"} colour. ${heads.length === 1 ? "Its" : "Their"} ${what}:`),
    h(cls + (now === "open" ? ".on" : ""), { onclick: async () => {
      const a = await get("/api/console/attributes?heads=" + nums.join(","));
      const at = (a.pages || []).flatMap((p) => p.attrs || []).find((x) => x.role === role);
      run("set_attribute", { attribute: role, value: at && at.open !== undefined ? at.open : 255, heads: nums });
    } }, gateOnly ? "On (open)" : "Open - light on"),
    h(cls + (now === "closed" ? ".on" : ""), { onclick: () => run("set_attribute", { attribute: role, value: 0, heads: nums }) },
      gateOnly ? "Off (closed)" : "Closed - dark"),
    gateOnly ? h("span.muted.small", "Strobe speeds and effects are on the Beam tab.") : null);
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

// An RGBW / RGBA / UV light's extra LEDs, each its own slider: a picked
// colour stays pure (no white mixed in), and white, amber or UV go on top
// only when you ask for them.
const EXTRA = [["white", "White", "#f5f5f0"], ["amber", "Amber", "#ffb000"], ["uv", "UV", "#8b5cf6"], ["lime", "Lime", "#b5ff4d"]];
const OWN = [["red", "Red", "#ff3b30"], ["green", "Green", "#34c759"], ["blue", "Blue", "#3b6cff"],
  ["cyan", "Cyan", "#22d3ee"], ["magenta", "Magenta", "#e83e8c"], ["yellow", "Yellow", "#ffd60a"], ...EXTRA];
// the colours a light that can't mix everything CAN make: each emitter
// alone, and red / green / blue in pairs (red + green is yellow)
const MAKES = [["Red", { red: 255 }], ["Green", { green: 255 }], ["Blue", { blue: 255 }],
  ["Yellow", { red: 255, green: 255 }], ["Magenta", { red: 255, blue: 255 }], ["Cyan", { green: 255, blue: 255 }],
  ["White", { white: 255 }], ["Amber", { amber: 255 }], ["UV", { uv: 255 }], ["Lime", { lime: 255 }]];
const MAKE_HEX = { Red: "#ff3b30", Green: "#34c759", Blue: "#3b6cff", Yellow: "#ffd60a", Magenta: "#e83e8c", Cyan: "#22d3ee",
  White: "#f5f5f0", Amber: "#ffb000", UV: "#8b5cf6", Lime: "#b5ff4d" };
let extraKey = "";
let extraHeld = 0;

function renderColourExtra() {
  const box = $("#colour-extra");
  const heads = selectionHeads();
  const roles = new Set(heads.flatMap((x) => x.map || []));
  const mixes = capabilities().mixing;
  // a light that mixes any colour: its extra emitters on top of the picker;
  // one that can't: every colour channel it has, each its own slider
  const list = mixes ? EXTRA.filter(([r]) => roles.has(r)) : hasSel() ? OWN.filter(([r]) => roles.has(r)) : [];
  const vals = ((state.snap && state.snap.programmer) || {}).values || {};
  const cur = (r) => {
    const v = heads.map((x) => (vals[x.head_no] || {})[r]).filter((x) => x !== undefined);
    return v.length ? Math.max(...v) : 0;
  };
  const key = JSON.stringify([list.map(([r]) => [r, cur(r)]), heads.map((x) => x.head_no)]);
  if (key === extraKey || Date.now() < extraHeld) return;
  extraKey = key;
  box.replaceChildren(...(list.length ? [h("span.muted.small", mixes ? "Added on top of the colour:" : "Its colour channels:"), ...list.map(([role, label, tint]) => {
    const input = h("input", { type: "range", min: 0, max: 255, value: cur(role), style: { accentColor: tint } });
    const out = h("output.mono.small", `${Math.round(cur(role) / 2.55)}%`);
    const send = throttle((v) => run("set_attribute", { attribute: role, value: v }, { silentError: true }), 60);
    input.addEventListener("input", () => {
      extraHeld = Date.now() + 800;                 // don't redraw under the finger
      out.textContent = `${Math.round(+input.value / 2.55)}%`;
      send(+input.value);
    });
    return h("label.extra-row", h("i.extra-dot", { style: { background: tint } }), h("span", label), input, out);
  })] : []));
}

function renderColour() {
  renderColourExtra();
  const hex = currentHex();
  if (hex) {
    picker.set(hex);
    if (document.activeElement !== $("#hex-in")) $("#hex-in").value = hex;
  }
  $$("#swatches button").forEach((b) => b.classList.toggle("on", !!hex && b.dataset.hex === hex));
  renderWheel();
  const heads = selectionHeads();
  const full = (x) => { const m = x.map || []; return ["red", "green", "blue"].every((r) => m.includes(r)) || ["cyan", "magenta", "yellow"].every((r) => m.includes(r)); };
  const mixers = heads.filter(full).length;
  $("#colour-reach").textContent = !heads.length ? ""
    : mixers === heads.length ? (heads.length === 1 ? "this light mixes any colour" : `all ${heads.length} mix any colour`)
      : mixers ? `${mixers} of ${heads.length} mix any colour - the rest only make their own colours`
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

// A light with only a colour wheel can't mix any colour: its own wheel
// colours lead (big buttons), and the picker - which can only land on the
// nearest wheel colour - waits behind "Pick any colour".
let pickerAnyway = false;

// A light patched in a mode that can't mix colour (an RGB bar in its
// "colour macro" mode) when another mode of it can: say so, one click away.
let modeHintKey = "";
function renderModeHint() {
  const box = $("#mode-hint");
  if (!box) return;
  const heads = patch().filter((x) => sel().includes(x.head_no) && x.better_mode);
  const k = JSON.stringify(heads.map((x) => [x.head_no, x.mode, x.better_mode]));
  if (k === modeHintKey) return;
  modeHintKey = k;
  box.hidden = !heads.length;
  if (!heads.length) { box.replaceChildren(); return; }
  const x = heads[0];
  box.replaceChildren(
    h("div", h("b", `${x.model} is in its “${x.mode}” mode`), ` - it can only pick its built-in colours there. Its “${x.better_mode}” mode mixes any colour.`),
    h("div.row-btns", h("button.btn.small.primary", {
      title: "Re-patch these lights in that mode (they keep their place, groups and cues). Set the real lights to the same mode in their own menu.",
      onclick: async () => {
        const ok = await confirmBox("Change mode", `Patch ${heads.length === 1 ? "this light" : `these ${heads.length} lights`} in the “${x.better_mode}” mode? Set the real light${heads.length === 1 ? "" : "s"} to that mode too (in ${heads.length === 1 ? "its" : "their"} own menu) - the DMX address may move.`, { ok: "Change mode" });
        if (ok) run("change_type", { heads: heads.map((y) => y.head_no), query: `${x.manufacturer} ${x.model}`, mode: x.better_mode }, { toast: true });
      } }, `Use the “${x.better_mode}” mode`)));
}

function renderWheel() {
  renderModeHint();
  const cap = capabilities();
  $("#kelvin-row").hidden = !cap.mixing && hasSel();
  const box = $("#wheel-steps");
  const makes = hasSel() && !cap.mixing ? MAKES.filter(([, set]) => Object.keys(set).every((r) => cap.lightRoles.has(r))) : [];
  box.hidden = !cap.roles.has("wheel") && !makes.length;
  const wheelOnly = hasSel() && !box.hidden && !cap.mixing;
  const pane = $('[data-pane="colour"]');
  // a light that can't mix any colour: its own colours lead, no picker
  pane.classList.toggle("wheel-only", hasSel() && !cap.mixing && (cap.roles.has("wheel") ? !pickerAnyway : makes.length > 0));
  if (box.hidden) return;
  if (!cap.roles.has("wheel")) {
    const key = "own|" + makes.map(([n]) => n).join(",");
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    const all = [...new Set(MAKES.flatMap(([, set]) => Object.keys(set)))].filter((r) => cap.lightRoles.has(r));
    box.replaceChildren(h("span.muted.small", `These lights make ${makes.length === 1 ? "one colour" : `${makes.length} colours`}: tap one.`),
      ...makes.map(([name, set]) => h("button.chip.slot", { title: Object.keys(set).join(" + "),
        onclick: async () => {
          for (const r of all) await run("set_attribute", { attribute: r, value: set[r] || 0 }, { silentError: true });
          loadAttributes();
        } }, h("i.slot-dot", { style: { background: MAKE_HEX[name] } }), name)));
    return;
  }
  const wheel = attrEntry("wheel");
  const key = [wheel && wheel.slots ? JSON.stringify(wheel.slots) : "guess", wheelOnly, pickerAnyway].join("|");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(
    h("span.muted.small", wheelOnly
      ? "These lights have a colour wheel: tap one of its colours."
      : wheel && wheel.slots ? (whitesOnly(wheel.slots) ? "White presets:" : "Colour wheel:") : "Colour wheel (guessed positions):"),
    ...slotButtons("wheel", wheel),
    // the file doesn't say which colours they are: name them once (by
    // looking at the real light) and they're named everywhere after
    !(wheel && wheel.slots && wheel.slots.some((x) => x.hex)) ? h("div.wheel-teach",
      h("span.muted.small", "The fixture file doesn't say which colours these are."),
      h("button.btn.small", { title: "Step through the wheel on the real light and name each colour (once for this model)",
        onclick: () => import("./teachwheel.js").then((m) => m.openTeachWheel("wheel", () => { box.dataset.key = ""; loadAttributes(); })) }, "Name the colours…")) : null,
    wheelOnly ? h("button.linkish.small", { onclick: () => { pickerAnyway = !pickerAnyway; box.dataset.key = ""; renderWheel(); } },
      pickerAnyway ? "Hide the colour picker" : "Pick any colour (goes to the nearest wheel colour)") : null);
}

// The attribute entry for one role, from the last /attributes read.
function attrEntry(role) {
  const attrs = attrState && attrState.pages ? attrState.pages.flatMap((p) => p.attrs || []) : [];
  return attrs.find((a) => a.role === role) || null;
}

// Buttons for a wheel: the fixture's own named slots (each sent to the
// middle of its range) when the file lists them, otherwise 8 even guesses.
// a slot's name as a chip says it: "GoboShake Wheel Slot 3" -> "Shake 3",
// "Wheel Slot 0 OPEN" -> "Open" (the whole name is its tooltip)
function slotLabel(name) {
  const n = String(name || "").replace(/\bgobo\s*shake\b/i, "Shake").replace(/\b(gobo\s*)?wheel\s*slot\s*/i, "").replace(/\s+/g, " ").trim();
  const open = /^\d*\s*open$/i.test(n);
  return open ? "Open" : n || name;
}

function slotButtons(role, entry) {
  const send = (value) => () => run("set_attribute", { attribute: role, value }).then(loadAttributes);
  if (entry && entry.slots && entry.slots.length) {
    const gobo = role.startsWith("gobo");
    return entry.slots.map((s) => h("button.chip.slot" + (gobo ? ".gobo" : ""), {
      title: `${s.name} - DMX ${s.from}-${s.to}`, onclick: send(s.value),
    }, gobo ? goboIcon(goboRef(role, s)) : s.hex ? h("i.slot-dot", { style: { background: s.hex } }) : null, slotLabel(s.name)));
  }
  return Array.from({ length: 8 }, (_, i) => h("button.chip", {
    title: `DMX ${i * 16 + 8} - this fixture's file lists no slots, so these are guesses`,
    onclick: send(i * 16 + 8),
  }, i === 0 ? "Open" : String(i)));
}

// ------------------------------------------------------------ position
const sendPad = throttle((pan, tilt) => {
  const p = { unit: "255" };
  if (pan !== null) p.pan = pan;
  if (tilt !== null) p.tilt = tilt;
  run("set_position", p);
}, 70);

// What the selection can do (from the desk): which axes, their degrees,
// the part every selected light can reach.  The pad shows just that.
let padInfo = null, padKey = "";
async function loadPadInfo() {
  const k = JSON.stringify([sel(), state.snap && state.snap.patch_rev, state.snap && state.snap.floor_lock]);
  if (k === padKey) return;
  padKey = k;
  const r = hasSel() ? await run("pad_info", {}, { silentError: true }) : null;
  padInfo = r && r.ok && r.heads.length ? r : null;
  drawPadFrame();
  renderPad();
}

const degAt = (role, f) => {
  const d = padInfo && padInfo.deg[role];
  return d ? Math.round(d[0] + f * (d[1] - d[0])) : null;
};

function drawPadFrame() {
  const pad = $("#pad");
  if (!pad) return;
  const info = padInfo;
  pad.classList.toggle("no-pan", !!info && !info.pan);
  pad.classList.toggle("no-tilt", !!info && !info.tilt);
  for (const el of pad.querySelectorAll(".pad-out, .pad-ax")) el.remove();
  if (!info) return;
  const rp = info.reach.pan || [0, 1], rt = info.reach.tilt || [0, 1];
  // grey where these lights can't go (their limits, the dance floor lock)
  const out = (style) => h("div.pad-out", { style });
  pad.append(
    out({ left: "0", top: "0", bottom: "0", width: `${rp[0] * 100}%` }),
    out({ right: "0", top: "0", bottom: "0", width: `${(1 - rp[1]) * 100}%` }),
    out({ left: `${rp[0] * 100}%`, right: `${(1 - rp[1]) * 100}%`, top: "0", height: `${(1 - rt[1]) * 100}%` }),
    out({ left: `${rp[0] * 100}%`, right: `${(1 - rp[1]) * 100}%`, bottom: "0", height: `${rt[0] * 100}%` }));
  // the lights' own degrees at the edges
  const ax = (cls, text) => text === null ? null : h("span.pad-ax." + cls, text);
  const d = (role, f) => { const v = degAt(role, f); return v === null ? null : `${v}°`; };
  pad.append(...[info.pan ? ax("l", d("pan", 0)) : null, info.pan ? ax("r", d("pan", 1)) : null,
    info.tilt ? ax("b", d("tilt", 0)) : null, info.tilt ? ax("t", d("tilt", 1)) : null].filter(Boolean));
  pad.title = !info.pan ? "These lights only tilt: drag up and down"
    : !info.tilt ? "These lights only pan: drag left and right"
      : "Drag: pan left-right, tilt up-down" + (info.mixed ? " (the selected models travel differently: degrees are the first one's)" : "");
}

function renderPad() {
  const first = sel()[0];
  const look = first !== undefined ? state.looks[first] : null;
  const dot = $("#pad-dot");
  if (look && (typeof look.pan === "number" || typeof look.tilt === "number")) {
    const pan = look.pan ?? 0.5, tilt = look.tilt ?? 0.5;
    dot.style.left = pan * 100 + "%";
    dot.style.top = (1 - tilt) * 100 + "%";
    const parts = [];
    if (!padInfo || padInfo.pan) { const dg = degAt("pan", pan); parts.push(`pan ${dg !== null ? dg + "°" : Math.round(pan * 255)}`); }
    if (!padInfo || padInfo.tilt) { const dg = degAt("tilt", tilt); parts.push(`tilt ${dg !== null ? dg + "°" : Math.round(tilt * 255)}`); }
    $("#pad-read").textContent = parts.join(" · ");
  } else {
    $("#pad-read").textContent = hasSel() ? (padInfo ? "not driven - drag the pad to aim" : "the selected lights can't pan or tilt") : "select moving heads to aim them";
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

function wireColourMatch() {
  const b = $("#colour-match");
  if (b) b.addEventListener("click", () => import("./colourmatch.js").then((m) => m.openColourMatch()));
}

function wirePad() {
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
    const rp = (padInfo && padInfo.reach.pan) || [0, 1], rt = (padInfo && padInfo.reach.tilt) || [0, 1];
    // only where these lights can go, and only the axes they have
    const x = Math.max(rp[0], Math.min(rp[1], (e.clientX - r.left) / r.width));
    const t = Math.max(rt[0], Math.min(rt[1], 1 - (e.clientY - r.top) / r.height));
    const hasPan = !padInfo || padInfo.pan, hasTilt = !padInfo || padInfo.tilt;
    if (hasPan) $("#pad-dot").style.left = x * 100 + "%";
    if (hasTilt) $("#pad-dot").style.top = (1 - t) * 100 + "%";
    sendPad(hasPan ? Math.round(x * 255) : null, hasTilt ? Math.round(t * 255) : null);
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
// A mixed selection (movers and PARs...) shows one section per kind of
// light - that model's own controls, sent to those lights only - instead of
// every control any of them has in one list.
let attrKinds = null;          // [{label, heads, state}] when the selection mixes models
function kindsOf(heads) {
  const by = new Map();
  for (const x of heads) {
    const k = `${x.manufacturer}|${x.model}|${x.mode}`;
    if (!by.has(k)) by.set(k, { model: x.model || "Light", label: (x.body && x.body.label) || "", heads: [] });
    by.get(k).heads.push(x.head_no);
  }
  return [...by.values()];
}

async function loadAttributes() {
  clearTimeout(attrTimer);
  if (!hasSel()) { attrState = null; attrKinds = null; renderAttributes(); return; }
  attrTimer = setTimeout(async () => {
    try {
      attrState = await get("/api/console/attributes?heads=" + sel().join(","));
      const kinds = kindsOf(selectionHeads());
      attrKinds = kinds.length > 1
        ? await Promise.all(kinds.map(async (k) => ({ ...k, state: await get("/api/console/attributes?heads=" + k.heads.join(",")) })))
        : null;
      renderAttributes();
    } catch (e) { /* the grid just stays as it was */ }
  }, 60);
}

const sendAttrNow = (attribute, value, heads) => run("set_attribute", heads ? { attribute, value, heads } : { attribute, value }, { silentError: true });
const sendAttrs = new Map();
const sendAttr = (attribute, value, heads) => {
  const k = attribute + "|" + (heads || []).join(",");
  if (!sendAttrs.has(k)) sendAttrs.set(k, throttle((v, hs) => sendAttrNow(attribute, v, hs), 70));
  sendAttrs.get(k)(value, heads);
};

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
    // the light's own strobe range, the right way round (the desk works it out)
    const open = strobe.open ?? 0;
    const lo = Math.max(open + 8, 64);
    const st = strobe.steps || { off: open, slow: lo, medium: Math.round((lo + 255) / 2), fast: 250 };
    rows.push(h("div.chip-row", h("span.k", "Strobe"),
      ...[["off", "Off"], ["slow", "Slow"], ["medium", "Medium"], ["fast", "Fast"]].map(([k, label]) =>
        h("button.chip", { title: `DMX ${st[k]}`, onclick: set(strobe.role, st[k]) }, label))));
    // strobe buttons: the desk's strobe (always works) or, once Slow /
    // Medium / Fast above are seen strobing the real light, its own
    if ("own_strobe" in strobe) {
      const first = selectionHeads()[0];
      const own = (v) => () => first && run("own_strobe", { head: first.head_no, on: v }, { toast: true }).then(loadAttributes);
      const top = strobe.own_hz ? `up to ${strobe.own_hz} Hz` : "as fast as it goes";
      rows.push(h("div.chip-row", { title: "What a Strobe button (and a strobe-speed fader) does on this model - for every light of this model" },
        h("span.k", "Strobe buttons"),
        h("button.chip" + (strobe.own_strobe ? "" : ".on"), { title: "The desk switches the light on and off: works on any light, 20 flashes a second at most", onclick: own(false) }, "Desk strobe"),
        h("button.chip" + (strobe.own_strobe ? ".on" : ""), { title: `Its own strobe channel: even flashes, ${top} - only if Slow / Medium / Fast above really strobe the light`, onclick: own(true) }, `Its own (${top})`)));
    }
  }
  // The desk's own strobe: flashes the level, like the Strobe all button -
  // strobes any light, even one whose strobe channel its file gets wrong
  if (selectionHeads().length) {
    const on = (state.snap && state.snap.desk_strobe) || {};
    const now = selectionHeads().map((x) => on[String(x.head_no)] || 0);
    const hz = now.every((v) => v === now[0]) ? now[0] : -1;
    const desk = (v) => () => run("desk_strobe", { hz: v }).then(loadAttributes);
    rows.push(h("div.chip-row", { title: "Flashes the light's level from the desk at a set speed - use it when the light's own strobe doesn't strobe the real light" },
      h("span.k", strobe ? "Desk strobe" : "Strobe"),
      ...[[0, "Off"], [3, "Slow"], [8, "Medium"], [14, "Fast"]].map(([v, label]) =>
        h("button.chip" + (hz === v && (v || now.some(Boolean)) ? ".on" : ""), { title: v ? `${v} flashes a second` : "stop the desk strobe", onclick: desk(v) }, label))));
  }
  // A shutter whose "open" value nobody knows sits at 0 - closed on many
  // movers - so the light tilts but never lights.  Find it on the real
  // light, once, and Haze remembers it for every head of that model.
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
    const finder = (...kids) => (strobe.open_known
      ? h("details.open-find", h("summary.small.muted", "Light stays dark on Full? Find its open value"), ...kids)
      : h("div.open-find.unknown", ...kids));
    rows.push(finder(
      h("div.small", strobe.open_known
        ? `Opens at ${openV}. If the real light stays dark on Full, find its open value:`
        : "The desk doesn't know which value opens this light's shutter, so Full may leave it dark. Put the dimmer up, slide until the REAL light comes on, then press This is open:"),
      h("div.fx-row", h("span.k", strobe.role === "shutter" ? "Shutter" : "Strobe"), find, val),
      h("button.btn.small.primary", {
        onclick: () => first && run("remember_open", { head: first.head_no, value: +find.value }, { toast: true }).then(loadAttributes),
      }, "This is open")));
  }
  for (const role of ["gobo", "gobo2"]) {
    if (!by[role]) continue;
    rows.push(h("div.chip-row", h("span.k", role === "gobo" ? "Gobo" : "Gobo 2"), ...slotButtons(role, by[role])));
  }
  // its file has no gobo pictures: the 3D draws stand-ins - the maker's own
  // GDTF (GDTF Share) usually has them
  const sel = new Set(selected());
  const drawn = by.gobo && patch().filter((x) => sel.has(x.head_no) && (x.gobos || []).some((g) => g[2] === "-"));
  if (drawn && drawn.length) {
    const models = [...new Set(drawn.map((x) => x.model))];
    rows.push(h("div.small.muted.gobo-standin",
      `${models.join(", ")}: its fixture file has no gobo pictures, so the 3D draws stand-ins. `,
      h("button.btn.small", { onclick: () => import("./stagepanel.js").then((m) => m.realBodies()) }, "Get the maker's gobos")));
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
  const pageRows = (st, heads) => {
    const rows = [];
    // a light's own extra channels (continuous pan, built-in programs...)
    // first: at the bottom of a long list nobody found them
    const pages = [...(st.pages || [])].sort((x, y) => (y.page === "other") - (x.page === "other"));
    for (const page of pages) {
      if (page.page === "intensity") continue;
      const attrs = (page.attrs || []).filter((a) => !["red", "green", "blue", "pan", "tilt", "pan_fine", "tilt_fine"].includes(a.role));
      if (!attrs.length) continue;
      const main = attrs.filter((a) => !isAdvanced(a)), adv = attrs.filter(isAdvanced);
      if (main.length) {
        rows.push(h("div.attr-page", page.page === "other" ? "More channels" : page.page));
        if (page.page === "other" && main.some((a) => /program|auto|macro|show|sound/i.test(a.name || ""))) {
          rows.push(h("p.muted.small.attr-note", "A program goes to the real light, which runs it itself. The 3D can't know what it does: it shows a slow colour cycle and wander, and tags the light ▶ with the program's name."));
        }
        const keep = page.page === "other" ? zoneKeepRow(main, heads) : null;
        if (keep) rows.push(keep);
        for (const a of main) rows.push(attrRow(a, heads));
      }
      if (adv.length) rows.push(h("details.attr-adv", h("summary", `Advanced · ${adv.map((a) => (a.name || attrName(a.role)).toLowerCase()).join(", ")}`),
        ...adv.map((a) => attrRow(a, heads))));
    }
    return rows;
  };
  if (attrKinds && attrKinds.length > 1) {
    const secs = attrKinds.map((k) => {
      const rows = pageRows(k.state, k.heads);
      const names = [...new Set(rows.filter((r) => r.classList && r.classList.contains("attr-page")).map((r) => r.textContent.toLowerCase()))];
      return h("details.attr-kind", { open: true },
        h("summary", h("b", `${k.heads.length} × ${k.model}`), k.label ? h("span.muted.small", ` ${k.label}`) : null,
          h("span.muted.small", names.length ? ` · ${names.join(", ")}` : " · nothing on this tab")),
        ...(rows.length ? rows : [h("p.muted.small", "No beam controls on these.")]));
    });
    box.replaceChildren(h("p.muted.small", "Mixed selection: each kind of light has its own controls, sent to those lights only."), ...secs);
    return;
  }
  const rows = pageRows(attrState, null);
  box.replaceChildren(...(rows.length ? rows : [h("p.muted.small", "These fixtures have no beam attributes.")]));
}

// A light's own movement program (Built-In Auto Tilt...) moves the head
// by itself - the desk can't keep that on a zone.  "Keep it on" hands the
// movement to the desk: the light's own movement program goes to its "no
// function", and the desk moves the light inside the zone (roam for a light
// that pans and tilts, a bounce across the floor for one that only tilts).
// Its other programs (heads on / off, colour programs) keep running.
const OWN_MOVE_RE = /auto ?(tilt|pan)|continuous (pan|tilt)|(pan|tilt) (rotat|spin)|built.?in.*(tilt|pan|move)|(pan|tilt).*(auto|program|macro)|movement (program|macro)/i;
const OFF_SLOT_RE = /no function|\boff\b|\bstop\b|disabled?|^0$/i;
function zoneKeepRow(attrs, heads) {
  const zones = [...new Map((((state.snap && state.snap.venue) || {}).zones || []).map((z) => [z.kind, z.name || z.kind])).entries()];
  const moving = attrs.filter((a) => OWN_MOVE_RE.test(a.name || "") && !/speed|sens/i.test(a.name || ""));
  const nums = heads || selected();
  const lights = patch().filter((x) => nums.includes(x.head_no));
  const both = lights.some((x) => x.map.includes("pan") && x.map.includes("tilt"));
  const tilt = lights.some((x) => x.map.includes("tilt"));
  if (!zones.length || !moving.length || !(both || tilt)) return null;
  const go = async (kind, name) => {
    for (const a of moving) {
      const off = (a.slots || []).find((sl) => OFF_SLOT_RE.test(sl.name || ""));
      await sendAttrNow(a.role, off ? off.from : 0, heads);
    }
    if (both) await run("roam", { zones: [kind], heads: nums, speed: 1 }, { toast: true });
    else {
      const across = lights.some((x) => x.map.filter((r) => r === "tilt").length > 1);
      await run("run_fx", { name: "tilt_bounce", heads: nums, across, params: { zone: kind, size: 40, speed: 0.125 } }, { toast: true });
    }
    toast(`${name}: the desk moves ${nums.length === 1 ? "this light" : "these lights"} there now. Stop it on the Move tab.`);
    loadAttributes();
  };
  return h("div.attr-keep",
    h("div.mv-row", h("span.k", "Keep it on"), h("span.chip-row", ...zones.map(([k, name]) => h("button.chip", {
      title: `Turn the light's own ${moving.map((a) => (a.name || "").toLowerCase()).join(" / ")} off and move it inside the ${name} from the desk`,
      onclick: () => go(k, name) }, name)))),
    h("p.muted.small", `${moving.map((a) => a.name).join(", ")} moves the light by itself, so the desk can't keep it on a zone. `
      + "“Keep it on” hands the movement to the desk: it stays inside the zone you pick, and the light's other programs keep running."));
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

// a wheel of whites (warm, cold, CTO...) is white presets, not a colour wheel
function whitesOnly(slots) {
  return (slots || []).length > 1 && slots.every((s) => /white|open|\bk\b|\d{4}\s*k|cto|ctb|warm|cool|cold|daylight|tungsten/i.test(s.name || "")
    || (s.hex && (() => { const n = parseInt(s.hex.slice(1), 16); const r = n >> 16, g = (n >> 8) & 255, b = n & 255; return Math.min(r, g, b) > 150; })()));
}
const TEACHABLE = new Set(["wheel", "wheel2", "gobo", "gobo2", "prism"]);
const ADVANCED_RE = /lamp|reset|fan|maint|display|dimmer ?curve|dim(mer)? mode|control|service|auto ?test|pan\/tilt ?mode|blackout while/i;
const isAdvanced = (a) => a.role === "control" || ADVANCED_RE.test(a.name || "");

// a rotation channel (gobo / prism spin) as ↺ ■ ↻ and slow -> fast, when
// its ranges say which way; else null and it stays a plain slider
const slowFirstName = (text) => !/decreas|fast\s*(to|->|→|-)\s*slow/i.test(text || "");
function rotateRow(a, heads) {
  const slots = a.slots || [];
  const ccw = slots.find((x) => /counter|anti|ccw|left/i.test(x.name));
  const cw = slots.find((x) => x !== ccw && /clockwise|\bcw\b|right/i.test(x.name));
  if (!ccw || !cw) return null;
  const stop = slots.find((x) => /stop|no rot|off|index|^0$/i.test(x.name));
  const cur = a.value;
  const dir = cur == null ? 0 : cur >= ccw.from && cur <= ccw.to ? -1 : cur >= cw.from && cur <= cw.to ? 1 : 0;
  const speed = h("input", { type: "range", min: 0, max: 100, value: 40, title: "Speed" });
  const inRange = (slot, s01) => { const f = slowFirstName(slot.name) ? s01 : 1 - s01; return Math.round(slot.from + f * (slot.to - slot.from)); };
  let d = dir;
  const send = (to) => {
    d = to;
    const v = to === 0 ? (stop ? stop.value : 0) : inRange(to < 0 ? ccw : cw, +speed.value / 100);
    sendAttrNow(a.role, v, heads);
    for (const b of row.querySelectorAll(".chip")) b.classList.toggle("on", +b.dataset.d === to);
  };
  speed.addEventListener("input", () => { if (d) sendAttr(a.role, inRange(d < 0 ? ccw : cw, +speed.value / 100), heads); });
  const b = (label, to, title) => h("button.chip" + (dir === to ? ".on" : ""), { title, "data-d": to, onclick: () => send(to) }, label);
  const row = h("div.attr-rot", h("label", { title: a.role }, a.name || attrName(a.role)),
    h("span.chip-row", b("↺", -1, "Turn counter-clockwise"), b("■", 0, "Stop"), b("↻", 1, "Turn clockwise")),
    h("span.muted.small", "slow"), speed, h("span.muted.small", "fast"));
  return row;
}

function attrRow(a, heads = null) {
  if (/_rot$/.test(a.role) || (/^aux\d+$/.test(a.role) && /rotat/i.test(a.name || ""))) {
    const r = rotateRow(a, heads);
    if (r) return r;
  }
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
  const clear = () => run("set_attr_range", heads ? { attribute: a.role, clear: true, heads } : { attribute: a.role, clear: true }).then(loadAttributes);
  const row = h("div.attr" + (a.set ? ".set" : "") + (a.partial ? ".partial" : ""),
    h("label", { title: a.partial ? `${a.role}: only ${a.heads} of the selection have it` : a.role },
      a.role === "wheel" && whitesOnly(a.slots) ? "White presets" : a.name || attrName(a.role)),
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
    sendAttr(a.role, cur, heads);
  };
  const fromPointer = (e) => {
    const r = bar.getBoundingClientRect();
    apply(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * full);
  };
  let dragging = false;
  // a finger: the bar only takes over once it moves sideways (8 px) - a
  // finger scrolling the list up or down passes through and changes nothing
  // (these values go straight to the lights); a tap still sets the value
  let touch = null;
  const start = (e) => {
    dragging = true;
    attrHeld = true;
    bar.setPointerCapture(e.pointerId);
    row.classList.add("drag");
    fromPointer(e);
  };
  bar.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") { touch = { id: e.pointerId, x: e.clientX, y: e.clientY }; return; }
    e.preventDefault();                     // no text selection, and keep focus for the arrow keys
    bar.focus({ preventScroll: true });
    start(e);
  });
  bar.addEventListener("pointermove", (e) => {
    if (dragging) { fromPointer(e); return; }
    if (!touch || e.pointerId !== touch.id) return;
    const dx = Math.abs(e.clientX - touch.x), dy = Math.abs(e.clientY - touch.y);
    if (dy > 8 && dy > dx) { touch = null; return; }       // a scroll
    if (dx > 8) { touch = null; start(e); }
  });
  bar.addEventListener("pointerup", (e) => {
    if (touch && e.pointerId === touch.id && !dragging) { touch = null; fromPointer(e); setTimeout(loadAttributes, 250); }
  });
  bar.addEventListener("pointercancel", () => { touch = null; });
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
  if (TEACHABLE.has(a.role) && (!a.slots || !a.slots.length) && !a.mixed) {
    return h("div.attr-wrap", row, h("div.attr-slots", h("button.linkish.small", {
      title: "Its file lists no positions: step through it on the real light and name them",
      onclick: () => import("./teachwheel.js").then((m) => m.openTeachWheel(a.role, loadAttributes)),
    }, `Teach the ${a.role.startsWith("wheel") ? "colour wheel" : attrName(a.role).toLowerCase()}…`)));
  }
  if (!a.slots || !a.slots.length || a.mixed) return row;
  // a wheel / gobo: its named slots as one-tap chips under the bar
  const gobo = /^gobo2?$/.test(a.role);
  const chips = h("div.attr-slots", ...a.slots.map((s) => h("button.chip.slot" + (gobo ? ".gobo" : "") + (val !== null && val >= s.from && val <= s.to ? ".on" : ""), {
    title: `${s.name} - DMX ${s.from}-${s.to}`,
    onclick: () => { apply(s.value); setTimeout(loadAttributes, 150); },
  }, gobo ? goboIcon(goboRef(a.role, s)) : s.hex ? h("i.slot-dot", { style: { background: s.hex } }) : null, s.name)));
  return h("div.attr-wrap", row, chips);
}

// ------------------------------------------------------- multi-head lights
// A Wave 360 has four heads (four tilts, four colour cells) on one address.
// Pick heads here and colour / attribute changes go to just those; "All"
// is the whole light again.
function headCount(hd) {
  const n = {};
  for (const r of hd.map || []) if (!/_fine$|^unused$|^raw$/.test(r)) n[r] = (n[r] || 0) + 1;
  return Math.max(0, ...Object.values(n).filter((c) => c > 1));
}
let cellsKey = "";
let cellsSel = "";

function renderProgCells() {
  const box = $("#prog-cells");
  const heads = selectionHeads();
  const sig = heads.map((x) => x.head_no).join(",");
  if (sig !== cellsSel) {                                            // a new selection starts on All
    cellsSel = sig;
    // ...unless the heads were picked in the 3D (Select Elements)
    const want = state.pendingCells;
    state.cells = want && sig === String(want.head) ? [...want.cells] : [];
    state.pendingCells = null;
  }
  const n = Math.max(0, ...heads.map(headCount));
  // each head's colour now (the first light's), so you can see what you set
  const look = heads.length ? (state.looks || {})[heads[0].head_no] : null;
  const dots = Array.from({ length: n }, (_, i) => (look && look.cells && look.cells[i] && look.cells[i].hex) || "");
  const key = JSON.stringify([n, state.cells || [], dots, tab === "position"]);
  if (key === cellsKey) return;
  cellsKey = key;
  // the head chips sit in the programmer's title row (they used to be a
  // row of their own that pushed everything down when a light with heads
  // was picked); only the per-head tilt bar, on Position, is a row
  const row = $("#prog-heads");
  row.hidden = n < 2;
  box.hidden = true;
  if (n < 2) { state.cells = []; row.replaceChildren(); return; }
  const cur = new Set(state.cells || []);
  const set = (list) => {
    state.cells = list.length === n ? [] : [...new Set(list)].sort((a, b) => a - b);
    renderProgCells();
  };
  // a tap picks that head on its own (head 1, a colour, head 2, another
  // colour - each keeps its own); Ctrl / Shift + tap adds it to the others
  const pick = (k, ev) => {
    if (k === 0) return set([]);
    if (ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) {
      if (cur.has(k)) cur.delete(k); else cur.add(k);
      return set([...cur]);
    }
    return set([k]);
  };
  const same = (list) => list.length === cur.size && list.every((k) => cur.has(k));
  const odd = Array.from({ length: n }, (_, i) => i + 1).filter((k) => k % 2);
  const even = Array.from({ length: n }, (_, i) => i + 1).filter((k) => !(k % 2));
  const more = (e) => menu(e.currentTarget, [
    { label: "Copy head 1's colour to all", disabled: !dots[0], run: () => run("set_colour", { hex: dots[0] }) },
    { label: "Spread a gradient", hint: `from head 1's colour to head ${n}'s`, disabled: !(dots[0] && dots[n - 1] && dots[0] !== dots[n - 1]),
      run: async () => { for (let k = 1; k <= n; k++) await run("set_colour", { hex: mixHex(dots[0], dots[n - 1], (k - 1) / (n - 1)), cell: [k] }, { silentError: true }); } },
  ]);
  row.title = cur.size ? `Changes go to head${cur.size > 1 ? "s" : ""} ${[...cur].sort((a, b) => a - b).join(", ")} only` : "Tap a head to colour it on its own (Ctrl/Shift + tap adds heads)";
  row.replaceChildren(...[h("span.prog-heads-k", "Heads"),
    h("button.chip" + (!cur.size ? ".on" : ""), { title: "Every head of the light", onclick: () => pick(0) }, "All"),
    ...Array.from({ length: n }, (_, i) => h("button.chip.cell-chip" + (cur.has(i + 1) ? ".on" : ""), {
      title: `Head ${i + 1} on its own - Ctrl or Shift + tap to add it to the heads already picked`,
      onclick: (ev) => pick(i + 1, ev) },
      dots[i] ? h("i.slot-dot", { style: { background: dots[i] } }) : null, String(i + 1))),
    n >= 4 ? h("button.chip" + (same(odd) ? ".on" : ""), { title: "Heads 1, 3…", onclick: () => set(odd) }, "Odd") : null,
    n >= 4 ? h("button.chip" + (same(even) ? ".on" : ""), { title: "Heads 2, 4…", onclick: () => set(even) }, "Even") : null,
    dots[0] ? h("button.chip.icon", { title: "Copy a head's colour to all, spread a gradient", "aria-label": "More for the heads", onclick: more }, "⋯") : null,
    // tilt per head: a chip in this row, its sliders floating under it -
    // never a row of its own pushing the programmer down
    tab === "position" ? headBar(heads, n) : null,
  ].filter(Boolean));
  box.hidden = true;
}

const mixHex = (a, b, f) => "#" + [1, 3, 5].map((i) => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - f) + parseInt(b.slice(i, i + 2), 16) * f)
  .toString(16).padStart(2, "0")).join("");

// A light with a tilt per head (a Wave 360): its heads as a bar - tilt each
// head in degrees, or a wave through them - instead of raw channels.
function headBar(heads, n) {
  const hd = heads.find((x) => (x.map || []).filter((r) => r === "tilt").length > 1);
  if (!hd) return null;
  const tilts = (hd.map || []).filter((r) => r === "tilt").length;
  const span = (hd.span && hd.span[1]) || 270;
  const vals = (((state.snap && state.snap.programmer) || {}).values || {})[hd.head_no] || {};
  const deg = (v) => `${Math.round((v / 255 - 0.5) * span)}°`;
  const send = throttle((k, v) => run("set_attribute", { attribute: "tilt", value: v, cell: [k] }, { silentError: true }), 60);
  // folded by default: it is a lot of sliders above every tab (remembered)
  let open = false;
  try { open = localStorage.getItem("haze.headBar") === "1"; } catch { /* private window */ }
  return h("details.head-bar", { title: "Tilt each head (degrees from straight out)", open,
    ontoggle: (e) => { try { localStorage.setItem("haze.headBar", e.currentTarget.open ? "1" : ""); } catch { /* private window */ } } },
    h("summary.chip", `Tilt ▾`),
    // (it opens over the programmer, never pushing it down)
    h("div.head-bar-pop", ...Array.from({ length: tilts }, (_, i) => {
      const v = vals[`tilt@${i + 1}`] ?? 128;
      const out = h("small", deg(v));
      const input = h("input", { type: "range", min: 0, max: 255, value: v, "aria-label": `Head ${i + 1} tilt`,
        oninput: (e) => { out.textContent = deg(+e.target.value); send(i + 1, +e.target.value); } });
      return h("label.head-tilt", h("b", String(i + 1)), input, out);
    }),
    h("button.chip", { title: "A tilt wave through the heads (Move tab for the angles and speed)",
      onclick: () => run("run_fx", { name: "range", heads: heads.map((x) => x.head_no), across: true,
        params: { tilt_a: -40, tilt_b: 40, pan_a: 0, pan_b: 0, speed: 0.25, spread: 360 } }, { toast: true }) }, "Wave")));
}

// ------------------------------------------------------ in the programmer
// What the programmer holds, by kind, each with its own x: clear just the
// colour (or the position...) without losing the rest.
const IN_GROUPS = [["intensity", "Dimmer"], ["colour", "Colour"], ["position", "Position"], ["beam", "Beam"], ["other", "Other"]];
const GROUP_OF = (() => {
  const m = {};
  for (const r of ["dimmer", "zone_dimmer", "shutter", "strobe"]) m[r] = "intensity";
  for (const r of ["red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow", "wheel", "macro"]) m[r] = "colour";
  for (const r of ["pan", "tilt", "speed"]) m[r] = "position";
  for (const r of ["gobo", "gobo_rot", "prism", "zoom", "focus", "frost", "iris"]) m[r] = "beam";
  return m;
})();
const MOVE_FX = new Set(["circle", "figure_eight", "pan_sweep", "tilt_bounce", "fan_pan", "range"]);
let progInKey = "";
let fxGridKey = "";
let runningKey = "";
let looksKey = "";

function renderProgIn() {
  const box = $("#prog-in");
  const vals = ((state.snap && state.snap.programmer) || {}).values || {};
  const counts = {};
  const others = new Set();
  for (const row of Object.values(vals)) {
    const seen = new Set();
    for (const r of Object.keys(row)) {
      const base = r.replace(/@\d+$/, "").replace(/_fine$/, "");
      const g = GROUP_OF[base] || "other";
      if (g === "other") others.add(base);
      seen.add(g);
    }
    for (const g of seen) counts[g] = (counts[g] || 0) + 1;
  }
  // "Other" says what it is: the laser, special effects, or the light's own
  // channels (programs, speeds, spins)
  const otherName = [...others].every((r) => r.startsWith("laser")) ? "Laser"
    : [...others].every((r) => /^(fire|flame|co2|confetti|spark|fog|haze|fan|sfx)/.test(r)) ? "Effects"
      : "Own channels";
  const moving = ((state.snap && state.snap.fx) || []).some((f) => MOVE_FX.has(f.lib) && (f.from || "programmer") === "programmer");
  if (moving && !counts.position) counts.position = 0;
  // the effects you started (not a cue's, a button's or the timeline's)
  const mineFx = ((state.snap && state.snap.fx) || []).filter((f) => (f.from || "programmer") === "programmer");
  const key = JSON.stringify([counts, otherName, mineFx.length]);
  if (key === progInKey) return;          // redraw only on change: keeps the x clickable
  progInKey = key;
  const groups = IN_GROUPS.filter(([g]) => g in counts).map(([g, l]) => [g, g === "other" ? otherName : l]);
  box.hidden = !groups.length && !mineFx.length;
  box.replaceChildren(...(groups.length || mineFx.length ? [
    // what is in the programmer, by family (each in its colour): × clears it
    // (in the title row: they used to be a row of their own that pushed the
    // panel down the moment anything was set)
    ...groups.map(([g, label]) => h("span.chip.prog-in-chip", { dataset: { g },
      title: counts[g] ? `The programmer holds ${label.toLowerCase()} for ${counts[g]} light${counts[g] === 1 ? "" : "s"} - it goes into the next cue or palette you record. × clears just that.` : `${label}: a movement is running` },
      label, counts[g] ? h("small", ` · ${counts[g]} light${counts[g] === 1 ? "" : "s"}`) : null,
      h("button.x", { title: `Clear ${label.toLowerCase()} only`, onclick: () => run("clear_attrs", { group: g }) }, "×"))),
    mineFx.length ? h("span.chip.prog-in-chip.prog-fx", { dataset: { g: "fx" }, title: mineFx.map((f) => f.label).join(", ") },
      "Effects", h("small", ` ${mineFx.length}`),
      h("button.x", { title: "Stop the effects you started - the lights keep their colour, level and position (cues' and buttons' effects keep playing)",
        onclick: () => run("stop_fx", { programmer: true }, { toast: true }) }, "×")) : null] : []));
  // the actions at the foot of the programmer (Make a button… sits next to
  // Record cue… on every tab): live only when there is something to keep
  const any = groups.length > 0 || mineFx.length > 0;
  $("#prog-clear").disabled = !any;
  $("#prog-record").disabled = !any;
  $("#prog-make").disabled = !any || !hasSel();
  // Update: the cue that is on in the focused playback, by name
  const upd = $("#prog-update");
  const on = cueOn();
  upd.disabled = !any || !on;
  const label = on ? `Update PB${on.pb} · ${on.name}` : "Update";
  const lab = upd.querySelector("span");
  if (lab && lab.textContent !== label) lab.textContent = label;
  upd.title = on ? `Merge the programmer into ${on.name} on PB${on.pb} - what you didn't touch stays (O)` : "Update: press GO on a playback first - it merges into the cue that is on (O)";
  // a greyed-out button says why (a disabled button shows no tooltip everywhere)
  const why = !any ? "Nothing to record yet: select lights and set a level, colour or position (or start an effect)."
    : !hasSel() ? "Select the lights to make a button of them." : "";
  // (the why is the greyed button's own tooltip: a line of text under the
  // buttons that came and went moved the whole panel)
  $("#prog-record").title = any ? "Record what is in the programmer as a cue (R)" : why;
  for (const b of [$("#prog-make"), $("#prog-clear")]) {
    if (b.dataset.title === undefined) b.dataset.title = b.title;
    b.title = (b.id === "prog-clear" ? !any : why) ? why || b.dataset.title : b.dataset.title;
  }
  const w = $("#prog-why");
  if (w) w.textContent = "";
}

// the cue that is on in the focused playback (what Update merges into)
function cueOn() {
  const n = focusedPlayback();
  const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === n);
  const c = pb && pb.index >= 0 ? (pb.stack || [])[pb.index] : null;
  return c ? { pb: n, cue: c.n, name: c.name || `Cue ${c.n}` } : null;
}

// a button holding some of the selected lights: say so, with a Turn off
let heldKey = "";
function renderHeld() {
  const box = $("#prog-held");
  if (!box) return;
  const held = (state.lite && state.lite.held) || (state.snap && state.snap.held) || [];
  const mine = new Set(sel());
  const hit = held.filter((b) => b.heads.some((n) => mine.has(n)));
  const key = JSON.stringify(hit);
  if (key === heldKey) return;
  heldKey = key;
  box.hidden = !hit.length;
  box.replaceChildren(...hit.map((b) => h("div.prog-held-row",
    h("span", "🔒 ", h("b", `“${b.label}”`), ` holds ${b.heads.filter((n) => mine.has(n)).length === 1 ? "this light" : "these lights"} - turn it off to change them.`),
    h("button.btn.small", { onclick: () => run("quick_press", { id: b.id, down: true }, { toast: true }) }, "Turn it off"))));
}
on("lite", renderHeld);
on("snapshot", renderHeld);
on("selection", renderHeld);

// a button opened in the programmer (Buttons -> Edit -> Open in the
// programmer): change it on stage, then Update button
let editKey = "";
function renderEditing() {
  const box = $("#prog-editing");
  if (!box) return;
  const q = (state.snap && state.snap.quick) || {};
  const b = q.editing ? (q.buttons || []).find((x) => x.id === q.editing) : null;
  const key = b ? b.id + b.label : "";
  if (key === editKey) return;
  editKey = key;
  box.hidden = !b;
  box.replaceChildren(...(b ? [h("div.prog-held-row",
    h("span", "✎ Editing the button ", h("b", `“${b.label}”`), " - change its lights here, then update it."),
    h("button.btn.small.primary", { onclick: () => run("quick_update", { id: b.id }, { toast: true }) }, "Update button"),
    h("button.btn.small", { onclick: () => run("quick_update", { cancel: true }) }, "Cancel"))] : []));
}
on("snapshot", renderEditing);
on("openprogrammer", () => import("./workspaces.js").then((m) => m.useMode("program")).catch(() => {}));

// What goes in a button made from the programmer: each family the
// programmer holds for these lights, on by default; a colour-only button
// stacks over a movement button and changes only the colour.
const KEEP = [["intensity", "Brightness"], ["colour", "Colour"], ["position", "Position / movement"], ["beam", "Beam (gobo, zoom…)"]];
function familiesFor(heads) {
  const vals = ((state.snap && state.snap.programmer) || {}).values || {};
  const have = new Set();
  for (const n of heads) {
    for (const r of Object.keys(vals[n] || {})) {
      const g = GROUP_OF[r.replace(/@\d+$/, "").replace(/_fine$/, "")];
      if (g && g !== "other") have.add(g);
    }
  }
  const set = new Set(heads);
  for (const f of (state.snap && state.snap.fx) || []) {
    if ((f.from || "programmer") !== "programmer" || !(f.heads || []).some((n) => set.has(n))) continue;
    have.add(MOVE_FX.has(f.lib) || f.lib === "roam" || f.lib === "shape" ? "position"
      : /colour|rainbow|alternate/.test(f.lib || "") ? "colour" : /dimmer|pulse|breathe|sparks|strobe/.test(f.lib || "") ? "intensity" : "beam");
  }
  return have;
}

// the buttons page open now (Make a button offers it first), and the pages
// there are: with buttons, a name or a grid of their own (1-4 always)
let buttonsPage = 1;
on("qbpage", (p) => { buttonsPage = +p || 1; });
function buttonPages() {
  const q = (state.snap && state.snap.quick) || {};
  const names = q.names || {};
  const there = new Set([...(q.buttons || []).map((b) => b.page),
    ...Object.entries(names).filter(([, v]) => v).map(([k]) => +k), ...Object.keys(q.layout || {}).map(Number)]);
  const last = Math.max(4, ...there);
  const out = [];
  for (let p = 1; p <= last; p++) if (p <= 4 || there.has(p)) out.push([p, names[p] || `Page ${p}`]);
  return out;
}

// what the selected lights do now, as a button that holds them
export async function makeHoldButton() {
  const heads = sel();
  if (!heads.length) { toast("Select the lights first"); return; }
  const have = familiesFor(heads);
  const keep = new Set(have.size ? have : KEEP.map(([k]) => k));
  const lights = `${heads.length} light${heads.length === 1 ? "" : "s"}`;
  const name = h("input", { type: "text", maxlength: 24, placeholder: "e.g. Slow circle red" });
  let toPage = buttonPages().some(([p]) => p === buttonsPage) ? buttonsPage : 1;
  const where = h("div.chip-row");
  const drawWhere = () => where.replaceChildren(...buttonPages().map(([p, l]) => h("button.chip" + (p === toPage ? ".on" : ""),
    { type: "button", onclick: () => { toPage = p; drawWhere(); } }, l)));
  drawWhere();
  const what = h("div.chip-row");
  const sum = h("p.small");
  const draw = () => {
    what.replaceChildren(...KEEP.map(([k, l]) => h("button.chip" + (keep.has(k) ? ".on" : ""), { type: "button",
      disabled: !have.has(k) && have.size > 0, title: have.has(k) ? "" : "The programmer has none of this for these lights",
      "aria-pressed": String(keep.has(k)), onclick: () => { if (keep.has(k)) keep.delete(k); else keep.add(k); draw(); } }, l)));
    const words = KEEP.filter(([k]) => keep.has(k)).map(([, l]) => l.split(" ")[0]);
    sum.textContent = keep.size ? `The button: ${words.join(" + ")} · ${lights}.` : "Pick at least one.";
    if (!name.value.trim() && !name.dataset.typed) name.placeholder = words.length === 1 ? `e.g. ${words[0]} for ${lights}` : "e.g. Slow circle red";
  };
  name.addEventListener("input", () => { name.dataset.typed = "1"; });
  draw();
  const body = h("div.qe",
    h("p.small", `What ${heads.length === 1 ? "this light shows" : `these ${heads.length} lights show`} now, as a button. It turns on and holds what it keeps - the programmer can't change that until you turn it off.`),
    h("label.small", "Name ", name),
    h("div.qe-label", "What goes in"), what, sum,
    h("div.qe-label", "On the page"), where,
    h("p.muted.small", "Buttons stack: a Colour-only button pressed over a movement button changes only the colour, and the movement keeps running. Nothing needs to be at full or moving to make a colour button."));
  const ok = await new Promise((resolve) => {
    const close = modal({ title: "Make a button", body, onClose: () => resolve(false), foot: [h("span.grow"),
      h("button.btn", { onclick: () => { resolve(false); close(); } }, "Cancel"),
      // answer first: closing the window counts as Cancel (onClose), and
      // whichever answer comes first is the one that counts
      h("button.btn.primary", { onclick: () => { if (!keep.size) return; resolve(true); close(); } }, "Make it")] });
  });
  if (!ok) return;
  const groups = (state.snap && state.snap.groups) || [];
  const set = new Set(heads);
  const g = groups.find((x) => x.heads.length === set.size && x.heads.every((n) => set.has(n)));
  const all = keep.size === KEEP.length;
  const r = await run("quick_from_programmer", { label: (name.value || "My look").trim().slice(0, 24),
    ...(all ? {} : { keep: [...keep] }), ...(g ? { group: g.n } : { heads }), page: toPage });
  if (r.ok) toast(r.summary, "ok", 6000);
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
    const key = JSON.stringify([list.map((fx) => fx.name), fxChosen, fxAcross, selectionHeads().map((x) => x.head_no)]);
    if (key !== fxGridKey) {               // redraw only on change: keeps the cards clickable
      fxGridKey = key;
      grid.replaceChildren(...list.map((fx) => h("button.fx-card" + (fxChosen === fx.name ? ".on" : ""), {
        title: "Run " + fx.label,
        onclick: () => startFx(fx),
      }, h("b", fx.label), h("small", fx.group))),
      selectionHeads().some((x) => headCount(x) > 1) ? h("button.chip.fx-across" + (fxAcross ? ".on" : ""), {
        title: "Run the effect across each light's own heads (a Wave 360's four cells) as if each were a light",
        onclick: () => { fxAcross = !fxAcross; fxGridKey = ""; renderFx(); } }, "Across each light's heads") : null,
      h("p.muted.small.fx-move-note", "Movements (circle, sweep...) are on the ", h("button.linkish", { onclick: () => showTab("position") }, "Position tab"), "."));
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

let fxAcross = false;

// Your own colours for a colour effect (and Breathe): the colours it goes
// through, in order - none chosen, every colour the light can make.  Kept
// per effect between runs.
const COLOUR_FX = new Set(["rainbow", "colour_chase", "alternate", "fan", "breathe"]);
const FX_SWATCHES = [["#ff0000", "Red"], ["#ff6a00", "Orange"], ["#ffd000", "Yellow"], ["#00ff00", "Green"], ["#00ffff", "Cyan"],
  ["#0040ff", "Blue"], ["#7a00ff", "Purple"], ["#ff00ff", "Magenta"], ["#ff4fa0", "Pink"], ["#ffffff", "White"]];
const FX_COLS_KEY = "haze.fxColours";
function fxColours(name) {
  try { return (JSON.parse(localStorage.getItem(FX_COLS_KEY) || "{}")[name] || []).slice(0, 8); } catch { return []; }
}
function setFxColours(name, cols) {
  try {
    const all = JSON.parse(localStorage.getItem(FX_COLS_KEY) || "{}");
    all[name] = cols;
    localStorage.setItem(FX_COLS_KEY, JSON.stringify(all));
  } catch { /* private window */ }
}
function colourPicker(fx, params, mine) {
  const box = h("div.fx-cols");
  const apply = (cols) => {
    setFxColours(fx.name, cols);
    params.colours = cols;
    for (const f of mine()) run("fx_tweak", { id: f.id, params: { colours: cols } }, { silentError: true });
    draw();
  };
  const draw = () => {
    const cols = fxColours(fx.name);
    const custom = h("input", { type: "color", value: "#ff00aa", "aria-label": "Any colour",
      onchange: (e) => { if (cols.length < 8) apply([...cols, e.target.value]); } });
    box.replaceChildren(
      h("div.fx-cols-head", h("b", "Colours"),
        h("span.muted.small", cols.length ? (fx.name === "breathe" ? "a new one each breath, in this order"
          : fx.name === "fan" ? "from the middle out, in this order" : "in this order")
          : fx.name === "breathe" ? "none: it breathes in the colour the lights have"
          : "none chosen: every colour the light can make"),
        cols.length ? h("button.btn.small.ghost", { title: "Back to every colour the light can make", onclick: () => apply([]) }, "Clear") : null),
      h("div.fx-cols-row",
        ...cols.map((c, i) => h("button.fx-col.on", { style: `--c:${c}`, title: "Take this colour out", "aria-label": `Take colour ${i + 1} out`,
          onclick: () => apply(cols.filter((_, j) => j !== i)) }, h("span", String(i + 1)))),
        cols.length < 8 ? h("span.fx-cols-add", h("span.muted.small", cols.length ? "add" : "pick"),
          ...FX_SWATCHES.map(([c, n]) => h("button.fx-col", { style: `--c:${c}`, title: `Add ${n}`, "aria-label": `Add ${n}`, onclick: () => apply([...cols, c]) })),
          h("label.fx-col.fx-col-any", { title: "Add any colour" }, "+", custom)) : h("span.muted.small", "8 at most")));
  };
  draw();
  return box;
}

function startFx(fx) {
  fxChosen = fx.name;
  const params = {};
  for (const p of fx.params || []) params[p.key] = p.default;
  if (COLOUR_FX.has(fx.name) && fxColours(fx.name).length) params.colours = fxColours(fx.name);
  run("run_fx", { name: fx.name, params, heads: sel(), across: fxAcross }, { toast: true }).then(() => renderFx());
  // its other knobs (speed is on the running row below): tap or type, and
  // the running effect changes as it runs - no restart, no jump
  const mine = () => ((state.snap && state.snap.fx) || []).filter((f) => f.lib === fx.name && (f.from || "programmer") === "programmer");
  $("#fx-params").replaceChildren(COLOUR_FX.has(fx.name) ? colourPicker(fx, params, mine) : "", ...(fx.params || []).filter((p) => !["phase", "speed"].includes(p.key)).map((p) => knob({
    label: p.label.replace(/^./, (c) => c.toUpperCase()), value: p.default, min: p.min, max: p.max,
    step: { add: Math.max(0.01, +((p.max - p.min) / 20).toPrecision(2)) }, halves: false,
    onSet: (v) => {
      params[p.key] = v;
      for (const f of mine()) run("fx_tweak", { id: f.id, params: { [p.key]: v } }, { silentError: true });
    },
  })));
}

// A running effect's speed (and a movement's size), as taps and a number:
// changed while it runs, carrying on from where it is
const isMove = (f) => f.lib in MOVE_SET || f.lib === "shape";
const MOVE_SET = Object.fromEntries([...MOVE_FX].map((k) => [k, 1]));
const fxSpeed = (f) => +((f.params && f.params.speed) ?? f.speed ?? 1);
export function fxKnobs(f) {
  if (f.steps || f.lib === "roam" || fxBeats(f)) {
    return fxBeats(f) ? h("p.muted.small.fx-locked", "Locked to the beat - set it back to free to change its speed here") : null;
  }
  const tweak = (args) => run("fx_tweak", { id: f.id, ...args }, { silentError: true });
  if (isMove(f)) {
    return h("div.fx-knobs",
      knob({ label: "Speed", unit: "s / turn", value: +(1 / Math.max(0.005, fxSpeed(f))).toFixed(1), min: 0.5, max: 200,
        presets: TURN_PRESETS, invert: true, minusTitle: "Faster (fewer seconds)", plusTitle: "Slower (more seconds)",
        onSet: (v) => tweak({ speed: +(1 / v).toFixed(4) }) }),
      knob({ label: "Size", unit: "°", value: +((f.params && f.params.size) || 20), min: 1, max: 270, presets: SIZE_PRESETS,
        halves: false, onSet: (v) => tweak({ size: v }) }));
  }
  return h("div.fx-knobs", knob({ label: "Speed", unit: "×", value: fxSpeed(f), min: 0.01, max: 20, presets: SPEED_PRESETS,
    minusTitle: "Slower", plusTitle: "Faster", onSet: (v) => tweak({ speed: v }) }));
}

// Lock a running effect to the beat clock: one cycle per 1, 2, 4 ... beats,
// in phase with the bar - or free, on its own speed.
const fxBeats = (f) => (f.params && f.params.beats) || f.beats || 0;
export function beatSelect(f) {
  const sel = h("select.select.fx-beats", { title: "Lock it to the beat: one cycle every … (the tempo in the top bar)" },
    h("option", { value: 0 }, "free"),
    ...[[0.25, "¼ beat"], [0.5, "½ beat"], [1, "1 beat"], [2, "2 beats"], [4, "1 bar"], [8, "2 bars"], [16, "4 bars"], [32, "8 bars"]]
      .map(([v, l]) => h("option", { value: v }, "♩ " + l)));
  sel.value = String(fxBeats(f));
  sel.addEventListener("change", () => run("fx_beats", { id: f.id, beats: +sel.value }, { toast: true }));
  return sel;
}

// Run it through the room by where the lights are, not by their numbers
const SPACES = [["", "in light order"], ["left-right", "→ left to right"], ["right-left", "← right to left"],
  ["stage-out", "↓ from the stage out"], ["back-in", "↑ back to the stage"], ["up", "bottom to top"], ["down", "top to bottom"],
  ["centre-out", "◎ centre out"], ["outside-in", "outside in"], ["around", "↻ round the room"]];
const fxSpace = (f) => (f.params && f.params.space) || "";
export function spaceSelect(f) {
  const sel = h("select.select.fx-beats", { title: "Which way it runs through the room (by where the lights are)" },
    ...SPACES.map(([v, l]) => h("option", { value: v }, l)));
  sel.value = fxSpace(f);
  sel.addEventListener("change", () => run("fx_space", { id: f.id, space: sel.value || null }, { toast: true }));
  return sel;
}

function renderRunning(force = false) {
  const list = (state.snap && state.snap.fx) || [];
  const box = $("#fx-running");
  // redraw only when the list changes: rebuilt on every live update, Stop
  // flickered and a click could land on a button already replaced
  const key = JSON.stringify(list.map((f) => [f.id, f.label, f.kind, f.role, f.from, (f.heads || []).length, fxBeats(f), fxSpace(f),
    fxSpeed(f).toPrecision(3), f.params && f.params.size]));
  if (!force && key === runningKey) return;
  runningKey = key;
  if (!list.length) { box.replaceChildren(h("p.muted.small", "No effects running.")); return; }
  box.replaceChildren(...list.map((f) => h("div.fx-run",
    h("b", f.label || `${f.kind || "wave"} ${f.role || ""}`),
    h("small", `${(f.heads || []).length} heads` + (f.from && f.from !== "programmer" ? ` · from a ${f.from}` : "")),
    // a button's, cue's or the timeline's effect: Stop only - its knobs are
    // changed in it (turned here, they changed a playing button by accident)
    (f.from || "programmer") === "programmer" && f.lib ? spaceSelect(f) : null,
    (f.from || "programmer") === "programmer" ? beatSelect(f) : null,
    h("button.btn.small", { onclick: () => run("stop_fx", { id: f.id }) }, "Stop"),
    (f.from || "programmer") === "programmer" ? fxKnobs(f)
      : h("p.muted.small", `Its speed, size and timing are the ${f.from}'s - change them there.`))),
  list.some((f) => (f.from || "programmer") === "programmer")
    ? h("button.btn.small.ghost", { title: "Stop the effects you started; cues' and buttons' effects keep playing", onclick: () => run("stop_fx", { programmer: true }) }, "Stop mine") : null,
  h("button.btn.small.ghost", { title: "Stop every effect, cues' and buttons' too", onclick: () => run("stop_fx", {}) }, "Stop all"));
}

// ----------------------------------------------------------------- looks
function renderLooks(force = false) {
  const pals = (state.snap && state.snap.palettes) || {};
  // (the selection and its values too: a tile shows whether the selection holds it)
  const key = JSON.stringify([pals, (state.snap && state.snap.presets) || [], ($("#look-search") || {}).value,
    selected(), ((state.snap && state.snap.programmer) || {}).values || {}]);
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
  // each family's own palettes, as tiles at the top of its tab, with a
  // tile to save the current one
  for (const kind of kinds) {
    const strip = $(`.palette-strip[data-kind=${kind}]`);
    if (!strip) continue;
    // only the palettes the selected lights can take (no gobo wheel: no
    // gobo palettes); nothing selected: all of them
    strip.replaceChildren(...(pals[kind] || []).filter((p) => paletteFits(p) !== false).map((p) => palButton(kind, p)),
      h("button.pal-item.pal-save", {
        title: `Save the selection's ${kind} as a palette`,
        onclick: async () => {
          if (!hasSel()) { toast("Select fixtures and set a look first"); return; }
          const name = await promptBox(`Save ${kind} palette`, "Name", "", { ok: "Save" });
          if (name) run("record_palette", { kind, name }, { toast: true });
        },
      }, "+ Save"));
  }
  const presets = (state.snap && state.snap.presets) || [];
  // a search box once there are enough looks to need one
  const search = $("#look-search");
  search.hidden = presets.length < 7;
  const q = search.hidden ? "" : search.value.trim().toLowerCase();
  const shown = q ? presets.filter((p) => [p.name, ...(p.tags || []), ...(p.type_labels || p.types || [])].join(" ").toLowerCase().includes(q)) : presets;
  $("#preset-list").replaceChildren(...(shown.length ? shown.map(lookTile)
    : [h("p.muted.small", presets.length ? `No look matches “${q}”.`
      : "No looks yet. Set colours, positions or a movement on some lights, then + Save look.")]));
}

// A look's tile: its colours, name and what it holds; tap plays it.
function lookTile(p) {
  const hexes = p.hexes && p.hexes.length ? p.hexes : [];
  const more = h("button.look-more", { "aria-label": `${p.name} options`, title: "Update, rename, make a button, delete",
    onclick: (e) => {
      e.stopPropagation();
      menu(e.currentTarget, [
        { label: "Update to what is on the lights now", run: () => run("record_preset", { name: p.name, preset: p.n }, { toast: true }) },
        { label: "Rename…", run: async () => {
          const name = await promptBox("Rename look", "Name", p.name, { ok: "Rename" });
          if (name) run("rename_preset", { preset: p.n, name });
        } },
        { label: "Make a button for it", run: () => makeLookButton(p) },
        (p.types || []).length ? { label: `Play on every ${(p.type_labels || p.types).join(" / ")}`, hint: "any light of these kinds",
          run: () => run("include_preset", { preset: p.n, on: "types" }, { toast: true }) } : null,
        "-",
        { label: "Delete", danger: true, run: async () => {
          if (await confirmBox("Delete look", `Delete “${p.name}”?`, { ok: "Delete", danger: true })) run("delete_preset", { preset: p.n });
        } },
      ]);
    } }, "⋯");
  return h("div.look",
    h("button.look-play", { title: `Play ${p.name}${hasSel() ? " on the selected lights" : ` on its ${p.heads} light(s)`}`,
      onclick: () => run("include_preset", { preset: p.n }, { toast: true }) },
    h("div.look-sw", ...(hexes.length ? hexes.map((c) => h("i", { style: { background: c } })) : [h("i.none")])),
    h("b", p.name),
    h("small", [...(p.tags || []), `${p.heads} light${p.heads === 1 ? "" : "s"}`].join(" · "))),
    more);
}

async function makeLookButton(p) {
  const q = (state.snap && state.snap.quick) || { buttons: [], slots: 24 };
  const taken = new Set(q.buttons.filter((b) => b.page === 1).map((b) => b.slot));
  let slot = 1;
  while (taken.has(slot) && slot <= (q.slots || 24)) slot++;
  if (slot > (q.slots || 24)) { toast("Buttons page 1 is full", "bad"); return; }
  const r = await run("quick_set", { page: 1, slot, button: { kind: "preset", label: p.name.slice(0, 24), preset: p.n,
    tint: (p.hexes && p.hexes[0]) || undefined } });
  if (r.ok) toast(`Button 1.${slot}: ${p.name}`, "ok");
}

async function saveLook() {
  const parts = [["intensity", "Level"], ["colour", "Colour"], ["position", "Position"], ["beam", "Beam"],
    ["fx", "Effects & movements"], ["other", "Other"]];
  const on = new Set(parts.map(([k]) => k));
  const name = h("input", { type: "text", placeholder: "e.g. Opening sweep", maxlength: 32, style: { width: "100%" } });
  const chipsBox = h("div.chip-row");
  const draw = () => chipsBox.replaceChildren(...parts.map(([k, label]) => h("button.chip" + (on.has(k) ? ".on" : ""), {
    type: "button", onclick: () => { if (on.has(k)) on.delete(k); else on.add(k); draw(); } }, label)));
  draw();
  const go = async () => {
    const r = await run("record_preset", { name: name.value.trim(), include: [...on] }, { toast: true });
    if (r.ok) close();
  };
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const close = modal({
    title: "Save look",
    body: h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
      h("label.field", h("span", "Name"), name),
      h("div", h("span.muted.small", "Include:"), chipsBox),
      h("p.muted.small", { style: { margin: 0 } }, hasSel()
        ? "Saves what the selected lights are doing. Playing it later with nothing selected uses these same lights."
        : "Saves every light the programmer holds and the effects running on them.")),
    foot: [h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", { onclick: go }, "Save look")],
  });
}

// A palette tile: lit while the selection holds it.  Tap: put it on the
// selection; tap a lit one: take it off again (that part of the programmer,
// for these lights).  Right-click / long-press: update, rename, delete.
function palButton(kind, p) {
  const v = p.values || {};
  // a colour's swatch; a beam palette's gobo, as the light throws it
  const sw = kind === "colour" && ("red" in v || "green" in v || "blue" in v)
    ? h("i", { style: { background: rgbToHex(v.red || 0, v.green || 0, v.blue || 0) } })
    : kind === "beam" ? paletteSwatch("beam", p) : null;
  const lit = paletteOn(kind, p);
  const opts = (e) => {
    e.preventDefault();
    menu(e.currentTarget, [
      { label: "Update from the selection", hint: "record it again", run: () => hasSel() ? run("record_palette", { kind, name: p.name }, { toast: true }) : toast("Select fixtures first") },
      { label: "Rename…", run: async () => { const name = await promptBox("Rename palette", "Name", p.name, { ok: "Rename" }); if (name) run("palette_edit", { kind, palette: p.n, name }, { toast: true }); } },
      "-",
      { label: "Delete", danger: true, run: async () => { if (await confirmBox("Delete palette", `Delete “${p.name}”? Cues keep their values. Ctrl+Z brings it back.`, { ok: "Delete", danger: true })) run("palette_edit", { kind, palette: p.n, delete: true }, { toast: true }); } },
    ]);
  };
  return h("button.pal-item" + (lit ? ".on" : ""), {
    title: lit ? `${p.name} is on the selection - tap to take it off (right-click: rename, delete)` : `Apply ${p.name} to the selection (right-click: rename, delete)`,
    "aria-pressed": String(lit),
    onclick: () => {
      if (!hasSel()) { toast("Select fixtures first"); return; }
      if (lit) run("clear_attrs", { group: kind === "beam" ? "beam" : kind, heads: selected() });
      else run("include_palette", { kind, palette: p.n });
    },
    oncontextmenu: opts,
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
  // FLIP / MIRROR on the Position tab
  $$("[data-pt]").forEach((b) => b.addEventListener("click", () => run(b.dataset.pt === "flip" ? "flip" : "mirror_pan", {}, { toast: true })));
  $$("[data-step]").forEach((b) => b.addEventListener("click", () => run("select_step", { step: b.dataset.step }, { toast: true })));
  $("#lineup-btn").addEventListener("click", () => import("./lineup.js").then((m) => m.openLineUp()));
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
  renderWheels(name);
  for (const n of $$("#prog-subslot .sub-tabs")) n.hidden = n.dataset.for !== name;
  // (on Palettes the wheels are the palette view's Knobs window)
  $$("#prog-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$("#prog-body .tab-pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
  renderHeader();
  renderProgCells();                               // (the tilt per head shows on Position)
  if (name === "beam" || name === "colour") loadAttributes();
  if (name === "fx") loadFx();
  if (["looks", "colour", "position", "beam"].includes(name)) renderLooks(true);   // (each family's palettes)
  if (name === "tools") renderTools();
  if (name === "desk") import("./deskview.js").then((m) => { m.initDeskView(); m.renderDeskView(); });
  if (name === "colour") renderColour();
  if (name === "position") { renderPad(); renderMarks(); }
  if (name === "laser" || name === "sfx") {
    loadAttributes();
    import("./fxpanel.js").then((m) => m.renderFxPane(name, attrState));
  }
  try { localStorage.setItem("haze.progtab", name); } catch (e) { /* ignore */ }
}

function wireActs() {
  // the foot's keys get their icons (icons.js), as on a pro desk
  $("#prog-clear").prepend(icon("clear"));
  $("#prog-make").prepend(icon("button"));
  const upd = $("#prog-update");
  upd.replaceChildren(icon("update"), h("span", "Update"));
  upd.addEventListener("click", () => {
    const on = cueOn();
    if (on) run("record_cue", { playback: on.pb, cue: on.cue, mode: "merge" }, { toast: true });
  });
  const rec = $("#prog-record");
  rec.textContent = rec.textContent.replace(/^●\s*/, "");
  rec.prepend(icon("rec"));
  $("#prog-clear").addEventListener("click", () => run("clear_programmer"));
  $("#prog-record").addEventListener("click", () => openCueDialog());
  $("#prog-make").addEventListener("click", makeHoldButton);
}

// Sub-tabs inside the busy tabs: Position (Point / Move / Limits), Beam
// (Quick / All channels), FX (Effects / Running / Step & paint).  Each part
// of the tab says which sub-tab it is on (data-in); the one picked is
// remembered on this computer.
const SUBS = {
  position: [["point", "Point", "Aim at spots, the room map, the pan / tilt pad"], ["move", "Move", "Movements: circles, A → B in degrees, waves, the light's own moves"],
    ["limits", "Zones & limits", "Where the beams may go (keep-out, stay in a zone), movements fit the floor, each light's own range, laser safety"]],
  beam: [["quick", "Quick", "Strobe, gobo, prism, zoom, iris, frost and focus in one press"], ["all", "All channels", "Every beam channel the light has, as a slider"]],
  fx: [["effects", "Effects", "The effect library and its knobs"], ["running", "Running", "What is running now, and where it comes from"],
    ["paint", "Step & paint", "Step effects from your looks; gradients, pictures and video across the lights"]],
};
function initSubTabs() {
  for (const [pane, subs] of Object.entries(SUBS)) {
    const el = $(`.tab-pane[data-pane="${pane}"]`);
    if (!el) continue;
    let cur = subs[0][0];
    try { cur = localStorage.getItem("haze.sub." + pane) || cur; } catch { /* private window */ }
    if (!subs.some(([k]) => k === cur)) cur = subs[0][0];
    const nav = h("nav.sub-tabs", { role: "tablist", "aria-label": `${pane} parts` });
    const pick = (k) => {
      el.dataset.sub = k;
      for (const b of nav.children) b.setAttribute("aria-selected", String(b.dataset.sub === k));
      try { localStorage.setItem("haze.sub." + pane, k); } catch { /* private window */ }
    };
    nav.append(...subs.map(([k, label, title]) => h("button", { role: "tab", type: "button", dataset: { sub: k }, title, onclick: () => pick(k) },
      label, h("span.sub-count", { hidden: true }))));
    // in the row with the wheels, not a full-width row of its own
    nav.dataset.for = pane;
    nav.hidden = true;
    $("#prog-subslot").append(nav);
    pick(cur);
  }
  // how many effects are running: on the FX tab's Running sub-tab
  on("snapshot", () => {
    const n = ((state.snap && state.snap.fx) || []).length;
    const c = document.querySelector('.sub-tabs[data-for="fx"] [data-sub="running"] .sub-count');
    if (c) { c.hidden = !n; c.textContent = String(n); }
  });
}

export function initProgrammer() {
  initSubTabs();
  // every tab sizes its big parts (fader, colour wheel, map, pad) from the
  // height there is: app.css --ph
  const body = $("#prog-body");
  if (body && window.ResizeObserver) new ResizeObserver(() => body.style.setProperty("--ph", `${body.clientHeight}px`)).observe(body);
  wireActs();
  intFader = vfader($("#int-fader"), { min: 0, max: 100, onInput: (v) => { $("#int-num").textContent = v + "%"; sendIntensity(v); } });
  $$("#int-quick button").forEach((b) => b.addEventListener("click", () => {
    if (!hasSel()) { toast("Select fixtures first"); return; }
    sendIntensity(+b.dataset.level);
  }));
  $("#locate-btn").addEventListener("click", () => run("locate"));
  const hl = $("#hl-btn");
  hl.addEventListener("click", (e) => {
    const cur = (state.snap && state.snap.highlight) || {};
    run("highlight", { state: !cur.on, solo: e.shiftKey || (cur.on ? cur.solo : false) }, { toast: true });
  });
  const syncHl = () => {
    const cur = (state.snap && state.snap.highlight) || {};
    hl.classList.toggle("on", !!cur.on);
    hl.textContent = cur.on && cur.solo ? "Solo" : "Highlight";
  };
  on("snapshot", syncHl);

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
  wireColourMatch();
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
  $("#preset-rec").addEventListener("click", saveLook);
  $("#look-search").addEventListener("input", () => renderLooks(true));
  $$("#prog-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  initWheels();
  // the desk opens on Palettes (Group, Position, Colour, Beam) every time
  showTab("desk");

  const refresh = () => {
    renderHeader();
    renderProgIn();
    renderProgCells();
    renderIntensity();
    if (tab === "colour") renderColour();
    if (tab === "fx") renderRunning();
    if (["looks", "colour", "position", "beam"].includes(tab)) renderLooks();
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
  on("looks", () => { renderProgCells(); if (tab === "position") renderPad(); });
  // heads of a multi-head light picked in the 3D (Select Elements), in this
  // window or the 3D's own window
  const cellsPicked = (m) => {
    if (!m || !Array.isArray(m.cells)) return;
    const sel = selectionHeads().map((x) => x.head_no);
    if (sel.length === 1 && sel[0] === m.head) { state.cells = [...m.cells]; cellsSel = String(m.head); cellsKey = ""; }
    else state.pendingCells = { head: m.head, cells: [...m.cells] };
    renderProgCells();
  };
  on("cells", cellsPicked);
  try { new BroadcastChannel("haze.cells").onmessage = (ev) => cellsPicked(ev.data); } catch (e) { /* an old browser */ }
  on("selection", () => loadPadInfo());
  on("snapshot", () => loadPadInfo());
  void patch; void select;
}
