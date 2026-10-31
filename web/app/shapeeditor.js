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

// A drawn stroke -> a loop of key points: walk it at an even pace, then
// keep the points where it turns (a straight run needs only its ends)
function fromStroke(raw) {
  let len = 0;
  for (let i = 1; i < raw.length; i++) len += Math.hypot(raw[i][0] - raw[i - 1][0], raw[i][1] - raw[i - 1][1]);
  if (len < 0.3) return null;                                   // a tap, not a path
  const N = 48, even = [raw[0]];
  let want = len / N, acc = 0;
  for (let i = 1; i < raw.length && even.length < N; i++) {
    let [x0, y0] = raw[i - 1];
    const [x1, y1] = raw[i];
    let seg = Math.hypot(x1 - x0, y1 - y0);
    while (acc + seg >= want && even.length < N) {
      const f = (want - acc) / seg;
      x0 += (x1 - x0) * f; y0 += (y1 - y0) * f;
      even.push([x0, y0]);
      seg = Math.hypot(x1 - x0, y1 - y0); acc = 0;
    }
    acc += seg;
  }
  // Douglas-Peucker on the loop: drop points that sit on a straight line
  const keep = new Set([0, even.length - 1]);
  const rdp = (a, b, eps) => {
    let worst = -1, wd = 0;
    const [ax, ay] = even[a], [bx, by] = even[b], L = Math.hypot(bx - ax, by - ay) || 1e-9;
    for (let i = a + 1; i < b; i++) {
      const d = Math.abs((bx - ax) * (ay - even[i][1]) - (ax - even[i][0]) * (by - ay)) / L;
      if (d > wd) { wd = d; worst = i; }
    }
    if (worst >= 0 && wd > eps) { keep.add(worst); rdp(a, worst, eps); rdp(worst, b, eps); }
  };
  rdp(0, even.length - 1, 0.05);
  let pts = [...keep].sort((x, y) => x - y).map((i) => even[i].map((v) => +Math.max(-1, Math.min(1, v)).toFixed(3)));
  // a loop: the end that came back near the start is the start
  const [fx, fy] = pts[0], [lx, ly] = pts[pts.length - 1];
  if (pts.length > 3 && Math.hypot(fx - lx, fy - ly) < 0.2) pts = pts.slice(0, -1);
  if (pts.length > 24) pts = pts.filter((_, i) => i % Math.ceil(pts.length / 24) === 0);
  return pts.length >= 2 ? pts : null;
}

// the curve without the box's edge: where a smooth curve swings out past it
function rawAlong(pts, t) {
  const n = pts.length;
  const u = ((t % 1) + 1) % 1 * n;
  const i = Math.floor(u) % n, f = u - Math.floor(u);
  const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
  return [0, 1].map((k) => 0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * f
    + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * f * f + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * f * f * f));
}
// how far past the box the path would go (0: it stays inside)
function overshoot(pts, smooth) {
  if (!smooth) return 0;
  let worst = 0;
  for (let i = 0; i < 400; i++) { const [x, y] = rawAlong(pts, i / 400); worst = Math.max(worst, Math.abs(x) - 1, Math.abs(y) - 1); }
  return Math.max(0, worst);
}

/** Draw a movement of your own.  The square is the light's movement box,
 *  seen from behind the light: right is pan right, up is tilt up, the
 *  middle is where the light is aimed.  The box is the Move tab's Size in
 *  degrees each way (or, with Where: a zone, that zone on the floor). */
export function openShapeEditor(shape) {
  const s = { name: shape ? shape.name : "", points: shape ? shape.points.map((p) => [...p]) : PRESETS.Triangle.map((p) => [...p]),
    smooth: shape ? shape.smooth !== false : true };
  const k = knobsOf() || {};
  const deg = Math.round(+k.size || 20);                     // degrees each way round the aim
  const loopS = k.speed ? Math.max(0.5, Math.round(10 / k.speed) / 10) : 8;   // seconds a loop
  const zone = k.zone ? String(k.zoneName || "zone") : "";
  const svg = el("svg", { viewBox: "-1.32 -1.22 2.64 2.44", class: "shape-svg" });
  const toXY = (ev) => {
    const m = svg.getScreenCTM();
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    return [Math.max(-1, Math.min(1, +pt.x.toFixed(3))), Math.max(-1, Math.min(1, +(-pt.y).toFixed(3)))];
  };
  let drag = -1, raf = 0;
  const dot = el("circle", { r: 0.055, class: "shape-dot" });
  const trail = el("polyline", { class: "shape-trail" });
  // the two motors over one loop: how pan and tilt actually move
  const track = (label) => {
    const g = el("svg", { viewBox: "0 -1.15 4 2.3", preserveAspectRatio: "none", class: "shape-track" });
    const line = el("polyline", { class: "shape-track-line" }), cur = el("line", { y1: -1.15, y2: 1.15, class: "shape-track-cur" });
    g.append(el("line", { x1: 0, y1: 0, x2: 4, y2: 0, class: "shape-track-mid" }), line, cur);
    const read = h("output.shape-track-v");
    return { box: h("div.shape-trackrow", h("span.shape-track-k", label), g, read), line, cur, read };
  };
  const panT = track(zone ? "Across" : "Pan"), tiltT = track(zone ? "Back / front" : "Tilt");
  const status = h("div.shape-status");
  const fitBtn = h("button.btn.small", { onclick: () => fit() }, "Fit inside the box");
  const fmt = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v * deg))}°`;
  const draw = () => {
    const lab = (x, y, text, cls = "") => { const e = el("text", { x, y, class: "shape-lab " + cls, "font-size": cls === "c" ? 0.07 : 0.085 }); e.textContent = text; return e; };
    svg.replaceChildren(el("rect", { x: -1, y: -1, width: 2, height: 2, class: "shape-bg" }),
      ...[-0.5, 0.5].flatMap((v) => [el("line", { x1: v, y1: -1, x2: v, y2: 1, class: "shape-grid" }), el("line", { x1: -1, y1: v, x2: 1, y2: v, class: "shape-grid" })]),
      el("line", { x1: -1, y1: 0, x2: 1, y2: 0, class: "shape-axis" }), el("line", { x1: 0, y1: -1, x2: 0, y2: 1, class: "shape-axis" }),
      el("circle", { cx: 0, cy: 0, r: 0.045, class: "shape-aim" }),
      lab(0, -1.08, zone ? "▲ far side of the " + zone : "▲ tilt up", "t"), lab(0, 1.17, zone ? "▼ near side" : "▼ tilt down", "t"),
      lab(-1.06, 0.03, zone ? "◀" : "◀ pan", "l"), lab(1.06, 0.03, zone ? "▶" : "pan ▶", "r"),
      lab(-0.98, -0.92, zone ? "" : `−${deg}° / +${deg}°`, "c"));
    const path = [];
    for (let i = 0; i <= 240; i++) { const [x, y] = along(s.points, i / 240, s.smooth); path.push(`${x},${-y}`); }
    svg.append(el("polyline", { points: path.join(" "), class: "shape-path" }), trail);
    s.points.forEach(([x, y], i) => {
      const c = el("circle", { cx: x, cy: -y, r: 0.07, class: "shape-pt" + (i === 0 ? " first" : "") });
      const tx = el("text", { x: x + 0.09, y: -y - 0.09, class: "shape-n" });
      tx.textContent = String(i + 1);
      c.addEventListener("pointerdown", (ev) => { ev.stopPropagation(); drag = i; svg.setPointerCapture(ev.pointerId); });
      c.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        if (s.points.length <= 2) { toast("A shape needs at least 2 points"); return; }
        s.points.splice(i, 1); draw();
      });
      svg.append(c, tx);
    });
    svg.append(dot);
    // the motors' tracks over one loop
    const pp = [], tp = [];
    for (let i = 0; i <= 200; i++) { const [x, y] = along(s.points, i / 200, s.smooth); pp.push(`${(i / 200) * 4},${-x}`); tp.push(`${(i / 200) * 4},${-y}`); }
    panT.line.setAttribute("points", pp.join(" "));
    tiltT.line.setAttribute("points", tp.join(" "));
    // does it stay in the box?
    const over = overshoot(s.points, s.smooth);
    status.className = "shape-status " + (over > 0.01 ? "warn" : "ok");
    status.replaceChildren(over > 0.01
      ? h("span", `The smooth curve swings ${Math.round(over * deg)}° past the box near the corners - the light is held at the edge there, so the path goes flat. `, fitBtn)
      : h("span", "✓ The whole path stays inside the box."));
  };
  // shrink the points round the middle until the curve stays inside
  const fit = () => {
    for (let n = 0; n < 40 && overshoot(s.points, s.smooth) > 0.005; n++) s.points = s.points.map(([x, y]) => [+(x * 0.97).toFixed(3), +(y * 0.97).toFixed(3)]);
    draw();
  };
  // draw the path: press and drag on the box, let go - the stroke becomes
  // a loop of key points (no clicking point by point); a point dragged
  // afterwards fine-tunes it
  let stroke = null;
  const ink = el("polyline", { class: "shape-ink" });
  svg.addEventListener("pointerdown", (ev) => {
    stroke = [toXY(ev)];
    svg.setPointerCapture(ev.pointerId);
    svg.append(ink);
    ink.setAttribute("points", "");
  });
  svg.addEventListener("pointermove", (ev) => {
    if (drag >= 0) { s.points[drag] = toXY(ev); draw(); return; }
    if (!stroke) return;
    const p = toXY(ev), q = stroke[stroke.length - 1];
    if (Math.hypot(p[0] - q[0], p[1] - q[1]) < 0.015) return;
    stroke.push(p);
    ink.setAttribute("points", stroke.map(([x, y]) => `${x},${-y}`).join(" "));
  });
  svg.addEventListener("pointerup", () => {
    drag = -1;
    if (!stroke) return;
    const pts = fromStroke(stroke);
    stroke = null;
    ink.remove();
    if (pts) { s.points = pts; draw(); }
    else toast("Press and drag to draw the path the beam goes round");
  });
  const now = h("output.shape-now");
  const t0 = performance.now();
  const spin = () => {
    const ph = ((performance.now() - t0) / 1000 / loopS) % 1;
    const [x, y] = along(s.points, ph, s.smooth);
    dot.setAttribute("cx", x); dot.setAttribute("cy", -y);
    const tr = [];
    for (let i = 12; i >= 0; i--) { const [a, b] = along(s.points, ph - i * 0.006, s.smooth); tr.push(`${a},${-b}`); }
    trail.setAttribute("points", tr.join(" "));
    for (const [tk, v] of [[panT, x], [tiltT, y]]) { tk.cur.setAttribute("x1", ph * 4); tk.cur.setAttribute("x2", ph * 4); tk.read.textContent = zone ? `${Math.round(v * 100)}%` : fmt(v); }
    now.textContent = zone ? "" : `now: pan ${fmt(x)}, tilt ${fmt(y)}`;
    raf = requestAnimationFrame(spin);
  };
  const name = h("input.input", { type: "text", maxlength: 32, value: s.name, placeholder: "Name, e.g. Star", oninput: (e) => { s.name = e.target.value; } });
  const smooth = h("div.chip-row", ...[[true, "Smooth curve", "A rounded path through the points"], [false, "Straight lines", "Point to point: sharp corners, the motors stop and turn at each"]].map(([v, l, tt]) =>
    h("button.chip" + (s.smooth === v ? ".on" : ""), { title: tt, onclick: (e) => {
      s.smooth = v;
      for (const b of e.currentTarget.parentNode.children) b.classList.toggle("on", b === e.currentTarget);
      draw();
    } }, l)));
  const presets = h("div.chip-row", ...Object.keys(PRESETS).map((key) =>
    h("button.chip", { onclick: () => { s.points = PRESETS[key].map((p) => [...p]); if (!s.name) { s.name = key; name.value = key; } draw(); } }, key)));
  draw();
  spin();
  const how = zone
    ? `The box is the ${zone} on the floor, seen from above: every beam draws this shape on it, wherever the light hangs.`
    : `The box is what the light can reach round where it is aimed: ${deg}° each way (the Move tab's Size). Seen from behind the light - right is pan right, up is tilt up. The dot is the beam; the two tracks below are what the pan and the tilt motors do in one loop (${loopS} s at the Move tab's Speed).`;
  const body = h("div.shape-ed",
    h("div.shape-left", svg, now,
      h("div.shape-tracks", panT.box, tiltT.box),
      h("p.muted.small", "Draw the path: press and drag on the box with the mouse or a finger, then let go. Drag a point to fine-tune it, double-click one to take it away. The beam goes round in the order you drew it (1 is yellow).")),
    h("div.shape-right",
      h("label.field", h("span", "Name"), name),
      h("div.field", h("span", "Path"), smooth),
      h("div.field", h("span", "Start from"), presets),
      h("p.small.shape-how", how),
      status,
      h("p.muted.small", "Size, speed, a wave across the lights and Where are the Move tab's, used when the shape runs - change them there.")));
  const save = async (andRun) => {
    const r = await run("shape_save", { id: shape ? shape.id : undefined, shape: { name: s.name || "Shape", points: s.points, smooth: s.smooth } }, { toast: true });
    if (r.ok) { close(); if (andRun) runShape(r.id); }
  };
  const close = modal({
    title: shape ? `Shape: ${shape.name}` : "New shape", body, wide: true,
    foot: [shape ? h("button.btn.ghost", { onclick: async () => {
      if (await confirmBox("Delete shape", `Delete “${shape.name}”?`, { ok: "Delete", danger: true })) { await run("shape_delete", { id: shape.id }, { toast: true }); close(); }
    } }, "Delete") : null, h("span.grow"), h("button.btn", { onclick: () => save(false) }, "Save"),
    h("button.btn.primary", { title: "Save it and run it on the selected moving lights", onclick: () => save(true) }, "Save and run")].filter(Boolean),
    onClose: () => cancelAnimationFrame(raf),
  });
}

let knobsOf = () => ({});
export function setShapeKnobs(fn) { knobsOf = fn; }

/** Run a shape on the selected movers with the Move tab's knobs. */
export function runShape(id) {
  const k = knobsOf();
  return run("run_shape", { id, speed: k.speed, size: k.size, spread: k.spread, direction: k.direction, zone: k.zone }, { toast: true });
}

/** The Shapes row of the Move tab. */
export function shapesRow() {
  return h("div.mv-row", h("span.k", "Shapes"), h("span.chip-row",
    ...shapes().map((sh) => h("span.shape-chip",
      h("button.chip", { title: `Run ${sh.name} (${sh.points.length} points) with the Size and Speed below`, onclick: () => runShape(sh.id) }, sh.name),
      h("button.chip.ghost", { title: "Edit", onclick: () => openShapeEditor(sh) }, "✎"))),
    h("button.chip", { title: "Draw a movement of your own", onclick: () => openShapeEditor(null) }, "+ New shape…")));
}
