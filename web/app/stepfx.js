// Step effects in the FX tab: effects made of your own looks.  Take a step
// from the programmer (the look the selected lights have now) or from a
// palette, give each step a time and a crossfade, pick a curve and how far
// the lights are spread round the cycle; save it with the show and run it
// like any effect (beat lock and direction in Running).
import { act } from "./api.js";
import { run } from "./actions.js";
import { state, on } from "./store.js";
import { $, h, modal, toast, confirmBox } from "./ui.js";

const list = () => (state.snap && state.snap.step_fx) || [];
let key = "";

export function renderStepFx(force = false) {
  const box = $("#stepfx-list");
  if (!box) return;
  const k = JSON.stringify(list());
  if (!force && k === key) return;
  key = k;
  box.replaceChildren(
    ...list().map((f) => h("div.fx-run",
      h("b", f.name), h("small", `${f.steps.length} steps · ${f.steps.reduce((a, s) => a + s.time, 0).toFixed(1)} s`),
      h("button.btn.small", { onclick: () => run("step_fx_run", { id: f.id }, { toast: true }) }, "Run"),
      h("button.btn.small.ghost", { onclick: () => openStepEditor(f) }, "Edit"),
      h("button.btn.small.ghost", { title: "Delete", onclick: async () => {
        if (await confirmBox("Delete step effect", `Delete “${f.name}”?`, { ok: "Delete", danger: true })) run("step_fx_delete", { id: f.id }, { toast: true });
      } }, "×"))),
    h("button.btn.small", { onclick: () => openStepEditor(null) }, "+ New step effect…"));
}

function paletteOptions() {
  const pals = (state.snap && state.snap.palettes) || {};
  const out = [];
  for (const kind of Object.keys(pals)) for (const p of pals[kind] || []) out.push([`${kind}:${p.n}`, `${kind}: ${p.name}`]);
  return out;
}

function describeStep(st) {
  if (st.palette) {
    const p = ((state.snap && state.snap.palettes) || {})[st.palette.kind] || [];
    const found = p.find((x) => x.n === st.palette.n);
    return `palette: ${st.palette.kind} ${found ? found.name : "#" + st.palette.n}`;
  }
  return `look: ${Object.keys(st.values || {}).length} light(s)`;
}

export function openStepEditor(existing) {
  const fx = existing ? JSON.parse(JSON.stringify(existing)) : { name: "", steps: [], curve: "smooth", spread: 0 };
  const name = h("input", { type: "text", value: fx.name, placeholder: "e.g. Warm to cold" });
  const curve = h("select.select", ...[["smooth", "smooth"], ["linear", "straight"], ["snap", "snap (no fade)"]].map(([v, l]) => h("option", { value: v }, l)));
  curve.value = fx.curve;
  const spread = h("input", { type: "range", min: 0, max: 360, step: 15, value: fx.spread });
  const spreadOut = h("span.mono.small", `${fx.spread}°`);
  spread.addEventListener("input", () => { spreadOut.textContent = `${spread.value}°`; });
  const steps = h("div.step-list");
  const drawSteps = () => {
    steps.replaceChildren(...(fx.steps.length ? fx.steps.map((st, i) => {
      const time = h("input", { type: "number", min: 0.05, step: 0.1, value: st.time ?? 1, title: "Seconds on this step" });
      const fade = h("input", { type: "range", min: 0, max: 100, step: 5, value: Math.round((st.fade ?? 0.5) * 100), title: "How much of it fades in from the step before" });
      time.addEventListener("change", () => { st.time = +time.value || 1; });
      fade.addEventListener("input", () => { st.fade = +fade.value / 100; });
      return h("div.step-row",
        h("b", `${i + 1}`), h("span.step-what", describeStep(st)),
        h("label.snd-k", "s", time), h("label.snd-k", "fade", fade),
        h("button.btn.small.ghost", { title: "Earlier", disabled: i === 0, onclick: () => { [fx.steps[i - 1], fx.steps[i]] = [fx.steps[i], fx.steps[i - 1]]; drawSteps(); } }, "↑"),
        h("button.btn.small.ghost", { title: "Remove", onclick: () => { fx.steps.splice(i, 1); drawSteps(); } }, "×"));
    }) : [h("p.muted.small", "No steps yet: set a look on some lights and take it as a step, then another look, and so on.")]));
  };
  const pal = h("select.select", h("option", { value: "" }, "a palette…"), ...paletteOptions().map(([v, l]) => h("option", { value: v }, l)));
  pal.addEventListener("change", () => {
    if (!pal.value) return;
    const [kind, n] = pal.value.split(":");
    fx.steps.push({ palette: { kind, n: +n }, time: 1, fade: 0.5 });
    pal.value = "";
    drawSteps();
  });
  const take = async () => {
    const r = await act("step_capture", {});
    if (!r.ok) { toast(r.error || "Nothing to take", "bad"); return; }
    fx.steps.push({ ...r.step, time: 1, fade: 0.5 });
    drawSteps();
  };
  const save = async (andRun) => {
    const def = { name: name.value.trim() || "Steps", curve: curve.value, spread: +spread.value, steps: fx.steps };
    const r = await run("step_fx_save", { id: existing ? existing.id : undefined, fx: def }, { toast: true });
    if (!r.ok) return;
    if (andRun) run("step_fx_run", { id: r.id }, { toast: true });
    close();
  };
  const body = h("div.stepfx",
    h("div.form-grid", h("label.field", h("span", "Name"), name), h("label.field", h("span", "Curve"), curve)),
    h("div.field", h("span.muted.small", "Spread: how far round the cycle each light is from the one before (0: all together)"), h("div.row-btns", spread, spreadOut)),
    h("h3", "Steps"), steps,
    h("div.row-btns",
      h("button.btn", { onclick: take, title: "The look the selected lights have in the programmer now" }, "+ Step: the look now"),
      pal),
    h("p.muted.small", "Palette steps follow the palette: change the palette and the effect changes with it."));
  const close = modal({
    title: existing ? `Edit ${existing.name}` : "New step effect", body, wide: true,
    foot: [h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn", { onclick: () => save(false) }, "Save"),
      h("button.btn.primary", { onclick: () => save(true) }, "Save and run")],
  });
  drawSteps();
  return close;
}

export function initStepFx() {
  on("snapshot", () => renderStepFx());
  renderStepFx(true);
}
