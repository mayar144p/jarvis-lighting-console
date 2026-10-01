// Paperwork, straight from the drawing: the light plot (the room from
// above - stage, zones, rigging - with a symbol per light, its number and
// its DMX address, a legend and a title block), the patch sheet (every
// light by universe and address) and the rigging (pieces, loads, parts).
// Print it or "Save as PDF" from the browser's print dialog.
import { act } from "/app/api.js";

const NS = "http://www.w3.org/2000/svg";
const $ = (s) => document.querySelector(s);
let data = null;

function el(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  for (const k of kids) if (k) e.append(k);
  return e;
}
function hx(tag, attrs = {}, ...kids) {
  if (attrs === null || typeof attrs !== "object" || attrs instanceof Node) { kids.unshift(attrs); attrs = {}; }
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const k of kids) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}

// ------------------------------------------------------------ symbols
// One symbol per kind of light, drawn in a 1 x 1 box around (0, 0), in the
// USITT spirit: outline for a plain light, a yoke for a mover, a bar for a
// batten, a diamond for a laser, a star for special effects.
const GROUPS = {
  mover: ["moving_spot", "moving_beam", "moving_hybrid"], wash: ["moving_wash"], mbar: ["moving_bar"],
  par: ["par", "par_can"], profile: ["profile", "fresnel", "followspot"], bar: ["bar", "cyc", "tube", "matrix"],
  panel: ["wash_panel", "blinder"], strobe: ["strobe"], laser: ["laser"], atmos: ["atmos"],
  sfx: ["confetti", "co2", "flame", "spark", "sfx"], scanner: ["scanner"], effect: ["effect"],
};
const GROUP_OF = Object.fromEntries(Object.entries(GROUPS).flatMap(([g, ts]) => ts.map((t) => [t, g])));
const S = { stroke: "#111", "stroke-width": 0.05, fill: "#fff" };

function symbol(type) {
  const g = el("g");
  switch (GROUP_OF[type] || "generic") {
    case "mover":
      g.append(el("path", { d: "M-0.42,-0.1 L-0.42,0.36 L0.42,0.36 L0.42,-0.1", ...S, fill: "none" }),
        el("circle", { r: 0.32, ...S }), el("path", { d: "M-0.2,0 L0.2,0 M0,-0.2 L0,0.2", ...S }));
      break;
    case "wash":
      g.append(el("path", { d: "M-0.42,-0.1 L-0.42,0.36 L0.42,0.36 L0.42,-0.1", ...S, fill: "none" }),
        el("circle", { r: 0.32, ...S, fill: "#ddd" }));
      break;
    case "mbar":
      g.append(el("rect", { x: -0.5, y: -0.16, width: 1, height: 0.32, rx: 0.06, ...S }),
        ...[-0.3, 0, 0.3].map((x) => el("circle", { cx: x, r: 0.1, ...S })));
      break;
    case "par":
      g.append(el("circle", { r: 0.3, ...S }), el("circle", { r: 0.18, ...S, fill: "none" }));
      break;
    case "profile":
      g.append(el("path", { d: "M-0.22,-0.45 L0.22,-0.45 L0.22,0.2 L0.34,0.45 L-0.34,0.45 L-0.22,0.2 Z", ...S }));
      break;
    case "bar":
      g.append(el("rect", { x: -0.55, y: -0.12, width: 1.1, height: 0.24, ...S }),
        ...[-0.37, -0.12, 0.12, 0.37].map((x) => el("circle", { cx: x, r: 0.07, ...S, fill: "#111" })));
      break;
    case "panel":
      g.append(el("rect", { x: -0.32, y: -0.32, width: 0.64, height: 0.64, ...S }),
        el("circle", { cx: -0.13, r: 0.1, ...S }), el("circle", { cx: 0.13, r: 0.1, ...S }));
      break;
    case "strobe":
      g.append(el("rect", { x: -0.34, y: -0.22, width: 0.68, height: 0.44, ...S }),
        el("path", { d: "M-0.08,-0.16 L0.06,-0.02 L-0.06,0.02 L0.08,0.16", ...S, fill: "none" }));
      break;
    case "laser":
      g.append(el("path", { d: "M0,-0.38 L0.38,0 L0,0.38 L-0.38,0 Z", ...S }), el("path", { d: "M0,0 L0,-0.6", ...S, "stroke-dasharray": "0.08 0.06" }));
      break;
    case "atmos":
      g.append(el("rect", { x: -0.4, y: -0.25, width: 0.8, height: 0.5, rx: 0.12, ...S }),
        el("path", { d: "M-0.2,0.02 q0.1,-0.14 0.2,0 q0.1,0.14 0.2,0", ...S, fill: "none" }));
      break;
    case "sfx": {
      const pts = Array.from({ length: 10 }, (_, i) => {
        const r = i % 2 ? 0.16 : 0.4, a = -Math.PI / 2 + i * Math.PI / 5;
        return `${(r * Math.cos(a)).toFixed(3)},${(r * Math.sin(a)).toFixed(3)}`;
      });
      g.append(el("polygon", { points: pts.join(" "), ...S }));
      break;
    }
    case "scanner":
      g.append(el("rect", { x: -0.2, y: -0.45, width: 0.4, height: 0.7, ...S }), el("path", { d: "M-0.2,0.25 L0.2,0.25 L0,0.45 Z", ...S }));
      break;
    case "effect":
      g.append(el("circle", { r: 0.34, ...S }), ...[0, 1, 2, 3, 4, 5].map((i) =>
        el("circle", { cx: 0.2 * Math.cos(i * Math.PI / 3), cy: 0.2 * Math.sin(i * Math.PI / 3), r: 0.06, ...S, fill: "#111" })));
      break;
    default:
      g.append(el("circle", { r: 0.3, ...S }), el("text", { y: 0.1, "text-anchor": "middle", "font-size": 0.28, fill: "#111" }, document.createTextNode("?")));
  }
  return g;
}

// ------------------------------------------------------------ the plot
function plotSheet(showAddr) {
  const v = data.venue || {};
  const r = v.room || { width: 12, depth: 12, back: -1, cx: 0 };
  const x0 = (r.cx || 0) - r.width / 2, z0 = r.back ?? -1;
  // a deep room is drawn on its side (stage on the left) to fill the page
  const side = r.depth > r.width * 1.15;
  const P = side ? (x, z) => [z, -x] : (x, z) => [x, z];
  const pts = (list) => list.map(([x, z]) => P(x, z).join(",")).join(" ");
  const box = (cx, cz, w, d) => [[cx - w / 2, cz - d / 2], [cx + w / 2, cz - d / 2], [cx + w / 2, cz + d / 2], [cx - w / 2, cz + d / 2]];
  const text = (x, z, str, attrs) => { const [px, py] = P(x, z); return el("text", { x: px, y: py, ...attrs }, document.createTextNode(str)); };
  const pad = 1.2;
  const corners = box((r.cx || 0), z0 + r.depth / 2, r.width, r.depth).map(([x, z]) => P(x, z));
  const minX = Math.min(...corners.map((c) => c[0])), minY = Math.min(...corners.map((c) => c[1]));
  const W = Math.max(...corners.map((c) => c[0])) - minX, H = Math.max(...corners.map((c) => c[1])) - minY;
  const svg = el("svg", { class: "plot", viewBox: `${minX - pad} ${minY - pad} ${W + pad * 2} ${H + pad * 2}`, preserveAspectRatio: "xMidYMid meet" });
  const k = Math.max(0.9, Math.max(r.width, r.depth) / 24);          // symbol size, m
  // walls
  const outline = (r.outline || []).length > 2 ? r.outline : box(r.cx || 0, z0 + r.depth / 2, r.width, r.depth);
  svg.append(el("polygon", { points: pts(outline), fill: "none", stroke: "#111", "stroke-width": 0.12 }));
  // zones and objects, light
  for (const zn of v.zones || []) {
    if (!(zn.points || []).length) continue;
    svg.append(el("polygon", { points: pts(zn.points), fill: zn.kind === "dancefloor" ? "#eef5fb" : "#f6f6f6", stroke: "#bbb", "stroke-width": 0.04, "stroke-dasharray": "0.2 0.12" }));
    const cx = zn.points.reduce((a, q) => a + q[0], 0) / zn.points.length, cz = zn.points.reduce((a, q) => a + q[1], 0) / zn.points.length;
    svg.append(text(cx, cz, (zn.name || zn.kind).toUpperCase(), { "text-anchor": "middle", "font-size": 0.42 * k, fill: "#aaa", "font-weight": 600 }));
  }
  const st = v.stage;
  if (st && st.width) {
    svg.append(el("polygon", { points: pts(box(st.x || 0, (st.z || 0) + (st.depth || 2) / 2, st.width, st.depth || 2)), fill: "#e9e9e9", stroke: "#666", "stroke-width": 0.06 }),
      text(st.x || 0, (st.z || 0) + (st.depth || 2) / 2, "STAGE", { "text-anchor": "middle", "font-size": 0.45 * k, fill: "#888", "font-weight": 700, "dominant-baseline": "middle" }));
  }
  for (const o of v.objects || []) {
    if (o.kind === "mark") {
      const [px, py] = P(o.x, o.z);
      svg.append(el("path", { d: `M${px - 0.2},${py - 0.2} L${px + 0.2},${py + 0.2} M${px + 0.2},${py - 0.2} L${px - 0.2},${py + 0.2}`, stroke: "#c00", "stroke-width": 0.05 }));
      continue;
    }
    const a = -(o.rot || 0) * Math.PI / 180;
    const cs = box(0, 0, o.w, o.d).map(([dx, dz]) => [o.x + dx * Math.cos(a) - dz * Math.sin(a), o.z + dx * Math.sin(a) + dz * Math.cos(a)]);
    svg.append(el("polygon", { points: pts(cs), fill: "#fafafa", stroke: "#999", "stroke-width": 0.04 }));
  }
  // rigging: truss as a double line, poles as a crossed square
  for (const rg of v.rigging || []) {
    const vertical = Math.abs(rg.a[1] - rg.b[1]) > 0.5 && Math.hypot(rg.a[0] - rg.b[0], rg.a[2] - rg.b[2]) < 0.3;
    const [ax, ay] = P(rg.a[0], rg.a[2]), [bx, by] = P(rg.b[0], rg.b[2]);
    if (vertical) {
      const q = Math.max(0.3, rg.size || 0.3);
      svg.append(el("rect", { x: ax - q / 2, y: ay - q / 2, width: q, height: q, fill: "#fff", stroke: "#111", "stroke-width": 0.05 }),
        el("path", { d: `M${ax - q / 2},${ay - q / 2} L${ax + q / 2},${ay + q / 2}`, stroke: "#111", "stroke-width": 0.03 }));
      continue;
    }
    const w = Math.max(0.12, rg.size || 0.3);
    svg.append(el("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: "#111", "stroke-width": w }),
      el("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: "#fff", "stroke-width": Math.max(0.04, w - 0.1) }));
  }
  const named = new Set();
  for (const rg of v.rigging || []) {
    const key = rg.group || rg.id;
    if (named.has(key) || !rg.name) continue;
    named.add(key);
    const members = (v.rigging || []).filter((x) => (x.group || x.id) === key);
    const ends = members.flatMap((x) => [P(x.a[0], x.a[2]), P(x.b[0], x.b[2])]);
    const xs = ends.map((e) => e[0]), ys = ends.map((e) => e[1]);
    const upright = Math.max(...ys) - Math.min(...ys) > Math.max(...xs) - Math.min(...xs);
    const trim = Math.min(rg.a[1], rg.b[1]) - (rg.size || 0.3) / 2;       // its underside
    const label = `${rg.name}${trim > 1 ? ` @ ${trim.toFixed(1)} m` : ""}`;
    // a run across the page: its name to the left; up the page: above it
    svg.append(upright
      ? el("text", { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: Math.min(...ys) - 0.25, "text-anchor": "middle", "font-size": 0.3 * k, fill: "#333", "font-style": "italic" }, document.createTextNode(label))
      : el("text", { x: Math.min(...xs) - 0.2, y: ys.reduce((a, e) => a + e, 0) / ys.length + 0.1, "text-anchor": "end", "font-size": 0.3 * k, fill: "#333", "font-style": "italic" }, document.createTextNode(label)));
  }
  // lights
  for (const L of data.lights) {
    const [px, py] = P(L.x, L.z);
    const g = el("g", { transform: `translate(${px} ${py}) scale(${k})` });
    g.append(symbol(L.type));
    g.append(el("text", { y: -0.5, "text-anchor": "middle", "font-size": 0.34, "font-weight": 700, fill: "#111" }, document.createTextNode(String(L.n))));
    if (showAddr) g.append(el("text", { y: 0.78, "text-anchor": "middle", "font-size": 0.24, fill: "#0b4f86" }, document.createTextNode(`${L.universe}.${String(L.address).padStart(3, "0")}`)));
    svg.append(g);
  }
  // scale bar, and which way the audience is
  const sy = minY + H + 0.7;
  svg.append(el("line", { x1: minX, y1: sy, x2: minX + 2, y2: sy, stroke: "#111", "stroke-width": 0.08 }),
    el("text", { x: minX + 2.2, y: sy + 0.12, "font-size": 0.3 * k, fill: "#111" }, document.createTextNode("2 m")),
    el("text", { x: minX + W, y: sy + 0.12, "text-anchor": "end", "font-size": 0.3 * k, fill: "#666" }, document.createTextNode(side ? "stage ← · audience →" : "stage ↑ · audience ↓")));

  // legend
  const byType = new Map();
  for (const L of data.lights) {
    const k = L.type;
    if (!byType.has(k)) byType.set(k, { label: L.label || k, models: new Map() });
    const m = byType.get(k).models;
    const name = `${L.manufacturer} ${L.model}`.trim();
    m.set(name, (m.get(name) || 0) + 1);
  }
  const legend = hx("div", { class: "legend" }, hx("h3", "Legend"),
    ...[...byType.entries()].map(([t, info]) => {
      const s = el("svg", { viewBox: "-0.6 -0.6 1.2 1.2" });
      s.append(symbol(t));
      return hx("div", { class: "lg" }, s, hx("div", hx("b", info.label),
        ...[...info.models.entries()].map(([n, c]) => hx("small", `${c} × ${n}`))));
    }),
    hx("div", { class: "lg" }, (() => { const s = el("svg", { viewBox: "-0.6 -0.6 1.2 1.2" }); s.append(el("text", { y: 0.1, "text-anchor": "middle", "font-size": 0.4, "font-weight": 700 }, document.createTextNode("12"))); return s; })(),
      hx("div", hx("b", "Light number"), showAddr ? hx("small", "blue: universe.address") : null)));
  const kg = data.rigging.total_kg, watts = data.lights.reduce((a, L) => a + (L.watts || 0), 0);
  const tb = hx("div", { class: "titleblock" },
    hx("div", hx("span", "Show"), data.show || "Untitled show"),
    hx("div", hx("span", "Venue"), v.name || "—"),
    hx("div", hx("span", "Lights"), `${data.lights.length} on ${data.universes.length} universe${data.universes.length === 1 ? "" : "s"}`),
    hx("div", hx("span", "Rigging"), `${kg} kg · ${data.rigging.points} points`),
    hx("div", hx("span", "Date"), new Date().toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) + (watts ? ` · ${Math.round(watts)} W` : "")));
  return hx("section", { class: "sheet", id: "sheet-plot" },
    hx("h1", "Light plot"), hx("div", { class: "meta" }, hx("span", `Room ${r.width} × ${r.depth} m, ${r.height} m high`), hx("span", "Seen from above")),
    hx("div", { class: "plotwrap" }, svg, legend), tb);
}

function patchSheet() {
  const rows = [...data.lights].sort((a, b) => a.universe - b.universe || a.address - b.address);
  const body = [];
  let uni = null;
  for (const L of rows) {
    if (L.universe !== uni) {
      uni = L.universe;
      const used = rows.filter((x) => x.universe === uni).reduce((a, x) => a + x.channels, 0);
      body.push(hx("tr", { class: "uni" }, hx("td", { colspan: 10 }, `Universe ${uni} · ${used} of 512 channels`)));
    }
    body.push(hx("tr",
      hx("td", { class: "n" }, hx("b", L.n)), hx("td", `${L.universe}.${String(L.address).padStart(3, "0")}`),
      hx("td", { class: "n" }, `${L.address}–${L.address + Math.max(1, L.channels) - 1}`), hx("td", L.name),
      hx("td", `${L.manufacturer} ${L.model}`), hx("td", L.mode), hx("td", L.label || L.type),
      hx("td", L.rig || "—"), hx("td", { class: "n" }, `${L.x}, ${L.y}, ${L.z}`),
      hx("td", { class: "n" }, `${L.kg}${L.kg_known ? "" : "*"}${L.watts ? ` · ${Math.round(L.watts)} W` : ""}`)));
  }
  return hx("section", { class: "sheet", id: "sheet-patch" }, hx("h2", "Patch sheet"),
    rows.length ? hx("table", hx("thead", hx("tr", ...["#", "Addr", "Chans", "Name", "Maker / model", "Mode", "Kind", "Hangs on", "x, y, z m", "kg · W"].map((t, i) => hx("th", { class: [0, 2, 8, 9].includes(i) ? "n" : "" }, t)))),
      hx("tbody", ...body)) : hx("p", { class: "empty" }, "No lights patched."),
    hx("p", { class: "meta" }, "* typical weight for the kind of light (its file doesn't say)."));
}

function rigSheet() {
  const rep = data.rigging;
  return hx("section", { class: "sheet", id: "sheet-rig" }, hx("h2", "Rigging"),
    hx("div", { class: "meta" }, hx("span", hx("b", `${rep.total_kg} kg`), " in all"), hx("span", hx("b", rep.points), " pick-up points")),
    rep.rigs.length ? hx("table", hx("thead", hx("tr", ...["Piece", "Pieces", "Length m", "Trim m", "Own kg", "Lights", "Lights kg", "Total kg", "Points", "kg / point", "Notes"].map((t) => hx("th", t)))),
      hx("tbody", ...rep.rigs.map((x) => hx("tr",
        hx("td", hx("b", x.name)), hx("td", { class: "n" }, x.pieces), hx("td", { class: "n" }, x.length), hx("td", { class: "n" }, x.trim ?? "—"),
        hx("td", { class: "n" }, x.self_kg), hx("td", { class: "n" }, x.lights.map((l) => l.head).join(", ") || "—"), hx("td", { class: "n" }, x.light_kg),
        hx("td", { class: "n" }, hx("b", x.total_kg)), hx("td", { class: "n" }, x.ground ? "ground" : x.points), hx("td", { class: "n" }, x.per_point_kg ?? "—"),
        hx("td", { class: "warn" }, x.warnings.join("; ")))))) : hx("p", { class: "empty" }, "No rigging."),
    rep.parts.length ? hx("div", hx("h2", "Parts list"), hx("table", hx("tbody", ...rep.parts.map((p) =>
      hx("tr", hx("td", { class: "n" }, hx("b", `${p.count} ×`)), hx("td", p.name), hx("td", typeof p.length === "number" ? `${p.length} m` : p.length || "")))))) : null,
    hx("p", { class: "meta" }, "A guide for planning: typical truss weights, the lights' own from their library files. Your rigger signs off the real loads."));
}

function draw() {
  if (!data) return;
  const pages = [];
  if ($("#show-plot").checked) pages.push(plotSheet($("#show-addr").checked));
  if ($("#show-patch").checked) pages.push(patchSheet());
  if ($("#show-rig").checked) pages.push(rigSheet());
  $("#pages").replaceChildren(...(pages.length ? pages : [hx("section", { class: "sheet" }, hx("p", { class: "empty" }, "Pick something to print."))]));
}

async function load() {
  try {
    const r = await act("paperwork", {});
    if (!r.ok) throw new Error(r.error || "no paperwork");
    data = r;
    document.title = `Light Plot · ${r.show || "Untitled show"}`;
    draw();
  } catch (e) {
    $("#pages").replaceChildren(hx("section", { class: "sheet" }, hx("p", { class: "empty" }, `Couldn't load the show: ${e.message}`)));
  }
}

for (const id of ["show-plot", "show-patch", "show-rig", "show-addr"]) $("#" + id).addEventListener("change", draw);
$("#reload").addEventListener("click", load);
$("#print").addEventListener("click", () => window.print());
load();
