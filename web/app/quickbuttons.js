// Quick buttons: a MagicQ-style grid of instant buttons.  Hold one to
// flash or strobe a group, latch one to run an effect or bump a colour,
// tap one to GO.  Pressing is live (never an undo step); setting a button
// up is an edit, saved with the show.
import { state, on, patch } from "./store.js";
import { run } from "./actions.js";
import { $, $$, h, modal, toast } from "./ui.js";

const KINDS = [
  ["flash", "Flash", "Full on while held"],
  ["strobe", "Strobe", "Strobes while held"],
  ["colour", "Colour bump", "Overrides the colour"],
  ["kill", "Kill", "Turns the target off while held"],
  ["fx", "Effect", "Runs an effect"],
  ["go", "GO", "GO on a playback"],
  ["release", "Release", "Releases a playback"],
  ["preset", "Preset", "Applies a recorded preset"],
  ["blackout", "Blackout", "Everything off while held"],
  // special effects: they never answer a light button, only these
  ["sfx", "Fire SFX", "Confetti / CO2 / flame / sparks (needs ARM)"],
  ["fog", "Fog / haze", "Fog or haze output"],
  ["laser", "Laser", "Laser output (needs ARM)"],
  ["arm", "ARM FX", "Arms fire and lasers"],
  ["fxkill", "KILL FX", "Stops every effect, disarms"],
];
const KIND_COLOUR = { flash: "#f8fafc", strobe: "#fde047", colour: null, kill: "#64748b", fx: "#a78bfa",
  go: "#22c55e", release: "#f97316", preset: "#38bdf8", blackout: "#ef4444",
  sfx: "#f97316", fog: "#cbd5e1", laser: "#22d3ee", arm: "#ef4444", fxkill: "#ef4444" };
const FX_NAMES = [["rainbow", "Rainbow"], ["circle", "Circle"], ["figure_eight", "Figure 8"], ["pan_sweep", "Pan sweep"],
  ["breathe", "Breathe"], ["dimmer_chase", "Dimmer chase"], ["sparks", "Sparks"]];
const TYPES = [["spot", "Moving spots"], ["beam", "Beams"], ["wash", "Washes"], ["par", "PARs"], ["bar", "Bars / battens"]];
const FX_TYPES = [["confetti", "Confetti"], ["co2", "CO2 jets"], ["flame", "Flames"], ["spark", "Spark fountains"],
  ["sfx", "Other effects"], ["atmos", "Fog / haze"], ["laser", "Lasers"]];

let page = 1;
let editing = false;
const held = new Set();

const quick = () => (state.snap && state.snap.quick) || { buttons: [], active: [], pages: 4, slots: 24 };
const activeIds = () => new Set((state.lite && state.lite.quick_active) || quick().active || []);

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

function render() {
  const box = $("#qb-grid");
  if (!box) return;
  const q = quick();
  const active = activeIds();
  const byslot = new Map(q.buttons.filter((b) => b.page === page).map((b) => [b.slot, b]));
  const cells = [];
  for (let slot = 1; slot <= (q.slots || 24); slot++) {
    const b = byslot.get(slot);
    if (!b) {
      cells.push(h("button.qbtn.empty", {
        title: editing ? "Set up this button" : "Empty - press Edit to set it up",
        onclick: () => { if (editing) editButton(slot, null); },
      }, editing ? "+" : ""));
      continue;
    }
    const tint = b.colour || KIND_COLOUR[b.kind] || "#94a3b8";
    const el = h("button.qbtn" + (active.has(b.id) ? ".on" : ""), {
      title: `${b.label} · ${b.kind} · ${b.mode}${editing ? " (click to edit)" : ""}`,
      dataset: { id: b.id },
    }, h("b", b.label), h("small", b.mode === "hold" ? "hold" : b.mode === "latch" ? "latch" : "tap"));
    el.style.setProperty("--tint", tint);
    if (editing) {
      el.addEventListener("click", () => editButton(slot, b));
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
  $$("#qb-pages button").forEach((x) => x.classList.toggle("on", +x.dataset.page === page));
  $("#qb-edit").classList.toggle("on", editing);
  const empty = !q.buttons.some((b) => b.page === page);
  $("#qb-suggest").hidden = !empty;
}

function editButton(slot, btn) {
  const b = btn || { kind: "flash", mode: "hold", label: "", target: { all: true } };
  const label = h("input", { type: "text", value: b.label || "", placeholder: "Label", maxlength: 24 });
  const kind = h("select.select", ...KINDS.map(([k, l]) => h("option", { value: k }, l)));
  kind.value = b.kind;
  const mode = h("select.select", h("option", { value: "hold" }, "Hold (on while pressed)"),
    h("option", { value: "latch" }, "Latch (press on, press off)"), h("option", { value: "tap" }, "Tap (one shot)"));
  mode.value = b.mode;
  const groups = (state.snap && state.snap.groups) || [];
  const target = h("select.select",
    h("option", { value: "all" }, "All (lights for a light button, effects for an FX button)"),
    h("option", { value: "sel" }, `The current selection (${((state.snap && state.snap.selected) || []).length})`),
    ...groups.map((g) => h("option", { value: "g" + g.n }, `Group: ${g.name}`)),
    ...TYPES.map(([k, l]) => h("option", { value: "t" + k }, `Type: ${l}`)),
    ...FX_TYPES.map(([k, l]) => h("option", { value: "t" + k }, `Effect: ${l}`)));
  const t = b.target || {};
  target.value = t.group !== undefined ? "g" + t.group : t.type ? "t" + t.type : t.heads ? "sel" : "all";
  const colour = h("input", { type: "color", value: b.colour || "#ffffff" });
  const level = h("input", { type: "number", min: 1, max: 100, value: b.level ?? 100 });
  const hz = h("input", { type: "number", min: 1, max: 20, step: 0.5, value: b.hz ?? 10 });
  const fx = h("select.select", ...FX_NAMES.map(([k, l]) => h("option", { value: k }, l)));
  fx.value = b.fx || "rainbow";
  const pb = h("input", { type: "number", min: 1, max: 10, value: b.playback ?? 1 });
  const secs = h("input", { type: "number", min: 0.2, max: 600, step: 0.1, value: b.seconds ?? "", placeholder: "machine limit" });
  const cue = h("input", { type: "number", min: 1, value: b.cue ?? "", placeholder: "next" });
  const preset = h("select.select", ...((state.snap && state.snap.presets) || []).map((p) => h("option", { value: p.n }, p.name)));
  if (b.preset) preset.value = b.preset;
  const rows = {
    colour: h("label.field", h("span", "Colour"), colour),
    level: h("label.field", h("span", "Level %"), level),
    hz: h("label.field", h("span", "Rate (flashes a second)"), hz),
    fx: h("label.field", h("span", "Effect"), fx),
    pb: h("label.field", h("span", "Playback"), pb),
    cue: h("label.field", h("span", "Cue"), cue),
    preset: h("label.field", h("span", "Preset"), preset),
    target: h("label.field", h("span", "Lights"), target),
    mode: h("label.field", h("span", "Behaviour"), mode),
    secs: h("label.field", h("span", "Seconds (a tap fires this long)"), secs),
  };
  const show = () => {
    const k = kind.value;
    rows.colour.hidden = !["flash", "strobe", "colour"].includes(k);
    rows.level.hidden = !["flash", "fog"].includes(k);
    rows.secs.hidden = !["sfx", "fog", "laser"].includes(k);
    rows.hz.hidden = k !== "strobe";
    rows.fx.hidden = k !== "fx";
    rows.pb.hidden = !["go", "release"].includes(k);
    rows.cue.hidden = k !== "go";
    rows.preset.hidden = k !== "preset";
    rows.target.hidden = ["go", "release", "preset", "blackout", "arm", "fxkill"].includes(k);
    rows.mode.hidden = ["go", "release", "preset", "arm", "fxkill"].includes(k);
  };
  kind.addEventListener("change", () => {
    show();
    if (!label.value || KINDS.some(([, l]) => l === label.value)) label.value = KINDS.find(([k]) => k === kind.value)[1];
  });
  show();
  const close = modal({
    title: `Button ${page}.${slot}`,
    body: h("div.form-grid", h("label.field", h("span", "Label"), label), h("label.field", h("span", "Does"), kind),
      rows.target, rows.mode, rows.colour, rows.level, rows.hz, rows.secs, rows.fx, rows.pb, rows.cue, rows.preset),
    foot: [
      btn ? h("button.btn.danger", { onclick: async () => {
        await run("quick_set", { page, slot, clear: true });
        close();
      } }, "Clear") : null,
      h("span.grow"),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => {
        const k = kind.value;
        const tv = target.value;
        const button = { kind: k, label: label.value.trim() || KINDS.find(([x]) => x === k)[1], mode: mode.value,
          target: tv === "all" ? { all: true } : tv === "sel" ? { heads: (state.snap && state.snap.selected) || [] }
            : tv[0] === "g" ? { group: +tv.slice(1) } : { type: tv.slice(1) } };
        if (!rows.colour.hidden && (k === "colour" || colour.value.toLowerCase() !== "#ffffff")) button.colour = colour.value;
        if (k === "flash" || k === "fog") button.level = +level.value || 100;
        if (["sfx", "fog", "laser"].includes(k) && secs.value) button.seconds = +secs.value;
        if (k === "strobe") button.hz = +hz.value || 10;
        if (k === "fx") button.fx = fx.value;
        if (k === "go" || k === "release") button.playback = +pb.value || 1;
        if (k === "go" && cue.value) button.cue = +cue.value;
        if (k === "preset") button.preset = +preset.value;
        const r = await run("quick_set", { page, slot, button });
        if (r.ok) close();
      } }, "Save"),
    ],
  });
}

export function initQuickButtons() {
  const box = $("#qb");
  if (!box) return;
  $$("#qb-pages button").forEach((b) => b.addEventListener("click", () => { page = +b.dataset.page; render(); }));
  $("#qb-edit").addEventListener("click", () => { editing = !editing; render(); });
  $("#qb-suggest").addEventListener("click", async () => {
    if (!patch().length) { toast("Add some lights first"); return; }
    const fxOnly = patch().length && patch().every((x) => (x.map || []).some((r) => r.startsWith("fx_") || r.startsWith("laser_") || r === "fog"));
    await run(fxOnly ? "quick_fx_defaults" : "quick_defaults", { page }, { toast: true });
  });
  $("#qb-release").addEventListener("click", () => run("quick_release_all"));
  on("snapshot", render);
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
  render();
}
