// Line up lights with the real ones (Programmer -> Rig -> Line up...):
// point each REAL light at two or three marks on the floor (taped crosses,
// the middle of the dance floor, the DJ), and the desk learns where that
// light's pan and tilt zero really are - every aim, spot, move and the 3D
// then agree with the real light.  The marks are the room's own (Arrange's
// marks and zones), so a whole rig is lined up against the same ones, one
// light after another.
import { h, modal, toast } from "./ui.js";
import { run, select } from "./actions.js";
import { state, patch, selected, on } from "./store.js";

const movers = (nums) => nums.map((n) => patch().find((x) => x.head_no === n))
  .filter((x) => x && (x.map || []).includes("pan") && (x.map || []).includes("tilt"));

export function openLineUp(heads = null) {
  let lights = movers(heads || selected());
  if (!lights.length) lights = movers(patch().map((x) => x.head_no));
  if (!lights.length) { toast("No moving lights to line up"); return; }
  const order = lights.map((x) => x.head_no);
  let i = 0;
  const head = () => order[i];
  const now = () => patch().find((x) => x.head_no === head()) || {};

  const spots = () => ((state.snap && state.snap.move_spots) || []).filter((s) => !String(s.key).startsWith("cell"));
  const markSel = h("select.select");
  const x = h("input.num", { type: "number", step: 0.1, placeholder: "x" });
  const z = h("input.num", { type: "number", step: 0.1, placeholder: "z" });
  const typed = h("span.lineup-typed", "x ", x, " z ", z);
  const fillMarks = () => {
    const cur = markSel.value;
    markSel.replaceChildren(...spots().map((s) => h("option", { value: `${s.x},${s.z}` }, `${s.label} (${s.x}, ${s.z})`)),
      h("option", { value: "typed" }, "Somewhere else: type where…"));
    if ([...markSel.options].some((o) => o.value === cur)) markSel.value = cur;
    typed.hidden = markSel.value !== "typed";
  };
  markSel.addEventListener("change", () => { typed.hidden = markSel.value !== "typed"; });
  const where = () => {
    if (markSel.value === "typed") {
      if (x.value === "" || z.value === "") { toast("Type where the mark is: x (across) and z (from the back wall), in metres"); return null; }
      return [+x.value, +z.value];
    }
    const [a, b] = markSel.value.split(",").map(Number);
    return [a, b];
  };

  const lightName = h("b");
  // which way up it is: a light hung the other way round from the rest
  // turns the wrong way - flip it here (Invert pan / tilt, Swap)
  const FLIPS = [["invert_pan", "Invert pan", "Pan goes the other way"], ["invert_tilt", "Invert tilt", "Tilt goes the other way"],
    ["swap", "Swap pan/tilt", "Pan and tilt are swapped on this light"]];
  const flipBtns = FLIPS.map(([k, label, title]) => h("button.btn.small", { title, dataset: { k },
    onclick: () => run("set_orient", { heads: [head()], [k]: !((now().orient || {})[k]) }, { toast: true }) }, label));
  const status = h("p.lineup-now");
  const marks = h("p.muted.small");
  const chips = h("div.lineup-chips");
  const paint = () => {
    const l = now();
    lightName.textContent = `${l.name || `light ${head()}`}  (${i + 1} of ${order.length})`;
    for (const b of flipBtns) b.classList.toggle("on", !!(l.orient || {})[b.dataset.k]);
    const a = l.align;
    status.textContent = a ? `Lined up: pan ${a.pan >= 0 ? "+" : ""}${a.pan}°, tilt ${a.tilt >= 0 ? "+" : ""}${a.tilt}°` : "Not lined up yet.";
    const n = (l.align_marks || []).length;
    marks.textContent = n ? `${n} mark${n > 1 ? "s" : ""} kept${n < 2 ? " - one or two more, spread over the floor, make it surer" : " - Work it out, or keep one more"}.` : "No marks kept for this light yet.";
    chips.replaceChildren(...order.map((n, k) => {
      const hd = patch().find((q) => q.head_no === n) || {};
      return h("button.chip" + (k === i ? ".on" : ""), { title: hd.align ? "Lined up" : "Not lined up yet", onclick: () => go(k) },
        (hd.align ? "✓ " : "") + (hd.name || n));
    }));
    if (!markSel.options.length) fillMarks();
  };
  const go = async (k) => {
    i = (k + order.length) % order.length;
    await select([head()]);
    paint();
  };
  const nudge = (axis, step) => run("nudge", { axis, step });
  const arrows = (axis, label) => h("div.lineup-row", h("span.k", label),
    ...[[-0.02, "◀◀"], [-0.002, "◀"], [0.002, "▶"], [0.02, "▶▶"]].map(([s, t]) =>
      h("button.btn.small", { title: Math.abs(s) > 0.01 ? "coarse" : "fine", onclick: () => nudge(axis, s) }, t)));
  const body = h("div.lineup",
    h("p.small", "If the real light moves the wrong way when you nudge it, flip it under Which way first. Then for each light: pick a mark, Point it there (it gets close), nudge the REAL light until it's on the mark, then It's on the mark. Two or three marks spread over the floor, then Work it out - or Next light, which works it out for you."),
    chips,
    h("div.lineup-row", h("span.k", "Light"), lightName,
      h("button.btn.small", { onclick: () => run("set_intensity", { level: 100 }) }, "Light it"),
      h("button.btn.small.ghost", { title: "Only this light on (find it in the rig)", onclick: () => run("highlight", { state: true, solo: true }) }, "Solo")),
    h("div.lineup-row", h("span.k", "Which way"), ...flipBtns,
      h("span.muted.small", "if it moves the wrong way")),
    h("div.lineup-row", h("span.k", "Mark"), markSel, typed,
      h("button.btn.small", { title: "Aim it at the mark from where the desk thinks it is - then nudge the last bit", onclick: () => {
        const w = where(); if (!w) return;
        run("aim_at", { x: w[0], y: 0, z: w[1], heads: [head()] });
      } }, "Point it there")),
    arrows("pan", "Pan"), arrows("tilt", "Tilt"),
    h("div.lineup-row", h("span.k", ""),
      h("button.btn.small.primary", { onclick: async () => {
        const w = where(); if (!w) return;
        await run("line_up", { head: head(), x: w[0], z: w[1] }, { toast: true });
      } }, "It's on the mark"),
      h("button.btn.small.ghost", { onclick: () => run("line_up", { head: head(), undo_mark: true }) }, "Drop last"),
      h("button.btn.small.ghost", { title: "Keep a typed spot as a mark of the room, for the next lights", onclick: async () => {
        const w = where(); if (!w || markSel.value !== "typed") { toast("Type a spot first (Somewhere else)"); return; }
        const n = spots().filter((s) => String(s.key).startsWith("mark:")).length + 1;
        const r = await run("venue_add", { item: { kind: "mark", name: `Tape ${n}`, x: w[0], z: w[1] } }, { toast: true });
        if (r.ok) setTimeout(() => { fillMarks(); markSel.value = `${w[0]},${w[1]}`; typed.hidden = true; }, 300);
      } }, "Keep as a room mark")),
    marks, status);
  const off = on("snapshot", paint);
  const solve = () => run("line_up", { head: head(), solve: true }, { toast: true });
  modal({
    title: "Line up with the real lights", body,
    foot: [h("button.btn.ghost", { onclick: () => run("line_up", { head: head(), clear: true }, { toast: true }) }, "Forget this light's line-up"),
      h("span.grow"),
      h("button.btn", { onclick: () => go(i - 1) }, "◀ Light"),
      h("button.btn.primary", { onclick: solve }, "Work it out"),
      h("button.btn", { title: "Work this one out (if it has marks) and go to the next light", onclick: async () => {
        if ((now().align_marks || []).length) await solve();
        go(i + 1);
      } }, "Next light ▶")],
    onClose: () => { if (typeof off === "function") off(); run("highlight", { state: false }); },
  });
  fillMarks();
  go(0);
}
