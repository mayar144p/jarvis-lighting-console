// The room's grid of lights, filled once a frame.
//
// Every surface used to loop over the 32 brightest lights (similar beams
// merged first), so on a big rig most pools simply weren't there: 121 lit
// lights, 32 on the floor.  Now each light goes into every cell of a grid
// over the floor plan that its cone can reach (a cone, from the lens out
// to as far as it goes, covers a box of the plan), and a point on a
// surface - floor, wall, truss or person - looks only at its own cell's
// lights.  Hundreds of lights land, and a pixel still only works on the
// few that can reach it.
import { GRID, GRID_W, INDEX_W, INDEX_H, MAX_LIGHTS, LIGHTS } from "./materials.js";

/** The box of the floor plan a light's cone can reach: from the lens out to
 *  `reach` metres, as wide as it has spread by then. */
function footprint(l) {
  const c = Math.min(0.99999, Math.max(1e-4, l.cosO));
  const k = Math.sqrt(1 - c * c) / c;
  const L = Math.max(0.5, l.reach || 30);
  const R = L * k + 0.2;
  const fx = l.pos.x + l.dir.x * L, fz = l.pos.z + l.dir.z * L;
  const ex = R * Math.sqrt(Math.max(0, 1 - l.dir.x * l.dir.x));
  const ez = R * Math.sqrt(Math.max(0, 1 - l.dir.z * l.dir.z));
  return {
    x0: Math.min(l.pos.x - 0.3, fx - ex), x1: Math.max(l.pos.x + 0.3, fx + ex),
    z0: Math.min(l.pos.z - 0.3, fz - ez), z1: Math.max(l.pos.z + 0.3, fz + ez),
  };
}

export class LightGrid {
  constructor() {
    this.lists = Array.from({ length: GRID_W * GRID_W }, () => []);
    this.stats = { lights: 0, cells: 0, entries: 0, dropped: 0, cell: 1 };
  }

  /**
   * lights: brightest first, each {pos, dir, cosO, cosI, r, g, b, gobo, rot,
   * blur, prism, prot, side, slot, reach}.  room: the room box (x0, x1, z0,
   * z1).  perCell: the most lights one patch of floor takes (the brightest).
   */
  update(lights, room, perCell = 48) {
    const n = Math.min(MAX_LIGHTS, lights.length);
    // the grid covers the room and wherever the lights reach (not miles out)
    let x0 = room.x0 - 1, x1 = room.x1 + 1, z0 = room.z0 - 1, z1 = room.z1 + 1;
    const boxes = new Array(n);
    for (let i = 0; i < n; i++) {
      const b = footprint(lights[i]);
      boxes[i] = b;
      x0 = Math.min(x0, Math.max(b.x0, room.x0 - 30));
      x1 = Math.max(x1, Math.min(b.x1, room.x1 + 30));
      z0 = Math.min(z0, Math.max(b.z0, room.z0 - 30));
      z1 = Math.max(z1, Math.min(b.z1, room.z1 + 30));
    }
    const size = Math.max(0.25, Math.max(x1 - x0, z1 - z0) / GRID_W);
    const gx = Math.max(1, Math.min(GRID_W, Math.ceil((x1 - x0) / size)));
    const gz = Math.max(1, Math.min(GRID_W, Math.ceil((z1 - z0) / size)));
    const lists = this.lists;
    for (let c = 0; c < gx * gz; c++) lists[c].length = 0;
    for (let i = 0; i < n; i++) {
      const b = boxes[i];
      const ia = Math.max(0, Math.floor((b.x0 - x0) / size)), ib = Math.min(gx - 1, Math.floor((b.x1 - x0) / size));
      const ja = Math.max(0, Math.floor((b.z0 - z0) / size)), jb = Math.min(gz - 1, Math.floor((b.z1 - z0) / size));
      for (let j = ja; j <= jb; j++) for (let ii = ia; ii <= ib; ii++) lists[j * gx + ii].push(i);
    }
    // the lists, one after another; each cell's first `perCell` (the
    // lights come brightest first, so a crowded cell keeps the brightest)
    const cells = GRID.cells.image.data, index = GRID.index.image.data;
    const cap = INDEX_W * INDEX_H;
    let k = 0, dropped = 0, used = 0;
    for (let j = 0; j < gz; j++) {
      for (let ii = 0; ii < gx; ii++) {
        const list = lists[j * gx + ii];
        const m = Math.min(list.length, perCell, cap - k);
        dropped += list.length - m;
        const o = (j * GRID_W + ii) * 4;
        cells[o] = k;
        cells[o + 1] = m;
        if (m) used++;
        for (let q = 0; q < m; q++) index[k++] = list[q];
      }
    }
    // the lights themselves: five texels a row
    const L = GRID.lights.image.data;
    for (let i = 0; i < n; i++) {
      const l = lights[i], o = i * 20;
      L[o] = l.pos.x; L[o + 1] = l.pos.y; L[o + 2] = l.pos.z; L[o + 3] = l.cosO;
      L[o + 4] = l.dir.x; L[o + 5] = l.dir.y; L[o + 6] = l.dir.z; L[o + 7] = l.cosI;
      L[o + 8] = l.r; L[o + 9] = l.g; L[o + 10] = l.b; L[o + 11] = l.gobo || 0;
      L[o + 12] = l.rot || 0; L[o + 13] = l.blur || 0; L[o + 14] = l.prism || 0; L[o + 15] = l.prot || 0;
      L[o + 16] = l.side || 0; L[o + 17] = l.slot === undefined || l.slot === null ? -1 : l.slot; L[o + 18] = 0; L[o + 19] = 0;
    }
    GRID.lights.needsUpdate = true;
    GRID.cells.needsUpdate = true;
    GRID.index.needsUpdate = true;
    GRID.origin.value.set(x0, z0);
    GRID.inv.value = 1 / size;
    GRID.dims.value.set(gx, gz);
    LIGHTS.uCount.value = n;
    this.stats = { lights: n, cells: used, entries: k, dropped, cell: size };
  }
}
