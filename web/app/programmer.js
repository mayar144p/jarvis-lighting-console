// The programmer: what the selected heads are being told to do.
import { get } from "./api.js";
import { state, on, patch, selected, selectionHeads } from "./store.js";
import { run, select } from "./actions.js";
import { $, $$, h, vfader, throttle, toast, promptBox, confirmBox } from "./ui.js";
import { createPicker, rgbToHex } from "./picker.js";
import { openCueDialog } from "./dialogs.js";

const SWATCHES = [
  ["Red", "#ff2a1f"], ["Orange", "#ff7a00"], ["Amber", "#ffb000"], ["Yellow", "#ffe600"],
  ["Green", "#1aff4a"], ["Cyan", "#00e5ff"], ["Blue", "#1f4bff"], ["Congo", "#5b2bff"],
  ["UV", "#8f00ff"], ["Magenta", "#ff00d4"], ["Pink", "#ff5fa2"], ["Lavender", "#b69cff"],
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

function renderIntensity() {
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
  const heads = selectionHeads();
  const colourable = heads.filter((x) => (x.map || []).some((r) => ["red", "wheel", "cyan", "white"].includes(r))).length;
  $("#colour-reach").textContent = heads.length
    ? (colourable === heads.length ? `reaches all ${heads.length}` : `reaches ${colourable} of ${heads.length} - the rest have no colour mixing`)
    : "";
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

function wirePad() {
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

function renderAttributes() {
  const box = $("#attr-list");
  if (!attrState || !attrState.pages) {
    box.replaceChildren(h("p.muted.small", "Select fixtures to see every attribute they have."));
    return;
  }
  const rows = [];
  for (const page of attrState.pages) {
    if (page.page === "intensity") continue;
    const attrs = (page.attrs || []).filter((a) => !["red", "green", "blue", "pan", "tilt", "pan_fine", "tilt_fine"].includes(a.role));
    if (!attrs.length) continue;
    rows.push(h("div.attr-page", page.page));
    for (const a of attrs) rows.push(attrRow(a));
  }
  box.replaceChildren(...(rows.length ? rows : [h("p.muted.small", "These fixtures have no beam attributes.")]));
}

function attrRow(a) {
  const full = a.full || 255;
  const val = a.value === null || a.value === undefined ? null : a.value;
  const fill = h("div.fill", { style: { width: val === null ? "0%" : (val / full * 100) + "%" } });
  const bar = h("div.hbar", { title: "Drag to set" }, fill);
  const out = h("output", val === null ? "–" : a.mixed ? "mix" : String(Math.round(val)));
  const row = h("div.attr" + (a.set ? ".set" : "") + (a.partial ? ".partial" : ""),
    h("label", { title: a.partial ? `only ${a.heads} of the selection have ${a.role}` : a.role }, a.role.replace("_", " ")),
    bar, out,
    h("button.clr", { title: "Remove from the programmer", onclick: () => run("set_attr_range", { attribute: a.role, clear: true }).then(loadAttributes) }, "×"));
  let dragging = false;
  const set = (e) => {
    const r = bar.getBoundingClientRect();
    const t = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const v = Math.round(t * full);
    fill.style.width = t * 100 + "%";
    out.textContent = v;
    row.classList.add("set");
    sendAttr(a.role, v);
  };
  bar.addEventListener("pointerdown", (e) => { dragging = true; bar.setPointerCapture(e.pointerId); set(e); });
  bar.addEventListener("pointermove", (e) => { if (dragging) set(e); });
  bar.addEventListener("pointerup", () => { dragging = false; setTimeout(loadAttributes, 250); });
  return row;
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
    grid.replaceChildren(h("p.muted.small", "Select fixtures to see the effects they can run."));
    $("#fx-params").replaceChildren();
  } else {
    grid.replaceChildren(...(fxAvailable.available || []).map((fx) => h("button.fx-card" + (fxChosen === fx.name ? ".on" : ""), {
      title: "Run " + fx.label,
      onclick: () => startFx(fx),
    }, h("b", fx.label), h("small", fx.group))));
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

function renderRunning() {
  const list = (state.snap && state.snap.fx) || [];
  const box = $("#fx-running");
  if (!list.length) { box.replaceChildren(h("p.muted.small", "No effects running.")); return; }
  box.replaceChildren(...list.map((f) => h("div.fx-run",
    h("b", f.label || `${f.kind || "wave"} ${f.role || ""}`),
    h("small", `${(f.heads || []).length} heads`),
    h("button.btn.small", { onclick: () => run("stop_fx", { id: f.id }) }, "Stop"))),
  h("button.btn.small.ghost", { onclick: () => run("stop_fx", {}) }, "Stop all"));
}

// ----------------------------------------------------------------- looks
function renderLooks() {
  const pals = (state.snap && state.snap.palettes) || {};
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
  if (name === "beam") loadAttributes();
  if (name === "fx") loadFx();
  if (name === "looks") renderLooks();
  if (name === "tools") renderTools();
  if (name === "colour") renderColour();
  if (name === "position") renderPad();
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
    renderIntensity();
    if (tab === "colour") renderColour();
    if (tab === "fx") renderRunning();
    if (tab === "looks") renderLooks();
  };
  on("snapshot", () => {
    refresh();
    if (tab === "beam") loadAttributes();
    if (tab === "tools") renderTools();
  });
  on("lite", refresh);
  on("selection", () => {
    refresh();
    if (tab === "beam") loadAttributes();
    if (tab === "fx") loadFx();
    if (tab === "tools") renderTools();
    if (tab === "position") renderPad();
  });
  on("looks", () => { if (tab === "position") renderPad(); });
  void patch; void select;
}
