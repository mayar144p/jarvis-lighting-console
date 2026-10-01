// Aim in the Move tab: point the selected movers by where you are
// pointing.  "Follow me" - press and drag on the 3D view and the lights
// follow the pointer over the floor (a ring marks the spot); the floor map
// does the same from above, when the 3D angle is awkward.
// Both send aim_at: each light solves its own pan/tilt from where it hangs.
import { run } from "./actions.js";
import { state, selectionHeads, on } from "./store.js";
import { h, toast } from "./ui.js";

const SVGNS = "http://www.w3.org/2000/svg";
let following = false;
let target = null;                      // {x, z}: the last spot aimed at
let pending = null, timer = 0, last = 0;
let fan = 0;                            // multi-head lights: metres between their heads' spots
const RATE_MS = 70;
// how fast the beams follow the pointer: 0 = straight there, else the
// seconds they take to cover most of the way (they glide after it)
const GLIDES = [[0, "Instant"], [0.25, "Fast"], [0.8, "Medium"], [2, "Slow"]];
let glide = (() => { try { return +(localStorage.getItem("jarvis.followGlide") || 0); } catch (e) { return 0; } })();
if (!GLIDES.some(([v]) => v === glide)) glide = 0;
let aimNow = null;                      // {x, z}: where the desk has the beams while they glide

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
  const m = await import("./stagepanel.js");
  return m.getStage();
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
    b.textContent = following ? "✋ Following - drag on the 3D floor" : "✋ Follow me on the 3D floor";
  }
}

// ------------------------------------------------------------ floor map
function bounds() {
  const v = (state.snap && state.snap.venue) || {};
  const r = v.room || { width: 12, depth: 14, back: -1, cx: 0 };
  const x0 = (r.cx || 0) - r.width / 2, z0 = r.back ?? -1;
  return { x0, z0, w: r.width, d: r.depth, v };
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
  const { x0, z0, w, d, v } = bounds();
  const pad = 0.4;
  const svg = el("svg", { viewBox: `${x0 - pad} ${z0 - pad} ${w + pad * 2} ${d + pad * 2}`, preserveAspectRatio: "xMidYMid meet" });
  svg.append(el("rect", { x: x0, y: z0, width: w, height: d, class: "am-room", rx: 0.2 }));
  const st = v.stage;
  if (st && st.width) svg.append(el("rect", { x: (st.x || 0) - st.width / 2, y: st.z || 0, width: st.width, height: st.depth || 2, class: "am-stage" }));
  for (const zn of v.zones || []) {
    if (!(zn.points || []).length) continue;
    svg.append(el("polygon", { points: zn.points.map((p) => p.join(",")).join(" "), class: "am-zone am-" + zn.kind }));
    const cx = zn.points.reduce((a, p) => a + p[0], 0) / zn.points.length;
    const cz = zn.points.reduce((a, p) => a + p[1], 0) / zn.points.length;
    const t = el("text", { x: cx, y: cz, class: "am-label" });
    t.textContent = zn.name || zn.kind;
    svg.append(t);
  }
  const sel = new Set(movers().map((x) => x.head_no));
  for (const hd of (state.snap && state.snap.patch) || []) {
    if (typeof hd.x !== "number" || typeof hd.z !== "number") continue;
    if (!(hd.map || []).includes("pan") && !(hd.map || []).includes("tilt")) continue;
    svg.append(el("circle", { cx: hd.x, cy: hd.z, r: sel.has(hd.head_no) ? 0.28 : 0.18, class: sel.has(hd.head_no) ? "am-head on" : "am-head" }));
  }
  const tg = el("g", { class: "aim-target" });
  tg.append(el("circle", { r: 0.5, class: "am-ring" }), el("circle", { r: 0.1, class: "am-dot" }));
  const now = el("g", { class: "aim-now" });
  now.append(el("circle", { r: 0.22, class: "am-now" }));
  svg.append(tg, now);
  // drag: the lights follow
  let down = false;
  const at = (ev, final) => {
    const m = svg.getScreenCTM();
    if (!m) return;
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    const x = Math.max(x0, Math.min(x0 + w, pt.x)), z = Math.max(z0, Math.min(z0 + d, pt.y));
    aimTo({ x, y: 0, z }, final);
  };
  svg.addEventListener("pointerdown", (ev) => {
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

/** The Aim section: follow toggle + floor map (+ the spots, from the caller). */
export function aimBlock(...after) {
  const btn = h("button.btn.aim-follow" + (following ? ".on" : ""), {
    title: "Then press and drag on the 3D view: every selected moving light follows the pointer over the floor (right-drag still turns the view, Esc stops)",
    onclick: () => setFollow(!following),
  }, following ? "✋ Following - drag on the 3D floor" : "✋ Follow me on the 3D floor");
  const map = h("div.aim-map", { title: "Drag on the floor plan: the selected lights follow (stage at the top)" });
  map.append(floorMap());
  queueMicrotask(drawTarget);
  const speed = h("div.mv-row", h("span.k", "Follow speed"),
    h("span.chip-row", ...GLIDES.map(([v, l]) => h("button.chip" + (glide === v ? ".on" : ""), {
      title: v ? `The beams glide after the pointer (about ${v} s to catch up), and carry on to where you let go` : "The beams go straight to the pointer, as fast as the lights can move",
      onclick: (e) => {
        glide = v;
        try { localStorage.setItem("jarvis.followGlide", String(v)); } catch (err) { /* private window */ }
        for (const c of e.currentTarget.parentNode.children) c.classList.toggle("on", c === e.currentTarget);
      },
    }, l))));
  const heads = multiHead() ? h("div.mv-row", h("span.k", "Heads"),
    h("span.chip-row", ...[[0, "Together"], [0.8, "Fan out"], [2, "Wide fan"]].map(([v, l]) => h("button.chip" + (fan === v ? ".on" : ""), {
      title: v ? `Each head of a multi-head light aims at its own spot, ${v} m apart along the throw` : "Every head on the spot (they sit side by side on the bar)",
      onclick: (e) => { fan = v; for (const c of e.currentTarget.parentNode.children) c.classList.toggle("on", c === e.currentTarget); },
    }, l))), h("span.muted.small", "or pick heads (Heads: 1 2 3 4) to move them alone")) : null;
  return h("div.mv-sec.aim-sec", h("h3", "Aim"), btn, map, speed, heads, ...after.filter(Boolean));
}

export const isFollowing = () => following;

// the desk's glide, from the live feed: the dashed ring on the map
on("lite", (lite) => {
  const g = lite && lite.aim_glide;
  const was = aimNow;
  aimNow = g ? { x: g.x, z: g.z } : null;
  if (was || aimNow) drawTarget();
});
