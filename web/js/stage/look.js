// The maths behind a light's look in the 3D view: reading the light feed,
// blending looks, strobe flashes and how fast a motor gets where it's sent.
// Pure: no scene, no DOM.
import * as THREE from "three";

export const DEG = Math.PI / 180;
export const UP = new THREE.Vector3(0, 1, 0);
export const X_AXIS = new THREE.Vector3(1, 0, 0);
export const DEFAULT_PAN = [-270, 270];
export const DEFAULT_TILT = [-135, 135];

export function smooth(t) { return t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t); }
export function lerp(a, b, t) { return a + (b - a) * t; }

export function hexLinear(hex, out) {
  out.set(hex || "#ffffff");            // Color.set converts sRGB -> linear
  return out;
}

export const EMPTY_LOOK = { a: 0, r: 1, g: 1, b: 1, pan: null, tilt: null, beam: {}, look: {}, hz: 0, mv: null, fx: null };

export function lookFrom(row, scratch) {
  if (!row) return { ...EMPTY_LOOK, beam: {}, look: {} };
  const c = hexLinear(row.hex, scratch);
  return {
    a: +row.a || 0, r: c.r, g: c.g, b: c.b,
    pan: typeof row.pan === "number" ? row.pan : null,
    tilt: typeof row.tilt === "number" ? row.tilt : null,
    deg: row.deg || null,
    beam: row.beam || {},
    hz: +row.hz || 0,            // the real strobe rate, 0 = steady
    mv: row.mv || null,          // {p, t: full-travel seconds, s: speed 0..1}
    fx: row.fx || null,          // an effect firing: {fire, fog, laser, pattern...}
    prog: row.prog || null,      // running a program of its own ("Program 3")
    spin: row.spin || null,      // endless pan / tilt rotation: {pan: ±speed}
    // what the beam is doing, in the fixture file's words (app/beamlook.py):
    // prism facets and turn, gobo turn / shake / scroll, split colours,
    // the colour wheel turning, the strobe's kind
    look: row.look || {},
    // each head of a multi-head light: its own colour and aim
    cells: Array.isArray(row.cells) ? row.cells.map((c) => {
      const cc = hexLinear(c.hex, scratch);
      return { r: cc.r, g: cc.g, b: cc.b, tilt: typeof c.tilt === "number" ? c.tilt : null,
        pan: typeof c.pan === "number" ? c.pan : null };
    }) : null,
  };
}

export function mixLook(a, b, t) {
  // Only what the NEW look has: a value the engine stopped sending (a
  // strobe that was released) must go, not linger from the old look.
  const beam = {};
  for (const k of Object.keys(b.beam)) {
    const x = a.beam[k], y = b.beam[k];
    beam[k] = x === undefined ? y : lerp(x, y, t);
  }
  // pan / tilt go straight to the target: the motor model (_drive) gives
  // the travel its real speed.  Easing them as well restarted from a
  // standstill on every look update, so a head following a moving target
  // (roam, the floor map, an XY pad) hardly moved at all.
  const ang = (x, y) => (y === null ? x : y);
  return {
    a: lerp(a.a, b.a, t), r: lerp(a.r, b.r, t), g: lerp(a.g, b.g, t),
    b: lerp(a.b, b.b, t), pan: ang(a.pan, b.pan), tilt: ang(a.tilt, b.tilt),
    deg: b.deg || a.deg, beam, look: b.look, hz: b.hz, mv: b.mv || a.mv, fx: b.fx, prog: b.prog, spin: b.spin,
    cells: b.cells ? b.cells.map((c, i) => {
      const o = (a.cells && a.cells[i]) || c;
      return { r: lerp(o.r, c.r, t), g: lerp(o.g, c.g, t), b: lerp(o.b, c.b, t),
        tilt: c.tilt, pan: c.pan };
    }) : null,
  };
}

// Seconds for a FULL pan / tilt at top speed, by type, until a fixture
// model is calibrated against the real light (Calibrate movement speed).
export const TRAVEL = {
  moving_beam: [2.2, 1.3], moving_spot: [3.0, 1.8], moving_wash: [3.2, 1.9],
  moving_hybrid: [2.8, 1.7], moving_bar: [3.0, 1.5],
  scanner: [0.35, 0.3],                      // a mirror, not a head: very quick
};

/**
 * One axis of a moving head's motor: accelerates, cruises at the
 * fixture's top speed (slowed by its speed channel), and brakes into the
 * target - instead of snapping there in a tenth of a second.  Positions
 * are the look's 0..1 across the axis's travel.
 */
/** How much light a strobing lamp lets out right now (1 = steady): a
 *  plain strobe flashes, a pulse breathes, a ramp opens or closes, a
 *  random strobe flashes when its dice say so (each light its own). */
export function flash(hz, mode, time, seed) {
  if (!(hz > 0)) return 1;
  const t = time * hz;
  const f = t - Math.floor(t);
  switch (mode) {
    case "pulse": return 0.5 - 0.5 * Math.cos(f * 2 * Math.PI);
    case "ramp_up": return f;
    case "ramp_down": return 1 - f;
    case "random": {
      const k = Math.sin((Math.floor(t) + seed * 7.13) * 12.9898) * 43758.5453;
      return k - Math.floor(k) > 0.55 && f < 0.3 ? 1 : 0;
    }
    default: return f > 0.3 ? 0 : 1;
  }
}

/** An angle for a turning part: indexed {at: degrees}, spinning {spin:
 *  turns a second}, or the plain reading when the file said nothing. */
export function turnOf(t, time, plain) {
  if (!t) return plain;
  if (t.at !== undefined) return t.at * DEG;
  return time * (t.spin || 0) * 2 * Math.PI;
}

export function motorStep(m, target, full, speed, dt) {
  if (target === null) { m.v = 0; return m.x; }          // not driven: hold
  if (m.x === null) { m.x = target; m.v = 0; return m.x; } // first sight
  const slow = 1 + 14 * Math.pow(Math.max(0, Math.min(1, speed || 0)), 1.6);
  // A full sweep takes `total` seconds INCLUDING speeding up and braking
  // (that is what a stopwatch measures): travel/vmax + ramp = total.
  const total = Math.max(0.2, full * slow);
  const ramp = Math.min(0.35, 0.12 + total * 0.08, total * 0.45);
  const vmax = 1 / (total - ramp);
  const acc = vmax / ramp;
  const dist = target - m.x;
  if (Math.abs(dist) < 1e-4 && Math.abs(m.v) < vmax * 0.02) { m.x = target; m.v = 0; return m.x; }
  const want = Math.sign(dist) * Math.min(vmax, Math.sqrt(2 * acc * Math.abs(dist)));
  const dv = want - m.v;
  m.v += Math.sign(dv) * Math.min(Math.abs(dv), acc * dt);
  const step = m.v * dt;
  m.x = Math.abs(step) >= Math.abs(dist) ? target : m.x + step;
  if (m.x === target) m.v = 0;
  return m.x;
}
