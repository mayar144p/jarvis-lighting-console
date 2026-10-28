// The plot (Windows -> Plot): the rig drawn flat - Plan (from above),
// Front or Side - with every light's number on it, after a pro desk's plot
// window.  Click a light to select it (Shift / Ctrl adds), drag a box round
// several; Design mode drags lights to where they really hang.
import { state, on, selected, patch } from "./store.js";
import { run, select } from "./actions.js";
import { h, modal, toast } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of kids) if (c) el.append(c);
  return el;
};

const VIEWS = [["plan", "Plan"], ["front", "Front"], ["side", "Side"]];
const LABELS = [["nos", "Head numbers"], ["names", "Names"], ["dmx", "DMX addresses"]];
const FILTERS = [["all", "All"], ["hung", "Hung"], ["floor", "On the floor"], ["movers", "Movers"]];
let view = "plan", labels = "nos", filter = "all", design = false;
let zoom = 1, panX = 0, panY = 0;

const venue = () => (state.snap && state.snap.venue) || {};
const hung = (hd) => hd.stance ? hd.stance === "hang" : (hd.kind === "truss" || (+hd.y || 0) >= 2);
const isMover = (hd) => (hd.map || []).includes("pan") && (hd.map || []).includes("tilt");

// a point of the room on the drawing: plan = x across, z toward the
// audience (down the page); front = x across, height up; side = z across
const CURVED = new Set(["bar_curve", "vip_booth", "half_table"]);

function flat(x, y, z) {
  if (view === "plan") return [x, z];
  if (view === "front") return [x, -y];
  return [z, -y];
}

function bounds() {
  const v = venue();
  const pts = [];
  const room = v.room || {};
  // Reset view frames the lights (as on a plot); with none, the rigging
  // and stage; the whole room only when there's nothing else
  for (const hd of patch()) pts.push([+hd.x || 0, +hd.y || 0, +hd.z || 0]);
  if (!pts.length) {
    for (const r of v.rigging || []) pts.push(r.a, r.b);
    if (v.stage) pts.push([v.stage.x - v.stage.width / 2, 0, v.stage.z - v.stage.depth / 2], [v.stage.x + v.stage.width / 2, v.stage.height || 0, v.stage.z + v.stage.depth / 2]);
  }
  if (!pts.length && +room.width > 0) {
    const cx = +room.cx || 0, w = +room.width, d = +room.depth || 10, ht = +room.height || 6;
    pts.push([cx - w / 2, 0, 0], [cx + w / 2, ht, d]);
  }
  if (!pts.length) pts.push([-5, 0, 0], [5, 4, 10]);
  const f = pts.map((p) => flat(+p[0] || 0, +p[1] || 0, +p[2] || 0));
  const xs = f.map((p) => p[0]), ys = f.map((p) => p[1]);
  const pad = 1.5;
  return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
}

// a truss as a pro plot draws it: two chords and the zig-zag between them
/** A point along a rig piece, on its curve when it is curved (the desk's
 *  own sum: app/venue.py curve_point). */
function curveAt(r, t) {
  const [ax, ay, az] = r.a, [bx, by, bz] = r.b;
  const dx = bx - ax, dz = bz - az, chord = Math.hypot(dx, dz), th = (+r.bend || 0) * Math.PI / 180;
  if (Math.abs(th) < 1e-6 || chord < 1e-9) return [ax + dx * t, ay + (by - ay) * t, az + dz * t];
  const rad = chord / (2 * Math.sin(th / 2)), p0 = Math.atan2(dz, dx) - th / 2;
  return [ax + rad * (Math.sin(p0 + th * t) - Math.sin(p0)), ay + (by - ay) * t, az + rad * (Math.cos(p0) - Math.cos(p0 + th * t))];
}

function trussPath(a, b, size) {
  const [x1, y1] = flat(...a), [x2, y2] = flat(...b);
  const len = Math.hypot(x2 - x1, y2 - y1);
  if (len < 0.01) return s("rect", { x: x1 - size / 2, y: y1 - size / 2, width: size, height: size, class: "pl-truss" });
  const ux = (x2 - x1) / len, uy = (y2 - y1) / len, nx = -uy * size / 2, ny = ux * size / 2;
  let d = `M${x1 + nx},${y1 + ny}L${x2 + nx},${y2 + ny}M${x1 - nx},${y1 - ny}L${x2 - nx},${y2 - ny}M${x1 + nx},${y1 + ny}`;
  const steps = Math.max(1, Math.round(len / Math.max(size, 0.3)));
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, side = i % 2 ? -1 : 1;
    d += `L${x1 + (x2 - x1) * t + nx * side},${y1 + (y2 - y1) * t + ny * side}`;
  }
  return s("path", { d, class: "pl-truss" });
}

function label(hd) {
  if (labels === "names") return hd.name || String(hd.head_no);
  if (labels === "dmx") return `${hd.universe || 1}.${String(hd.address || 1).padStart(3, "0")}`;
  return String(hd.head_no);
}

function shown(hd) {
  if (filter === "hung") return hung(hd);
  if (filter === "floor") return !hung(hd);
  if (filter === "movers") return isMover(hd);
  return true;
}

function draw(svg, px = 1000) {
  const [x0, y0, x1, y1] = bounds();
  const w = (x1 - x0) / zoom, ht = (y1 - y0) / zoom;
  svg.setAttribute("viewBox", `${x0 + panX + ((x1 - x0) - w) / 2} ${y0 + panY + ((y1 - y0) - ht) / 2} ${w} ${ht}`);
  const v = venue();
  const g = s("g");
  const room = v.room || {};
  // the room, the stage, the zones
  if (+room.width > 0) {
    const cx = +room.cx || 0, rw = +room.width, d = +room.depth || 10, rh = +room.height || 6;
    const [ax, ay] = flat(cx - rw / 2, 0, 0), [bx, by] = flat(cx + rw / 2, rh, d);
    g.append(s("rect", { x: Math.min(ax, bx), y: Math.min(ay, by), width: Math.abs(bx - ax), height: Math.abs(by - ay), class: "pl-room" }));
  }
  if (v.stage) {
    const st = v.stage;
    const [ax, ay] = flat(st.x - st.width / 2, 0, st.z - st.depth / 2), [bx, by] = flat(st.x + st.width / 2, st.height || 0.05, st.z + st.depth / 2);
    g.append(s("rect", { x: Math.min(ax, bx), y: Math.min(ay, by), width: Math.max(0.05, Math.abs(bx - ax)), height: Math.max(0.05, Math.abs(by - ay)), class: "pl-stage" }));
  }
  if (view === "plan") {
    for (const z of v.zones || []) {
      if (!(z.points || []).length) continue;
      g.append(s("polygon", { points: z.points.map(([x, zz]) => `${x},${zz}`).join(" "), class: "pl-zone" }));
    }
  }
  // the room's objects: their outline from above, their box from the front / side
  for (const o of v.objects || []) {
    if (o.kind === "mark") continue;
    const title = s("title", {}, o.name || o.kind.replace(/_/g, " "));
    if (view === "plan") {
      // the 3D turns an object by -rot about up: on this plan (z down) that is +rot
      const t = `translate(${o.x},${o.z}) rotate(${o.rot || 0})`;
      if (CURVED.has(o.kind)) {
        // its half-round (or more) outline, the curve on its +z side
        const R = o.w / 2, a = ((o.arc || 180) * Math.PI) / 360, r0 = o.kind === "half_table" ? 0 : Math.max(0.05, R - o.d);
        const P = (r, f) => `${(r * Math.sin(f)).toFixed(3)},${(r * Math.cos(f)).toFixed(3)}`;
        const big = a > Math.PI / 2 ? 1 : 0;
        const d = `M${P(R, -a)} A${R},${R} 0 ${big} 0 ${P(R, a)} ` + (r0 ? `L${P(r0, a)} A${r0},${r0} 0 ${big} 1 ${P(r0, -a)} Z` : "L0,0 Z");
        g.append(s("path", { d, transform: t, class: "pl-obj" }, title));
      } else if (o.kind === "high_table" || o.kind === "stool" || o.kind === "plant" || o.kind === "table" || o.kind === "pillar") {
        g.append(s("circle", { cx: o.x, cy: o.z, r: o.w / 2, class: "pl-obj" }, title));
      } else {
        g.append(s("rect", { x: -o.w / 2, y: -o.d / 2, width: o.w, height: o.d, transform: t, class: "pl-obj" + (o.kind === "rope" ? " rope" : "") }, title));
      }
    } else {
      const turned = Math.abs((o.rot || 0) % 180) > 45;
      const across = view === "front" ? (turned ? o.d : o.w) : (turned ? o.w : o.d);
      const at = view === "front" ? o.x : o.z, y0 = o.kind === "balcony" ? (o.y || 0) - 0.25 : o.y || 0;
      g.append(s("rect", { x: at - across / 2, y: -(y0 + o.h), width: Math.max(0.03, across), height: Math.max(0.02, o.h), class: "pl-obj" }, title));
    }
  }
  // the centre lines (as on a plot)
  const [cx0, cy0] = flat(0, 0, 0);
  g.append(s("line", { x1: cx0, y1: -1e3, x2: cx0, y2: 1e3, class: "pl-axis" }));
  if (view !== "plan") g.append(s("line", { x1: -1e3, y1: cy0, x2: 1e3, y2: cy0, class: "pl-axis" }));
  for (const r of v.rigging || []) {
    // a curved piece (an arc, a spiral, an S): drawn in short straight bits along its curve
    const n = r.bend ? Math.max(2, Math.ceil(Math.abs(r.bend) / 8)) : 1;
    for (let i = 0; i < n; i++) g.append(trussPath(curveAt(r, i / n), curveAt(r, (i + 1) / n), Math.max(0.2, +r.size || 0.3)));
  }
  // the lights
  const sel = new Set(selected());
  // a light is the same size on screen whatever the zoom (22 px), as on a plot
  const size = 22 * (w / Math.max(200, px));
  for (const hd of patch()) {
    if (!shown(hd)) continue;
    const [px, py] = flat(+hd.x || 0, +hd.y || 0, +hd.z || 0);
    const on = sel.has(hd.head_no);
    const head = s("g", { class: "pl-head" + (on ? " on" : "") + (isMover(hd) ? " mover" : ""), "data-head": hd.head_no, transform: `translate(${px},${py})` },
      isMover(hd) ? s("rect", { x: -size / 2, y: -size / 2, width: size, height: size, rx: size * 0.2 }) : s("circle", { r: size / 2 }),
      s("text", { y: size * 0.32, "font-size": size * 0.9 }, label(hd)));
    head.append(s("title", {}, `${hd.name || "light " + hd.head_no} · ${hd.universe || 1}.${hd.address || 1}`));
    g.append(head);
  }
  svg.replaceChildren(g);
  return { size };
}

export function openPlot() {
  const svg = s("svg", { class: "pl-svg", preserveAspectRatio: "xMidYMid meet" });
  const seg = (list, cur, set) => h("div.seg", ...list.map(([k, l]) => h("button" + (k === cur() ? ".on" : ""),
    { onclick: (e) => { set(k); [...e.currentTarget.parentNode.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); paint(); } }, l)));
  const pick = (list, cur, set) => {
    const el = h("select.select", ...list.map(([k, l]) => h("option", { value: k }, l)));
    el.value = cur();
    el.addEventListener("change", () => { set(el.value); paint(); });
    return el;
  };
  const designBtn = h("button.btn.small" + (design ? ".on" : ""), { title: "Design mode: drag lights to where they really hang", onclick: () => {
    design = !design; designBtn.classList.toggle("on", design); paint();
  } }, "Design mode");
  const bar = h("div.pl-bar",
    pick(LABELS, () => labels, (v) => { labels = v; }),
    designBtn,
    h("button.btn.small.ghost", { title: "See the whole rig", onclick: () => { zoom = 1; panX = 0; panY = 0; paint(); } }, "Reset view"),
    seg(VIEWS, () => view, (v) => { view = v; zoom = 1; panX = 0; panY = 0; }),
    h("span.grow"),
    h("label.pl-filter", "Show ", pick(FILTERS, () => filter, (v) => { filter = v; })));
  const box = h("div.pl-wrap", svg, h("div.pl-band", { hidden: true }));
  const hint = h("p.muted.small", "Click a light to select it (Shift adds) · drag a box round several · wheel zooms · right-drag moves the view.");
  let geo = { size: 0.3 };
  function paint() {
    geo = draw(svg, svg.clientWidth || 1000);
    svg.classList.toggle("designing", design);
  }
  const toRoom = (e) => {
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  };
  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    const g = e.target.closest(".pl-head");
    const p = toRoom(e);
    svg.setPointerCapture(e.pointerId);
    if (e.button === 2 || e.button === 1) { drag = { pan: true, x: e.clientX, y: e.clientY, px: panX, py: panY }; return; }
    if (g) {
      const n = +g.dataset.head;
      if (design) { drag = { move: n, g, x0: p.x, y0: p.y }; return; }
      if (e.shiftKey || e.ctrlKey || e.metaKey) {
        const cur = selected();
        select(cur.includes(n) ? cur.filter((x) => x !== n) : [...cur, n]);
      } else select([n]);
      return;
    }
    drag = { band: true, x0: p.x, y0: p.y, add: e.shiftKey || e.ctrlKey || e.metaKey };
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const p = toRoom(e);
    if (drag.pan) {
      const vb = svg.viewBox.baseVal, k = vb.width / svg.clientWidth;
      panX = drag.px - (e.clientX - drag.x) * k; panY = drag.py - (e.clientY - drag.y) * k;
      paint();
    } else if (drag.move) {
      drag.g.setAttribute("transform", `translate(${p.x},${p.y})`);
      drag.to = [p.x, p.y];
    } else if (drag.band) {
      let r = svg.querySelector(".pl-rect");
      if (!r) { r = s("rect", { class: "pl-rect" }); svg.append(r); }
      r.setAttribute("x", Math.min(drag.x0, p.x)); r.setAttribute("y", Math.min(drag.y0, p.y));
      r.setAttribute("width", Math.abs(p.x - drag.x0)); r.setAttribute("height", Math.abs(p.y - drag.y0));
      drag.x1 = p.x; drag.y1 = p.y;
    }
  });
  svg.addEventListener("pointerup", async () => {
    const d = drag;
    drag = null;
    if (!d) return;
    if (d.move && d.to) {
      const hd = patch().find((x) => x.head_no === d.move);
      if (!hd) return;
      const [a, b] = d.to.map((v) => Math.round(v * 100) / 100);
      const where = view === "plan" ? { x: a, z: b } : view === "front" ? { x: a, y: -b } : { z: a, y: -b };
      await run("set_place", { head: d.move, ...where }, { toast: true });
    } else if (d.band && d.x1 !== undefined) {
      const [lx, hx] = [Math.min(d.x0, d.x1), Math.max(d.x0, d.x1)], [ly, hy] = [Math.min(d.y0, d.y1), Math.max(d.y0, d.y1)];
      const inside = patch().filter((hd) => shown(hd)).filter((hd) => {
        const [px, py] = flat(+hd.x || 0, +hd.y || 0, +hd.z || 0);
        return px >= lx && px <= hx && py >= ly && py <= hy;
      }).map((hd) => hd.head_no);
      if (inside.length) select(d.add ? [...new Set([...selected(), ...inside])] : inside);
      else if (!d.add) select([]);
    }
    paint();
  });
  svg.addEventListener("contextmenu", (e) => e.preventDefault());
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoom = Math.max(0.5, Math.min(12, zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    paint();
  }, { passive: false });
  const off = [on("snapshot", () => { if (!drag) paint(); }), on("selection", () => { if (!drag) paint(); })];
  modal({ title: "Plot", wide: true, body: h("div.pl", bar, box, hint),
    onClose: () => off.forEach((f) => f && f()) });
  paint();
  if (!patch().length) toast("No lights yet: add some, and they show here");
}
