// The light map: how much light lands where, in lux.
//
// A lighting designer's check before the gig: is the DJ lit enough for the
// stream, is the dance floor even, does the stage wash reach the front, how
// dark is the bar?  The 3D already works out every light's real output
// (lumens and lens, materials.js / stage.js), so the light on a surface is
// a measurement, not a guess: a light of I candela puts I / d² lux on a
// surface d metres away, times the cosine of the angle it lands at.
//
// The surfaces' shader (materials.js) paints the map - every point in false
// colour, LUX_STOPS - and this file holds the same sum on the computer's
// side (luxAt), for a reading where the pointer is and for the AI's
// measure_light: the rig's direct light, the beam's edge, its gobo, prism
// and split, the distance, the angle, and anything solid in the way.  Not
// counted: the house lights and light bounced off the room.

// The shader's light is in units of 3000 / 10.5 candela (stage.js: a light
// of `lm` lumens spread over its cone gives 10.5 * lm / 3000 / (pi tan^2)
// units) - this turns it back into candela, and so lux at a distance.
export const CD_PER_UNIT = 3000 / 10.5;

// The false colours, by lux: dark blue (no light) through green (a lit
// room) and yellow (a stage) to red and white (a TV studio, daylight).
// Between stops the colour runs smoothly, on a log scale of lux.
export const LUX_STOPS = [
  [0, "#0a0d2a"], [5, "#1c2f9a"], [20, "#1c74de"], [50, "#18b4d2"], [100, "#1fc47c"],
  [200, "#70d43a"], [500, "#e4dc2a"], [1000, "#ff9e1e"], [2000, "#ff4128"], [5000, "#ff40c8"],
];
export const LUX_TOP = 5000;

/** Where `lux` sits on the scale, 0..1 (a log scale, so 10 and 1000 are
 *  both easy to read). */
export function luxScale(lux) {
  return Math.max(0, Math.min(1, Math.log10(1 + Math.max(0, lux)) / Math.log10(1 + LUX_TOP)));
}

const srgbToLinear = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
const hexRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);

/** The stops as [position 0..1, linear r, g, b] (the shader's ramp). */
export function luxRamp() {
  return LUX_STOPS.map(([lux, hex]) => [luxScale(lux), ...hexRgb(hex).map(srgbToLinear)]);
}

/** The false colour for `lux` as the screen shows it, sRGB 0..255 - what
 *  the legend shows, and what a test reads back off the screen. */
export function luxColour(lux) {
  const t = luxScale(lux);
  const ramp = luxRamp();
  let c = ramp[0].slice(1);
  for (let i = 1; i < ramp.length; i++) {
    const [a] = ramp[i - 1], [b, ...col] = ramp[i];
    const k = Math.max(0, Math.min(1, (t - a) / (b - a)));
    c = c.map((v, j) => v + (col[j] - v) * k);
  }
  const toSrgb = (v) => (v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055);
  return c.map((v) => Math.round(255 * Math.max(0, Math.min(1, toSrgb(v)))));
}

/** A legend's CSS gradient, bottom (no light) to top. */
export function luxGradientCss() {
  return "linear-gradient(to top, " + LUX_STOPS.map(([lux, hex]) => `${hex} ${(100 * luxScale(lux)).toFixed(1)}%`).join(", ") + ")";
}

// ---------------------------------------------------------------------------
// The gobo, prism and split of a light, as the surfaces' shader works them
// out (materials.js goboShape / goboMask), here for one point.
// ---------------------------------------------------------------------------
const smooth = (a, b, x) => {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};
const fract = (x) => x - Math.floor(x);

let atlasCache = { version: -1, data: null, canvas: null };

/** How much of the light gets through gobo `id` at (u, v), -1..1 across
 *  the beam: 1 open, 0 dark.  `atlas`: the real gobo pictures ({canvas,
 *  version}). */
export function goboShape(id, u, v, atlas = null) {
  const r = Math.hypot(u, v), a = Math.atan2(v, u);
  if (id > 99.5) {
    if (!atlas || !atlas.canvas) return 1;
    if (atlasCache.version !== atlas.version || atlasCache.canvas !== atlas.canvas) {
      try {
        const c = atlas.canvas;
        atlasCache = { version: atlas.version, canvas: c, data: c.getContext("2d").getImageData(0, 0, c.width, c.height) };
      } catch (e) {
        atlasCache = { version: atlas.version, canvas: atlas.canvas, data: null };
      }
    }
    const d = atlasCache.data;
    if (!d) return 1;
    const n = id - 100, cell = d.width / 8;
    const cx = n % 8, cy = Math.floor(n / 8);
    const px = Math.max(0.004, Math.min(0.996, u * 0.5 + 0.5));
    const py = Math.max(0.004, Math.min(0.996, v * 0.5 + 0.5));
    const x = Math.min(d.width - 1, Math.floor((cx + px) * cell));
    const y = Math.min(d.height - 1, Math.floor((cy + 1 - py) * cell));
    return d.data[(y * d.width + x) * 4] / 255;
  }
  if (id < 0.5) return 1;
  if (id < 1.5) {                                        // dot ring
    let k = 0;
    for (let i = 0; i < 8; i++) {
      const t = i * 0.785398;
      k = Math.max(k, 1 - smooth(0.13, 0.17, Math.hypot(u - Math.cos(t) * 0.58, v - Math.sin(t) * 0.58)));
    }
    return Math.max(k, 1 - smooth(0.16, 0.2, r));
  }
  if (id < 2.5) {                                        // star
    const st = Math.pow(Math.abs(Math.cos(a * 2.5)), 6) * 0.45 + 0.35;
    return 1 - smooth(st - 0.04, st + 0.04, r);
  }
  if (id < 3.5) return smooth(0.35, 0.45, Math.abs(fract(u * 2.5) - 0.5) * 2);     // bars
  if (id < 4.5) return smooth(0.3, 0.5, Math.abs(fract(r * 3) - 0.5) * 2);         // rings
  if (id < 5.5) return smooth(0.2, 0.35, Math.abs(Math.sin(a * 3)) * (1 - r * 0.6)); // petals
  if (id < 6.5) {                                        // breakup
    const x = u * 3.2, y = v * 3.2;
    const nn = Math.sin(x * 1.7 + Math.sin(y * 2.3)) * Math.cos(y * 1.3 + Math.sin(x * 1.9));
    return smooth(-0.05, 0.25, nn);
  }
  return 1 - smooth(0.08, 0.12, Math.min(Math.abs(u), Math.abs(v)));               // cross
}

/** goboMask: the picture through the prism (copies round the centre, or in
 *  a row), softened out of focus, one half of a split beam. */
export function goboMask(id, u0, v0, blur, prism, prot, side, atlas = null) {
  let u = u0, v = v0, edge = 1;
  const n = Math.abs(prism);
  if (n > 1.5) {
    const cs = Math.cos(prot), sn = Math.sin(prot);
    const qx = cs * u + sn * v, qy = -sn * u + cs * v;
    if (prism < 0) {
      const sp = 1.6 / n;
      const k = Math.max(0, Math.min(n - 1, Math.floor(qx / sp + n * 0.5)));
      u = (qx - (k - (n - 1) * 0.5) * sp) / (sp * 0.62);
      v = qy / (sp * 0.62);
    } else {
      const step = 6.2831853 / n;
      const t = Math.floor(Math.atan2(qy, qx) / step + 0.5) * step;
      const rr = Math.min(0.53, Math.sin(3.14159265 / n) * 0.58);
      u = (qx - Math.cos(t) * 0.47) / rr;
      v = (qy - Math.sin(t) * 0.47) / rr;
    }
    edge = 1 - smooth(1 - blur * 0.3 - 0.1, 1.0, Math.hypot(u, v));
  }
  if (side > 0.5) edge *= smooth(-0.04, 0.04, side > 1.5 ? -u0 : u0);
  if (id > 0.5) {
    const g = goboShape(id, u, v, atlas);
    return (g + (0.55 - g) * Math.min(0.85, blur)) * edge;
  }
  return edge;
}

/**
 * The lux the rig puts on a point.  lights: the stage's light list (the
 * same the surfaces' shader reads: pos, dir, cosO, cosI, r, g, b, gobo,
 * rot, blur, prism, prot, side, head); p: the point; n: which way its
 * surface faces (a light meter's sensor); blocked(origin, dir, dist): the
 * distance to the first solid thing on the way (dist when nothing is).
 * With `why`, also each light's share: {lux, by: Map(head -> lux)}.
 */
export function luxAt(lights, p, n, { blocked = null, atlas = null, why = false } = {}) {
  let total = 0;
  const by = why ? new Map() : null;
  const dir = { x: 0, y: 0, z: 0 };
  for (const l of lights || []) {
    const Lx = p.x - l.pos.x, Ly = p.y - l.pos.y, Lz = p.z - l.pos.z;
    const along = Lx * l.dir.x + Ly * l.dir.y + Lz * l.dir.z;
    if (along <= 0) continue;                                    // behind the lens
    const d2 = Lx * Lx + Ly * Ly + Lz * Lz, d = Math.sqrt(d2);
    const c = along / Math.max(d, 1e-4);
    if (c <= l.cosO) continue;                                   // outside the beam
    let spot = smooth(l.cosO, l.cosI, c);
    const lam = Math.max(0, -(n.x * Lx + n.y * Ly + n.z * Lz) / Math.max(d, 1e-4));
    if (spot * lam <= 1e-5) continue;
    if ((l.gobo || 0) > 0.5 || Math.abs(l.prism || 0) > 0.5 || (l.side || 0) > 0.5) {
      // the point in the beam's own frame, -1..1 across it (as the shader)
      const dx = l.dir.x, dy = l.dir.y, dz = l.dir.z;
      let ux = 0, uy = 1, uz = 0;
      if (Math.abs(dy) >= 0.99) { ux = 1; uy = 0; }
      let rx = dy * uz - dz * uy, ry = dz * ux - dx * uz, rz = dx * uy - dy * ux;
      const rl = Math.hypot(rx, ry, rz) || 1;
      rx /= rl; ry /= rl; rz /= rl;
      const vx = ry * dz - rz * dy, vy = rz * dx - rx * dz, vz = rx * dy - ry * dx;
      const tanO = Math.sqrt(Math.max(1 - l.cosO * l.cosO, 1e-5)) / l.cosO;
      const k = Math.max(along * tanO, 1e-4);
      const u0 = (Lx * rx + Ly * ry + Lz * rz) / k, v0 = (Lx * vx + Ly * vy + Lz * vz) / k;
      const cs = Math.cos(l.rot || 0), sn = Math.sin(l.rot || 0);
      spot *= goboMask(l.gobo || 0, cs * u0 + sn * v0, -sn * u0 + cs * v0, l.blur || 0, l.prism || 0, l.prot || 0, l.side || 0, atlas);
      if (spot <= 1e-5) continue;
    }
    if (blocked) {
      dir.x = Lx / d; dir.y = Ly / d; dir.z = Lz / d;
      if (blocked(l.pos, dir, d) < d - 0.05) continue;           // something solid in the way
    }
    const lum = 0.2126 * l.r + 0.7152 * l.g + 0.0722 * l.b;
    const lux = lum * spot * lam / (d2 + 0.09) * CD_PER_UNIT;
    total += lux;
    if (by && lux > 0) by.set(l.head, (by.get(l.head) || 0) + lux);
  }
  return why ? { lux: total, by } : total;
}

/** A reading said the way a light meter would: "0", "8", "140", "1,200". */
export function luxText(lux) {
  const v = Math.max(0, lux);
  if (v < 0.5) return "0";
  if (v < 10) return v.toFixed(1).replace(/\.0$/, "");
  const r = v < 1000 ? Math.round(v) : Math.round(v / 10) * 10;
  return r.toLocaleString("en-GB");
}
