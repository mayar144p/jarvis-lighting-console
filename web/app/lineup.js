// Line up a light with the real one (Programmer -> Tools -> Line up...):
// point the REAL light at two or three marks on the floor, say where each
// mark is, and the desk learns where that light's pan and tilt zero really
// are - every aim, spot, move and the 3D then agree with the real light.
import { h, modal, toast } from "./ui.js";
import { run, select } from "./actions.js";
import { patch, selected, on } from "./store.js";

export function openLineUp() {
  const movers = selected().map((n) => patch().find((x) => x.head_no === n))
    .filter((x) => x && (x.map || []).includes("pan") && (x.map || []).includes("tilt"));
  if (!movers.length) { toast("Select the moving light to line up"); return; }
  const head = movers[0].head_no;
  if (selected().length !== 1) select([head]);
  const now = () => patch().find((x) => x.head_no === head) || {};

  const status = h("p.lineup-now");
  const marks = h("p.muted.small");
  const x = h("input.num", { type: "number", step: 0.1, placeholder: "x" });
  const z = h("input.num", { type: "number", step: 0.1, placeholder: "z" });
  const paint = () => {
    const l = now();
    const a = l.align;
    status.textContent = a ? `Lined up: pan ${a.pan >= 0 ? "+" : ""}${a.pan}°, tilt ${a.tilt >= 0 ? "+" : ""}${a.tilt}°` : "Not lined up yet.";
    const n = (l.align_marks || []).length;
    marks.textContent = n ? `${n} mark${n > 1 ? "s" : ""} kept${n < 2 ? " - one or two more, spread over the floor, make it surer" : ""}.` : "No marks kept yet.";
  };
  const nudge = (axis, step) => run("nudge", { axis, step }, { silentError: false });
  const arrows = (axis, label) => h("div.lineup-row", h("span.k", label),
    ...[[-0.02, "◀◀"], [-0.002, "◀"], [0.002, "▶"], [0.02, "▶▶"]].map(([s, t]) =>
      h("button.btn.small", { title: Math.abs(s) > 0.01 ? "coarse" : "fine", onclick: () => nudge(axis, s) }, t)));
  const body = h("div.lineup",
    h("p", `Line up ${movers[0].name || `light ${head}`} with the real one, so it lands where the desk aims it.`),
    h("ol.small",
      h("li", "Light it, then point the REAL light at a mark on the floor (a taped cross, a corner of the stage) with the arrows."),
      h("li", "Type where the mark is - across from the middle (x) and from the back wall (z), in metres - and press Keep this mark."),
      h("li", "Two or three marks spread over the floor, then Work it out.")),
    h("div.row-btns", h("button.btn.small", { onclick: () => run("set_intensity", { level: 100 }) }, "Light it")),
    arrows("pan", "Pan"), arrows("tilt", "Tilt"),
    h("div.lineup-row", h("span.k", "Mark at"), h("label", "x ", x), h("label", "z ", z),
      h("button.btn.small.primary", { onclick: async () => {
        if (x.value === "" || z.value === "") { toast("Type where the mark is: x and z"); return; }
        const r = await run("line_up", { head, x: +x.value, z: +z.value }, { toast: true });
        if (r.ok) { x.value = ""; z.value = ""; }
      } }, "Keep this mark"),
      h("button.btn.small.ghost", { onclick: () => run("line_up", { head, undo_mark: true }) }, "Drop last")),
    marks, status);
  const off = on("snapshot", paint);
  paint();
  modal({
    title: "Line up with the real light", body,
    foot: [h("button.btn.ghost", { onclick: () => run("line_up", { head, clear: true }, { toast: true }) }, "Forget the line-up"),
      h("span.grow"),
      h("button.btn.primary", { onclick: () => run("line_up", { head, solve: true }, { toast: true }) }, "Work it out")],
    onClose: () => { if (typeof off === "function") off(); },
  });
}
