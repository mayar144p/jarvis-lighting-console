// Teach the wheel: a light whose file doesn't list its colour wheel (or
// gobo / prism) positions.  Step through it on the real light, and at each
// colour give it a name (and a swatch); saved with the fixture, so every
// light of that model gets real named buttons instead of guesses.
import { act } from "./api.js";
import { run } from "./actions.js";
import { selectionHeads } from "./store.js";
import { h, modal, toast, throttle } from "./ui.js";

const WHAT = { wheel: "colour wheel", wheel2: "colour wheel 2", gobo: "gobo wheel", gobo2: "gobo wheel 2", prism: "prism" };
const SWATCH = ["#ffffff", "#ff0000", "#ff7f00", "#ffd000", "#00c000", "#00d0d0", "#0040ff", "#8000ff", "#ff00c0", "#ffb070", "#b0d0ff"];

export function openTeachWheel(role = "wheel", onDone = null) {
  const sel = selectionHeads().filter((x) => (x.map || []).includes(role));
  if (!sel.length) { toast(`Select a light with a ${WHAT[role] || role}`); return; }
  const first = sel[0];
  const same = sel.filter((x) => x.model === first.model && x.mode === first.mode).map((x) => x.head_no);
  let value = 0;
  const found = [];
  const push = throttle((v) => run("set_attribute", { attribute: role, value: v, heads: same }, { silentError: true }), 80);
  const out = h("output.mono.teach-val", "0");
  const slider = h("input", { type: "range", min: 0, max: 255, value: 0 });
  const setV = (v) => { value = Math.max(0, Math.min(255, Math.round(v))); slider.value = value; out.textContent = value; push(value); };
  slider.addEventListener("input", () => setV(+slider.value));
  const step = (d, label) => h("button.btn.small", { onclick: () => setV(value + d) }, label);
  const name = h("input", { type: "text", placeholder: role.startsWith("wheel") ? "e.g. Red" : "e.g. Stars" });
  let hex = "";
  const sw = role.startsWith("wheel") ? h("div.chip-row", ...SWATCH.map((c) => {
    const b = h("button.swatch-mini", { style: { background: c }, title: c, onclick: () => { hex = c; for (const x of sw.children) x.classList.toggle("on", x === b); } });
    return b;
  })) : null;
  const list = h("div.teach-list");
  const draw = () => list.replaceChildren(...(found.length ? found.sort((a, b) => a.value - b.value).map((f, i) =>
    h("div.teach-row", f.hex ? h("i.slot-dot", { style: { background: f.hex } }) : null, h("b", f.name), h("span.mono.muted", ` DMX ${f.value}`),
      h("button.btn.small.ghost", { title: "Go there", onclick: () => setV(f.value) }, "Show"),
      h("button.btn.small.ghost", { title: "Remove", onclick: () => { found.splice(i, 1); draw(); } }, "×")))
    : [h("p.muted.small", "Nothing yet. Move the slider until the real light shows the first colour, name it, Add; then the next.")]));
  const add = () => {
    const n = name.value.trim();
    if (!n) { toast("Give it a name"); name.focus(); return; }
    const at = found.findIndex((f) => f.value === value);
    const row = { name: n, value, ...(hex ? { hex } : {}) };
    if (at >= 0) found[at] = row; else found.push(row);
    name.value = "";
    hex = "";
    if (sw) for (const x of sw.children) x.classList.remove("on");
    draw();
    setV(value + 8);                           // on towards the next one
  };
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  const body = h("div.teach",
    h("p.muted.small", `${first.model} (${first.mode}): its file doesn't say where the ${WHAT[role] || role} positions are. Watch the real light (${same.length} of them follow this slider), stop on each position, name it and Add. Saved for every light of this model.`),
    h("div.teach-dial", slider, out),
    h("div.row-btns", step(-8, "−8"), step(-1, "−1"), step(1, "+1"), step(8, "+8")),
    h("div.row-btns", name, h("button.btn.primary", { onclick: add }, "Add this position")),
    sw, h("h3", "Positions"), list);
  draw();
  const close = modal({
    title: `Teach the ${WHAT[role] || role}`, body,
    foot: [h("button.btn.ghost", { onclick: async () => {
      await run("teach_slots", { head: first.head_no, role, clear: true }, { toast: true });
      close(); onDone && onDone();
    } }, "Forget taught"), h("span.grow"),
    h("button.btn", { onclick: () => close() }, "Cancel"),
    h("button.btn.primary", { onclick: async () => {
      const r = await act("teach_slots", { head: first.head_no, role, slots: found });
      if (!r.ok) { toast(r.error || "Couldn't save", "bad"); return; }
      toast(r.summary, "ok");
      close(); onDone && onDone();
    } }, "Save")],
  });
  setV(0);
}
