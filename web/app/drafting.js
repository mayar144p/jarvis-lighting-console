// Drawing doors, pillars and balconies on the plan (Arrange -> Draw).
// Pure: points in metres in, venue objects out.  Walls are the room's
// outline as [[x, z], ...] (outlineOf in /js/stage/venue.js).

const r2 = (v) => Math.round(v * 100) / 100;

/** The wall nearest (x, z): {i, t (0..1 along it), d (metres away), a, b}. */
export function nearestWall(walls, x, z) {
  let best = null;
  walls.forEach((a, i) => {
    const b = walls[(i + 1) % walls.length];
    const dx = b[0] - a[0], dz = b[1] - a[1];
    const L2 = dx * dx + dz * dz;
    if (L2 < 1e-9) return;
    const t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (z - a[1]) * dz) / L2));
    const d = Math.hypot(a[0] + dx * t - x, a[1] + dz * t - z);
    if (!best || d < best.d) best = { i, t, d, a, b };
  });
  return best;
}

/** A door in the wall nearest the two clicks, as wide as they are apart
 *  along it (at least 0.7 m), kept inside that wall. */
export function doorFrom(p1, p2, walls) {
  const mid = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
  const w = nearestWall(walls, mid[0], mid[1]);
  if (!w) return null;
  const dx = w.b[0] - w.a[0], dz = w.b[1] - w.a[1];
  const L = Math.hypot(dx, dz);
  const along = (p) => ((p[0] - w.a[0]) * dx + (p[1] - w.a[1]) * dz) / L;
  let s0 = along(p1), s1 = along(p2);
  if (s0 > s1) [s0, s1] = [s1, s0];
  let width = Math.min(L, Math.max(0.7, s1 - s0));
  let c = Math.max(width / 2, Math.min(L - width / 2, (s0 + s1) / 2));
  width = r2(width);
  // a little inside the wall, so it shows on the inside face
  const nx = -dz / L, nz = dx / L;
  const inward = (mx, mz) => {
    const test = [mx + nx * 0.2, mz + nz * 0.2];
    return pointIn(test, walls) ? 1 : -1;
  };
  const cx = w.a[0] + dx / L * c, cz = w.a[1] + dz / L * c;
  const k = inward(cx, cz) * 0.06;
  let rot = Math.round(Math.atan2(dz, dx) * 180 / Math.PI);
  if (rot > 90) rot -= 180;
  if (rot <= -90) rot += 180;
  return { x: r2(cx + nx * k), z: r2(cz + nz * k), w: width, d: 0.12, h: 2.2, rot };
}

/** A balcony: the rectangle between two corners, its deck high enough to
 *  walk under (2.4 m clear), at most 3.2 m. */
export function balconyFrom(p1, p2, ceiling) {
  const w = Math.abs(p2[0] - p1[0]), d = Math.abs(p2[1] - p1[1]);
  if (w < 0.5 || d < 0.5) return null;
  const y = r2(Math.max(2.4, Math.min(3.2, (ceiling || 5) - 2.4)));
  return { x: r2((p1[0] + p2[0]) / 2), z: r2((p1[1] + p2[1]) / 2), w: r2(w), d: r2(d), h: 0.3, y };
}

export function pillarAt(p, ceiling) {
  return { x: r2(p[0]), z: r2(p[1]), w: 0.5, d: 0.5, h: r2(ceiling || 5) };
}

export function pointIn(p, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if ((zi > p[1]) !== (zj > p[1]) && p[0] < (xj - xi) * (p[1] - zi) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}
