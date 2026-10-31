// Aim in the Move tab: point the selected movers by where you are
// pointing.  "Follow me" - press and drag on the 3D view and the lights
// follow the pointer over the floor (a ring marks the spot); the floor map
// does the same from above, when the 3D angle is awkward.
// Both send aim_at: each light solves its own pan/tilt from where it hangs.
import { run } from "./actions.js";
import { state, selectionHeads, on } from "./store.js";
import { h, toast, menu } from "./ui.js";
import { stagePanel } from "./view3d.js";

const SVGNS = "http://www.w3.org/2000/svg";
let following = false;
let target = null;                      // {x, z}: the last spot aimed at
let pending = null, timer = 0, last = 0;
let fan = 0;                            // multi-head lights: metres between their heads' spots
const RATE_MS = 70;
// the beams go straight where they're aimed (a choice of glide speeds
// earned no place on screen; the engine still takes a glide for a cue or a
// script that wants one)
const glide = 0;
let aimNow = null;                      // {x, z}: where the desk has the beams while they glide
// the map turned: "audience" (the stage at the top), "dj" (the way the DJ
// faces at the top, the DJ's left on the left) or a turn in degrees;
// remembered on this computer
let mapTurn = (() => { try { return localStorage.getItem("haze.mapTurn") || "audience"; } catch (e) { return "audience"; } })();
let setting = "";                       // "booth" / "centre" / "front": the next tap on the map sets it
const orient = () => (state.snap && state.snap.venue_orient) || { booth: [0, 1.7, 0], centre: [0, 6], front: 0 };
const turnDeg = () => (mapTurn === "audience" ? 0 : mapTurn === "dj" ? 180 + orient().front : +mapTurn || 0) % 360;

const multiHead = () => selectionHeads().some((x) => (x.map || []).filter((r) => r === "tilt").length > 1);
const movers = () => selectionHeads().filter((x) => (x.map || []).includes("pan") || (x.map || []).includes("tilt"));

/** Aim at (x, y, z): straight there, or - with a Follow speed - the desk
 *  glides the real lights after it (the engine moves them every DMX frame,
 *  whatever this page does). */
export function aimTo(p, final = false) {
  target = { x: p.x, z: p.z };
  drawTarget();
  sendAim(p, final);
}

function sendAim(p, final) {
  pending = { x: +p.x.toFixed(2), y: +(p.y || 0).toFixed(2), z: +p.z.toFixed(2) };
  // a Wave 360 and the like: the heads picked follow on their own, or all
  // of them fan out along the throw
  if (state.cells && state.cells.length) pending.cell = [...state.cells];
  if (fan && multiHead()) pending.spread = fan;
  pending.glide = glide;                // 0: straight there
  const send = () => {
    timer = 0;
    if (!pending) return;
    const q = pending;
    pending = null;
    last = performance.now();
    run("aim_at", q, { silentError: !final });
  };
  const wait = RATE_MS - (performance.now() - last);
  if (final || wait <= 0) { clearTimeout(timer); send(); } else if (!timer) timer = setTimeout(send, wait);
}

async function stage() {
  const m = await stagePanel();
  return m && m.getStage();
}

export async function setFollow(on) {
  const st = await stage();
  if (on && !movers().length) { toast("Select moving lights first"); return; }
  if (!st) { toast("The 3D view isn't open"); return; }
  following = !!on;
  if (following) {
    st.follow((p, up) => {
      if (!movers().length) return;
      aimTo(p, up);
    }, () => { following = false; syncButtons(); });
    toast("Follow: press and drag on the floor - the lights follow. Esc or the button again to stop.", "", 4500);
  } else {
    st.follow(null);
  }
  syncButtons();
}

function syncButtons() {
  for (const b of document.querySelectorAll(".aim-follow")) {
    b.classList.toggle("on", following);
    b.textContent = following ? "✋ Following - drag the 3D floor" : "✋ Follow me";
  }
}

// ------------------------------------------------------------ floor map
function bounds() {
  const v = (state.snap && state.snap.venue) || {};
  const r = v.room || { width: 12, depth: 14, back: -1, cx: 0 };
  const out = (r.outline || []).filter((p) => Array.isArray(p) && p.length >= 2);
  if (out.length >= 3) {
    // the room's own shape (drawn in Arrange): its extent, not a rectangle
    const xs = out.map((p) => +p[0]), zs = out.map((p) => +p[1]);
    const x0 = Math.min(...xs), z0 = Math.min(...zs);
    return { x0, z0, w: Math.max(...xs) - x0, d: Math.max(...zs) - z0, v, outline: out };
  }
  const x0 = (r.cx || 0) - r.width / 2, z0 = r.back ?? -1;
  return { x0, z0, w: r.width, d: r.depth, v, outline: null };
}

function drawTarget() {
  for (const svg of document.querySelectorAll(".aim-map svg")) {
    const t = svg.querySelector(".aim-target");
    if (!t) continue;
    t.style.display = target ? "" : "none";
    if (target) t.setAttribute("transform", `translate(${target.x} ${target.z})`);
    // where the beams are right now, while they glide after the pointer
    const c = svg.querySelector(".aim-now");
    if (c) {
      c.style.display = aimNow ? "" : "none";
      if (aimNow) c.setAttribute("transform", `translate(${aimNow.x} ${aimNow.z})`);
    }
  }
}

function el(tag, attrs = {}) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

function floorMap() {
  const { x0, z0, w, d, v, outline } = bounds();
  const pad = 0.4;
  // turned: the view box holds the turned room; everything is drawn in
  // room metres inside one turned group (taps are read back through it)
  const deg = turnDeg(), rad = deg * Math.PI / 180, mx = x0 + w / 2, mz = z0 + d / 2;
  const cs = Math.abs(Math.cos(rad)), sn = Math.abs(Math.sin(rad));
  const vw = w * cs + d * sn, vd = w * sn + d * cs;
  const svg = el("svg", { viewBox: `${mx - vw / 2 - pad} ${mz - vd / 2 - pad} ${vw + pad * 2} ${vd + pad * 2}`, preserveAspectRatio: "xMidYMid meet" });
  const room = el("g", { transform: `rotate(${deg} ${mx} ${mz})`, class: "am-turned" });
  svg.append(room);
  const upright = (t, x, y) => { if (deg) t.setAttribute("transform", `rotate(${-deg} ${x} ${y})`); return t; };
  // the room as it is: its drawn outline, else its rectangle
  room.append(outline ? el("polygon", { points: outline.map((p) => `${p[0]},${p[1]}`).join(" "), class: "am-room" })
    : el("rect", { x: x0, y: z0, width: w, height: d, class: "am-room", rx: 0.2 }));
  const st = v.stage;
  if (st && st.width) room.append(el("rect", { x: (st.x || 0) - st.width / 2, y: st.z || 0, width: st.width, height: st.depth || 2, class: "am-stage" }));
  const o = orient();
  const bx = o.booth[0], bz = o.booth[2];
  const tip = (g, text) => { const t = el("title", {}); t.textContent = text; g.append(t); return g; };
  for (const zn of v.zones || []) {
    if (!(zn.points || []).length) continue;
    room.append(el("polygon", { points: zn.points.map((p) => p.join(",")).join(" "), class: "am-zone am-" + zn.kind }));
    const cx = zn.points.reduce((a, p) => a + p[0], 0) / zn.points.length;
    const cz = zn.points.reduce((a, p) => a + p[1], 0) / zn.points.length;
    // (the DJ marker names its own zone: two labels on one spot can't be read)
    if (Math.hypot(cx - bx, cz - bz) < 1.2) continue;
    const t = el("text", { x: cx, y: cz, class: "am-label" });
    t.textContent = zn.name || zn.kind;
    room.append(upright(t, cx, cz));
  }
  // what stands in the room (bar, DJ booth, speakers...), turned as placed;
  // marks as a small cross
  for (const o of v.objects || []) {
    if (typeof o.x !== "number" || typeof o.z !== "number" || o.kind === "screen" && !o.w) continue;
    const g = el("g", { transform: `translate(${o.x} ${o.z}) rotate(${+o.rot || 0})`, class: "am-obj am-" + o.kind });
    if (o.kind === "mark") {
      g.append(el("path", { d: "M-.25 0H.25M0 -.25V.25", class: "am-mark" }));
      tip(g, "A mark placed in the venue");
    } else {
      g.append(el("rect", { x: -(o.w || 1) / 2, y: -(o.d || 1) / 2, width: o.w || 1, height: o.d || 1, rx: 0.08 }));
    }
    room.append(g);
  }
  // the rigging at its real angle: a turned truss shows turned
  for (const r of v.rigging || []) {
    const [a, b] = [r.a || [0, 0, 0], r.b || [0, 0, 0]];
    if (Math.hypot(b[0] - a[0], b[2] - a[2]) < 0.05) {
      const sz = Math.max(0.3, +r.size || 0.3);
      room.append(el("rect", { x: a[0] - sz / 2, y: a[2] - sz / 2, width: sz, height: sz, class: "am-tower" }));
    } else {
      room.append(el("line", { x1: a[0], y1: a[2], x2: b[0], y2: b[2], class: "am-truss" }));
    }
  }
  // the lights, each body turned the way its base faces (with its truss)
  const sel = new Set(movers().map((x) => x.head_no));
  for (const hd of (state.snap && state.snap.patch) || []) {
    if (typeof hd.x !== "number" || typeof hd.z !== "number") continue;
    if (!(hd.map || []).includes("pan") && !(hd.map || []).includes("tilt")) continue;
    const on = sel.has(hd.head_no), s = on ? 0.32 : 0.22;
    const g = el("g", { transform: `translate(${hd.x} ${hd.z}) rotate(${-(+hd.yaw || 0)})`, class: on ? "am-head on" : "am-head" });
    g.append(el("rect", { x: -s, y: -s * 0.55, width: s * 2, height: s * 1.1, rx: s * 0.4 }));
    room.append(g);
  }
  // the DJ booth, the middle, and an arrow the way the DJ faces (starting
  // at the marker's edge, with a head, so it reads as "facing this way")
  const fr = o.front * Math.PI / 180, sx = Math.sin(fr), sz = Math.cos(fr);
  const dj = el("g", { class: "am-dj" });
  const ax = bx + sx * 1.5, az = bz + sz * 1.5;
  dj.append(el("line", { x1: bx + sx * 0.4, y1: bz + sz * 0.4, x2: ax, y2: az, class: "am-front" }),
    el("path", { d: `M${ax + sx * 0.3} ${az + sz * 0.3}L${ax - sz * 0.22} ${az + sx * 0.22}L${ax + sz * 0.22} ${az - sx * 0.22}Z`, class: "am-front-head" }),
    el("circle", { cx: bx, cy: bz, r: 0.35 }));
  tip(dj, "Where the DJ stands - the arrow is the way the DJ faces (change both with the ⋯ above the map)");
  const djt = el("text", { x: bx, y: bz + 0.12, class: "am-label am-dj-t" });
  djt.textContent = "DJ";
  dj.append(upright(djt, bx, bz));
  const mid = el("g", { class: "am-mid" });
  mid.append(el("path", { d: `M${o.centre[0] - 0.4} ${o.centre[1]}H${o.centre[0] + 0.4}M${o.centre[0]} ${o.centre[1] - 0.4}V${o.centre[1] + 0.4}` }),
    el("circle", { cx: o.centre[0], cy: o.centre[1], r: 0.25 }));
  tip(mid, "The middle of the room (⋯ above the map to move it)");
  room.append(dj, mid);
  const tg = el("g", { class: "aim-target" });
  tg.append(el("circle", { r: 0.5, class: "am-ring" }), el("circle", { r: 0.1, class: "am-dot" }));
  const now = el("g", { class: "aim-now" });
  now.append(el("circle", { r: 0.22, class: "am-now" }));
  room.append(tg, now);
  // drag: the lights follow
  let down = false;
  const where = (ev) => {
    const m = room.getScreenCTM();
    if (!m) return null;
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    return { x: Math.max(x0, Math.min(x0 + w, pt.x)), z: Math.max(z0, Math.min(z0 + d, pt.y)) };
  };
  const at = (ev, final) => {
    const p = where(ev);
    if (p) aimTo({ x: p.x, y: 0, z: p.z }, final);
  };
  svg.addEventListener("pointerdown", (ev) => {
    if (setting) {
      // Set the DJ booth / the middle / which way the DJ faces: this tap
      const p = where(ev);
      if (!p) return;
      const o2 = orient();
      const params = setting === "front" ? { front: Math.round((Math.atan2(p.x - o2.booth[0], p.z - o2.booth[2]) * 180 / Math.PI + 360) % 360) }
        : { [setting]: [+p.x.toFixed(2), +p.z.toFixed(2)] };
      setting = "";
      run("venue_orient", params, { toast: true });
      return;
    }
    if (!movers().length) { toast("Select moving lights first"); return; }
    down = true;
    svg.setPointerCapture(ev.pointerId);
    at(ev, false);
  });
  svg.addEventListener("pointermove", (ev) => { if (down) at(ev, false); });
  svg.addEventListener("pointerup", (ev) => { if (down) { down = false; at(ev, true); } });
  svg.addEventListener("pointercancel", () => { down = false; });
  return svg;
}

/** The Aim section: the map (Follow me and the map's own menu above it,
 *  the spot nudges under it) and, beside it, what the caller adds: the
 *  spots, formations and roam. */
export function aimBlock(...after) {
  const btn = h("button.btn.aim-follow" + (following ? ".on" : ""), {
    title: "Follow me: then press and drag on the 3D view - every selected moving light follows the pointer over the floor (right-drag still turns the view, Esc stops)",
    onclick: () => setFollow(!following),
  }, following ? "✋ Following - drag the 3D floor" : "✋ Follow me");
  const map = h("div.aim-map", { title: "The room as drawn in Arrange, trusses at their real angle. Drag on it: every selected light points at that spot, wherever its truss is turned. (The pan/tilt pad moves each light's own pan and tilt.)" });
  map.append(floorMap());
  const vb = map.querySelector("svg") && map.querySelector("svg").viewBox.baseVal;
  const mapAr = vb && vb.width && vb.height ? (vb.width / vb.height).toFixed(3) : "";
  // which way round the map is, and where the DJ / the middle / the front
  // are: setup, kept in the map's menu (they were three rows of chips)
  const mapMenu = (anchor) => menu(anchor, [
    ...[["audience", "Seen from the audience", "the stage at the top"], ["dj", "Seen from the DJ", "left on the map is the DJ's left"]]
      .map(([v, l, hint]) => ({ label: (mapTurn === v ? "✓ " : "") + l, hint, run: () => turnMap(v) })),
    { label: "Turn the map a quarter", run: () => turnMap(String((turnDeg() + 90) % 360)) },
    "-",
    ...[["booth", "Set where the DJ stands…", "then tap the map"], ["centre", "Set the middle of the room…", "then tap the map"],
      ["front", "Set the way the DJ faces…", "then tap towards the crowd"]].map(([k, l, hint]) => ({ label: l, hint, run: () => {
      setting = k;
      toast(`${l.replace("…", "")}: the next tap on the map sets it`, "", 3500);
    } })),
    ...((orient().set || []).length ? [{ label: "Back to what the desk works out", run: () => run("venue_orient", { clear: "all" }, { toast: true }) }] : []),
  ]);
  // the spot, moved as the DJ sees it: left / right / away / nearer
  const step = (fwd, right) => {
    if (!movers().length) { toast("Select moving lights first"); return; }
    const o = orient(), a = o.front * Math.PI / 180;
    const f = [Math.sin(a), Math.cos(a)], r = [-f[1], f[0]];
    const from = target || { x: o.centre[0], z: o.centre[1] };
    const { x0, z0, w, d } = bounds();
    const S = 0.75;
    aimTo({ x: Math.max(x0, Math.min(x0 + w, from.x + (f[0] * fwd + r[0] * right) * S)), y: 0,
      z: Math.max(z0, Math.min(z0 + d, from.z + (f[1] * fwd + r[1] * right) * S)) }, true);
  };
  const nudge = h("div.aim-spotpad", { title: "Move the spot as the DJ sees it" },
    h("span.muted.small", "Spot"),
    h("button.btn.small", { title: "The spot to the DJ's left", onclick: () => step(0, -1) }, "◀"),
    h("button.btn.small", { title: "The spot further from the DJ", onclick: () => step(1, 0) }, "▲"),
    h("button.btn.small", { title: "The spot nearer the DJ", onclick: () => step(-1, 0) }, "▼"),
    h("button.btn.small", { title: "The spot to the DJ's right", onclick: () => step(0, 1) }, "▶"),
    h("button.btn.small", { title: "The middle of the room", onclick: () => { const o = orient(); target = null; aimTo({ x: o.centre[0], y: 0, z: o.centre[1] }, true); } }, "Middle"));
  queueMicrotask(drawTarget);
  const heads = multiHead() ? h("div.mv-row", h("span.k", "Heads"),
    h("span.chip-row", ...[[0, "Together"], [0.8, "Fan out"], [2, "Wide fan"]].map(([v, l]) => h("button.chip" + (fan === v ? ".on" : ""), {
      title: v ? `Each head of a multi-head light aims at its own spot, ${v} m apart along the throw` : "Every head on the spot (they sit side by side on the bar) - or pick heads in the title row to move them alone",
      onclick: (e) => { fan = v; for (const c of e.currentTarget.parentNode.children) c.classList.toggle("on", c === e.currentTarget); },
    }, l)))) : null;
  const left = h("div.aim-left", h("div.aim-head", btn, h("button.btn.small.ghost.aim-mapmenu", { title: "The map: which way round; where the DJ, the middle and the front are", "aria-label": "Map options", onclick: (e) => mapMenu(e.currentTarget) }, "⋯")), map, nudge);
  if (mapAr) left.style.setProperty("--map-ar", mapAr);           // its width follows the room's shape
  return h("div.mv-sec.aim-sec", left, h("div.aim-ctl", ...after.filter(Boolean), heads));
}

function turnMap(v) {
  mapTurn = v;
  try { localStorage.setItem("haze.mapTurn", v); } catch (e) { /* private window */ }
  for (const box of document.querySelectorAll(".aim-map")) box.replaceChildren(floorMap());
  queueMicrotask(drawTarget);
  for (const c of document.querySelectorAll(".am-turn")) c.classList.toggle("on", c.dataset.turn === v);
}

/** The Follow speed picked (seconds; 0 = instant): the spot buttons use it too. */
export const followGlide = () => glide;

// the desk's glide, from the live feed: the dashed ring on the map
on("lite", (lite) => {
  const g = lite && lite.aim_glide;
  const was = aimNow;
  aimNow = g ? { x: g.x, z: g.z } : null;
  if (was || aimNow) drawTarget();
});
