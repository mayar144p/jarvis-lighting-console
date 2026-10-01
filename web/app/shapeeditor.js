// Move tab -> Shapes: a movement of your own.  Put key points on the square
// (where the beam goes, round where the light is aimed - up is tilt up):
// click to add one, drag to move it, double-click to take it away.  The
// lights go round the points in order, along a smooth curve through them
// or straight from point to point, at the Size and Speed of the Move tab.
import { run } from "./actions.js";
import { state } from "./store.js";
import { h, modal, toast, confirmBox } from "./ui.js";

const SVGNS = "http://www.w3.org/2000/svg";
const SIZE = 240;
const PRESETS = {
  Triangle: [[0, 0.9], [0.8, -0.6], [-0.8, -0.6]],
  Square: [[-0.8, 0.8], [0.8, 0.8], [0.8, -0.8], [-0.8, -0.8]],
  Star: [[0, 0.9], [0.25, 0.25], [0.9, 0.2], [0.35, -0.2], [0.55, -0.85], [0, -0.45], [-0.55, -0.85], [-0.35, -0.2], [-0.9, 0.2], [-0.25, 0.25]],
  Wave: [[-0.9, 0], [-0.45, 0.6], [0, 0], [0.45, -0.6], [0.9, 0], [0.45, 0.6], [0, 0], [-0.45, -0.6]],
  "Zig-zag": [[-0.9, -0.6], [-0.45, 0.6], [0, -0.6], [0.45, 0.6], [0.9, -0.6]],
};

export const shapes = () => (state.snap && state.snap.shapes) || [];

function el(tag, attrs = {}) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

// the same curve the desk runs (app/motion.py key_frames), for the preview
function along(pts, t, smooth) {
  const n = pts.length;
  const u = ((t % 1) + 1) % 1 * n;
  const i = Math.floor(u) % n, f = u - Math.floor(u);
  const p1 = pts[i], p2 = pts[(i + 1) % n];
  if (!smooth) return [p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f];
  const p0 = pts[(i - 1 + n) % n], p3 = pts[(i + 2) % n];
  return [0, 1].map((k) => Math.max(-1, Math.min(1, 0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * f
    + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * f * f + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * f * f * f))));
}

export function openShapeEditor(shape) {
  const s = { name: shape ? shape.name : "", points: shape ? shape.points.map((p) => [...p]) : PRESETS.Triangle.map((p) => [...p]),
    smooth: shape ? shape.smooth !== false : true };
  const svg = el("svg", { viewBox: `-1.1 -1.1 2.2 2.2`, width: SIZE, height: SIZE, class: "shape-svg" });
  const toXY = (ev) => {
    const m = svg.getScreenCTM();
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    return [Math.max(-1, Math.min(1, +pt.x.toFixed(3))), Math.max(-1, Math.min(1, +(-pt.y).toFixed(3)))];
  };
  let drag = -1, raf = 0;
  const dot = el("circle", { r: 0.06, class: "shape-dot" });
  const draw = () => {
    svg.replaceChildren(el("rect", { x: -1, y: -1, width: 2, height: 2, class: "shape-bg" }),
      el("line", { x1: -1, y1: 0, x2: 1, y2: 0, class: "shape-axis" }), el("line", { x1: 0, y1: -1, x2: 0, y2: 1, class: "shape-axis" }));
    const path = [];
    for (let i = 0; i <= 200; i++) { const [x, y] = along(s.points, i / 200, s.smooth); path.push(`${x},${-y}`); }
    svg.append(el("polyline", { points: path.join(" "), class: "shape-path" }));
    s.points.forEach(([x, y], i) => {
      const c = el("circle", { cx: x, cy: -y, r: 0.07, class: "shape-pt" + (i === 0 ? " first" : "") });
      const t = el("text", { x: x + 0.09, y: -y - 0.09, class: "shape-n" });
      t.textContent = String(i + 1);
      c.addEventListener("pointerdown", (ev) => { ev.stopPropagation(); drag = i; svg.setPointerCapture(ev.pointerId); });
      c.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        if (s.points.length <= 2) { toast("A shape needs at least 2 points"); return; }
        s.points.splice(i, 1); draw();
      });
      svg.append(c, t);
    });
    svg.append(dot);
  };
  svg.addEventListener("pointerdown", (ev) => {
    if (s.points.length >= 32) { toast("At most 32 points"); return; }
    // a new point goes in after the nearest one, so the loop stays in order
    const p = toXY(ev);
    let best = 0, bd = Infinity;
    s.points.forEach((q, i) => { const d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2; if (d < bd) { bd = d; best = i; } });
    s.points.splice(best + 1, 0, p);
    drag = best + 1;
    svg.setPointerCapture(ev.pointerId);
    draw();
  });
  svg.addEventListener("pointermove", (ev) => { if (drag >= 0) { s.points[drag] = toXY(ev); draw(); } });
  svg.addEventListener("pointerup", () => { drag = -1; });
  const t0 = performance.now();
  const spin = () => {
    const [x, y] = along(s.points, (performance.now() - t0) / 4000, s.smooth);
    dot.setAttribute("cx", x); dot.setAttribute("cy", -y);
    raf = requestAnimationFrame(spin);
  };
  const name = h("input.input", { type: "text", maxlength: 32, value: s.name, placeholder: "Name, e.g. Star", oninput: (e) => { s.name = e.target.value; } });
  const smooth = h("div.chip-row", ...[[true, "Smooth curve"], [false, "Straight lines"]].map(([v, l]) =>
    h("button.chip" + (s.smooth === v ? ".on" : ""), { onclick: (e) => {
      s.smooth = v;
      for (const b of e.currentTarget.parentNode.children) b.classList.toggle("on", b === e.currentTarget);
      draw();
    } }, l)));
  const presets = h("div.chip-row", h("span.muted.small", "Start from:"), ...Object.keys(PRESETS).map((k) =>
    h("button.chip", { onclick: () => { s.points = PRESETS[k].map((p) => [...p]); if (!s.name) { s.name = k; name.value = k; } draw(); } }, k)));
  draw();
  spin();
  const body = h("div.shape-ed", h("div.shape-left", svg, h("p.muted.small", "Click to add a point · drag to move · double-click to remove. Up is tilt up; the dot shows the path.")),
    h("div.shape-right", h("label.field", h("span", "Name"), name), smooth, presets));
  const save = async (andRun) => {
    const r = await run("shape_save", { id: shape ? shape.id : undefined, shape: { name: s.name || "Shape", points: s.points, smooth: s.smooth } }, { toast: true });
    if (r.ok) { close(); if (andRun) runShape(r.id); }
  };
  const close = modal({
    title: shape ? `Shape: ${shape.name}` : "New shape", body, wide: true,
    foot: [shape ? h("button.btn.ghost", { onclick: async () => {
      if (await confirmBox("Delete shape", `Delete “${shape.name}”?`, { ok: "Delete", danger: true })) { await run("shape_delete", { id: shape.id }, { toast: true }); close(); }
    } }, "Delete") : null, h("span.grow"), h("button.btn", { onclick: () => save(false) }, "Save"),
    h("button.btn.primary", { onclick: () => save(true) }, "Save and run")].filter(Boolean),
    onClose: () => cancelAnimationFrame(raf),
  });
}

let knobsOf = () => ({});
export function setShapeKnobs(fn) { knobsOf = fn; }

/** Run a shape on the selected movers with the Move tab's knobs. */
export function runShape(id) {
  const k = knobsOf();
  return run("run_shape", { id, speed: k.speed, size: k.size, spread: k.spread, direction: k.direction }, { toast: true });
}

/** The Shapes row of the Move tab. */
export function shapesRow() {
  return h("div.mv-row", h("span.k", "Shapes"), h("span.chip-row",
    ...shapes().map((sh) => h("span.shape-chip",
      h("button.chip", { title: `Run ${sh.name} (${sh.points.length} points) with the Size and Speed below`, onclick: () => runShape(sh.id) }, sh.name),
      h("button.chip.ghost", { title: "Edit", onclick: () => openShapeEditor(sh) }, "✎"))),
    h("button.chip", { title: "Draw a movement of your own", onclick: () => openShapeEditor(null) }, "+ New shape…")));
}
